"""
Stage 0b — Interactive Script Stripper (git add -p style)

Reviews a raw Markdown script in logical hunks (headings, dialogue turns, stage
directions, songs, front matter) and lets you interactively keep, cut, split,
edit in $EDITOR, skip/cut whole scenes, undo, and resume across sessions.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile


# ANSI color helpers (automatically disabled when stdout is not a TTY or --no-color)
class Colors:
    def __init__(self, enabled=True):
        self.enabled = enabled

    def wrap(self, code, text):
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text): return self.wrap("1", text)
    def dim(self, text): return self.wrap("2", text)
    def cyan(self, text): return self.wrap("36", text)
    def green(self, text): return self.wrap("32", text)
    def yellow(self, text): return self.wrap("33", text)
    def red(self, text): return self.wrap("31", text)
    def magenta(self, text): return self.wrap("35", text)


HEADING_RE = re.compile(r"^\s*#+\s+(.+?)\s*$")
ACT_RE = re.compile(r"\bact\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE)
SCENE_RE = re.compile(r"\bscene\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE)
SPEAKER_LINE_RE = re.compile(
    r"^\s*(?:\*\*[^*]{1,48}\*\*\s*:?|[A-Z][A-Z0-9 .'\-/&]{1,36}:)"
)
STAGE_DIR_RE = re.compile(r"^\s*(?:\*[^*].*\*\s*$|\(.*\)\s*$|\[.*\]\s*$)")
SONG_MARKER_RE = re.compile(r"^\s*(?:>|\[(?:song|music|singing|chorus)\b|\((?:singing|music|song)\b)", re.IGNORECASE)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_unit(unit_text, in_front_matter):
    """Classifies an atomic Markdown block for clearer hunk headers."""
    lines = [l for l in unit_text.splitlines() if l.strip()]
    if not lines:
        return "text"
    if HEADING_RE.match(lines[0]):
        return "heading"
    if in_front_matter:
        return "front_matter"
    if any(SONG_MARKER_RE.match(l) for l in lines):
        return "song"
    if any(SPEAKER_LINE_RE.match(l) for l in lines):
        return "dialogue"
    if all(STAGE_DIR_RE.match(l) for l in lines):
        return "stage_direction"
    return "text"


def split_into_atomic_units(raw_text):
    """
    Splits raw Markdown into atomic logical units:
      - Headings are always standalone units.
      - Blank-line separated paragraphs are split; within a dense block without blank
        lines, new speaker cues start a fresh atomic unit so 's' (split) works cleanly.
    """
    units = []
    buf = []

    def flush():
        if buf:
            text = "\n".join(buf).strip("\n")
            if text.strip():
                units.append(text)
            buf.clear()

    for line in raw_text.splitlines():
        if not line.strip():
            flush()
            continue

        if HEADING_RE.match(line):
            flush()
            units.append(line.strip())
            continue

        if buf and SPEAKER_LINE_RE.match(line):
            flush()

        buf.append(line)

    flush()
    return units


def segment_markdown_into_hunks(raw_text, max_lines=20):
    """
    Groups atomic Markdown units into reviewable hunks tagged with section & kind.
    Headings always stand alone so scene boundaries stay crisp.
    """
    units = split_into_atomic_units(raw_text)

    hunks = []
    current_act = None
    current_scene = None
    in_front_matter = True

    cur_units = []
    cur_lines = 0
    cur_section = "Front Matter"
    cur_kind = None

    def section_label():
        if in_front_matter:
            return "Front Matter"
        parts = []
        if current_act:
            parts.append(current_act)
        if current_scene:
            parts.append(current_scene)
        return " - ".join(parts) if parts else "Script"

    def flush_hunk():
        nonlocal cur_units, cur_lines, cur_kind
        if not cur_units:
            return
        hunks.append({
            "section": cur_section,
            "kind": cur_kind or "text",
            "units": list(cur_units),
            "text": "\n\n".join(cur_units),
            "decision": None,
        })
        cur_units = []
        cur_lines = 0
        cur_kind = None

    for unit in units:
        heading_m = HEADING_RE.match(unit)
        if heading_m:
            flush_hunk()
            heading_body = heading_m.group(1).strip()
            act_m = ACT_RE.search(heading_body)
            scene_m = SCENE_RE.search(heading_body)
            if act_m:
                in_front_matter = False
                current_act = f"Act {act_m.group(1)}"
                if not scene_m:
                    current_scene = None
            if scene_m:
                in_front_matter = False
                current_scene = f"Scene {scene_m.group(1)}"
            if re.search(r"\b(prologue|epilogue)\b", heading_body, re.IGNORECASE):
                in_front_matter = False
                current_scene = heading_body

            cur_section = section_label()
            hunks.append({
                "section": cur_section,
                "kind": "heading",
                "units": [unit],
                "text": unit,
                "decision": None,
            })
            continue

        unit_kind = classify_unit(unit, in_front_matter)
        unit_line_count = len(unit.splitlines())

        # Start a new hunk if section changed, kind transitioned significantly, or size limit reached
        kind_changed = (
            cur_kind is not None
            and cur_kind != unit_kind
            and ("stage_direction" in (cur_kind, unit_kind) or "song" in (cur_kind, unit_kind))
        )
        would_exceed = cur_units and (cur_lines + unit_line_count + 1 > max_lines)

        if cur_section != section_label() or kind_changed or would_exceed:
            flush_hunk()
            cur_section = section_label()

        cur_units.append(unit)
        cur_lines += unit_line_count + (1 if len(cur_units) > 1 else 0)
        if cur_kind is None:
            cur_kind = unit_kind

    flush_hunk()
    return hunks


def split_hunk(hunk):
    """
    Splits a single hunk into smaller hunks:
      1. If it contains multiple atomic units, split into 1 hunk per unit.
      2. Otherwise, if it has multiple lines, split in half (or per-line if <= 3 lines).
      3. Returns None if the hunk cannot be split further.
    """
    units = hunk.get("units") or [hunk["text"]]
    if len(units) > 1:
        sub_hunks = []
        in_fm = hunk["section"] == "Front Matter"
        for u in units:
            sub_hunks.append({
                "section": hunk["section"],
                "kind": classify_unit(u, in_fm),
                "units": [u],
                "text": u,
                "decision": None,
            })
        return sub_hunks

    lines = hunk["text"].splitlines()
    if len(lines) <= 1:
        return None

    if len(lines) <= 3:
        chunks = [[l] for l in lines if l.strip()]
    else:
        mid = len(lines) // 2
        chunks = [lines[:mid], lines[mid:]]

    chunks = ["\n".join(c).strip("\n") for c in chunks if any(l.strip() for l in c)]
    if len(chunks) <= 1:
        return None

    in_fm = hunk["section"] == "Front Matter"
    return [
        {
            "section": hunk["section"],
            "kind": classify_unit(c, in_fm),
            "units": [c],
            "text": c,
            "decision": None,
        }
        for c in chunks
    ]


def edit_hunk_in_editor(text):
    """Opens the hunk text in $VISUAL / $EDITOR and returns the edited text."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".md", prefix="lav_hunk_", encoding="utf-8", delete=False
    ) as tf:
        tf.write(text)
        tf.write("\n")
        temp_path = tf.name

    try:
        subprocess.run([editor, temp_path], check=True)
        with open(temp_path, "r", encoding="utf-8") as f:
            edited = f.read().strip("\n")
        return edited
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def render_kept_markdown(hunks):
    """Assembles all kept hunks into a clean Markdown script."""
    kept = [h["text"].strip("\n") for h in hunks if h["decision"] == "keep" and h["text"].strip()]
    if not kept:
        return ""
    output = "\n\n".join(kept)
    output = re.sub(r"\n{3,}", "\n\n", output)
    return output.strip() + "\n"


def save_state(state_path, source_path, source_hash, hunks):
    parent = os.path.dirname(os.path.abspath(state_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {
        "source_path": os.path.abspath(source_path),
        "source_sha256": source_hash,
        "hunks": hunks,
    }
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_state(state_path):
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_help(c):
    print(c.bold("\nCommands:"))
    print(f"  {c.green('y')}  - keep this hunk")
    print(f"  {c.red('n')}  - cut (drop) this hunk")
    print(f"  {c.yellow('s')}  - split this hunk into smaller hunks")
    print(f"  {c.cyan('e')}  - manually edit this hunk in $EDITOR (marks as keep)")
    print(f"  {c.green('a')}  - keep this hunk and ALL remaining undecided hunks in the current section/scene")
    print(f"  {c.red('d')}  - cut this hunk and ALL remaining undecided hunks in the current section/scene")
    print(f"  {c.magenta('u')}  - undo the last action")
    print(f"  {c.cyan('/')}  - search forward for a regex (e.g. '/Scrooge' or '/' then pattern)")
    print(f"  {c.yellow('q')}  - save progress to state file and quit (resume later)")
    print(f"  {c.dim('?')}  - print this help message\n")


def display_hunk(hunk, idx, total, pending_count, c):
    lines = hunk["text"].splitlines()
    kind_color = {
        "heading": c.magenta,
        "dialogue": c.green,
        "stage_direction": c.yellow,
        "song": c.cyan,
        "front_matter": c.red,
    }.get(hunk["kind"], c.dim)

    bar = "=" * 68
    print("\n" + c.dim(bar))
    header = (
        f"Hunk {idx + 1}/{total} ({pending_count} remaining) | "
        f"{hunk['section']} | {kind_color(hunk['kind'])} | {len(lines)} line(s)"
    )
    print(c.bold(header))
    print(c.dim(bar))

    for l_num, line in enumerate(lines, start=1):
        prefix = c.dim(f"{l_num:3d} | ")
        if HEADING_RE.match(line):
            print(prefix + c.magenta(c.bold(line)))
        elif STAGE_DIR_RE.match(line):
            print(prefix + c.yellow(line))
        else:
            print(prefix + line)


def next_pending_index(hunks, start_idx=0):
    """Finds the next hunk index with decision == None starting from start_idx (wrapping around)."""
    n = len(hunks)
    for offset in range(n):
        idx = (start_idx + offset) % n
        if hunks[idx]["decision"] is None:
            return idx
    return None


def interactive_strip(
    input_path,
    output_path,
    max_lines=20,
    state_path=None,
    reset=False,
    re_review=False,
    drop_frontmatter=False,
    no_color=False,
    input_fn=input,
):
    c = Colors(enabled=(not no_color and sys.stdout.isatty()))

    with open(input_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    source_hash = sha256_text(raw_text)

    state_file = state_path or (output_path + ".strip_state.json")

    hunks = None
    if not reset and os.path.exists(state_file):
        saved = load_state(state_file)
        if saved.get("source_sha256") == source_hash:
            hunks = saved.get("hunks", [])
            if re_review:
                for h in hunks:
                    h["decision"] = None
                print(c.cyan(f"Loaded {len(hunks)} hunks from state file for re-review."))
            else:
                done = sum(1 for h in hunks if h["decision"] is not None)
                print(c.cyan(f"Resuming previous session ({done}/{len(hunks)} hunks already decided)."))
        else:
            print(
                c.yellow(
                    "Note: Input script has changed since the last saved session; starting fresh."
                )
            )

    if hunks is None:
        hunks = segment_markdown_into_hunks(raw_text, max_lines=max_lines)
        if drop_frontmatter:
            dropped = 0
            for h in hunks:
                if h["section"] == "Front Matter":
                    h["decision"] = "cut"
                    dropped += 1
            if dropped:
                print(c.yellow(f"Pre-cut {dropped} Front Matter hunk(s) (--drop-frontmatter)."))

    undo_stack = []  # snapshots of (hunks_copy, current_idx)
    cur_idx = next_pending_index(hunks, 0)

    while cur_idx is not None:
        pending_count = sum(1 for h in hunks if h["decision"] is None)
        hunk = hunks[cur_idx]
        display_hunk(hunk, cur_idx, len(hunks), pending_count, c)

        prompt_str = c.bold("Keep this hunk? [y,n,s,e,a,d,u,/,q,?]> ")
        try:
            raw_cmd = input_fn(prompt_str).strip()
        except EOFError:
            raw_cmd = "q"

        if not raw_cmd:
            continue

        cmd = raw_cmd[0].lower()

        if cmd == "y":
            undo_stack.append((copy.deepcopy(hunks), cur_idx))
            hunks[cur_idx]["decision"] = "keep"
            save_state(state_file, input_path, source_hash, hunks)
            cur_idx = next_pending_index(hunks, cur_idx + 1)

        elif cmd == "n":
            undo_stack.append((copy.deepcopy(hunks), cur_idx))
            hunks[cur_idx]["decision"] = "cut"
            save_state(state_file, input_path, source_hash, hunks)
            cur_idx = next_pending_index(hunks, cur_idx + 1)

        elif cmd == "s":
            sub_hunks = split_hunk(hunk)
            if not sub_hunks:
                print(c.yellow("Sorry, cannot split this hunk any further."))
                continue
            undo_stack.append((copy.deepcopy(hunks), cur_idx))
            hunks[cur_idx : cur_idx + 1] = sub_hunks
            print(c.cyan(f"Split into {len(sub_hunks)} smaller hunks."))
            save_state(state_file, input_path, source_hash, hunks)

        elif cmd == "e":
            undo_stack.append((copy.deepcopy(hunks), cur_idx))
            edited = edit_hunk_in_editor(hunk["text"])
            if edited.strip():
                hunks[cur_idx]["text"] = edited
                hunks[cur_idx]["units"] = [edited]
                hunks[cur_idx]["decision"] = "keep"
                print(c.green("Saved edited hunk (marked KEEP)."))
            else:
                hunks[cur_idx]["decision"] = "cut"
                print(c.red("Hunk emptied in editor (marked CUT)."))
            save_state(state_file, input_path, source_hash, hunks)
            cur_idx = next_pending_index(hunks, cur_idx + 1)

        elif cmd in ("a", "d"):
            target_section = hunk["section"]
            decision = "keep" if cmd == "a" else "cut"
            undo_stack.append((copy.deepcopy(hunks), cur_idx))
            count = 0
            for j in range(cur_idx, len(hunks)):
                if hunks[j]["section"] != target_section:
                    break
                if hunks[j]["decision"] is None:
                    hunks[j]["decision"] = decision
                    count += 1
            verb = "Kept" if decision == "keep" else "Cut"
            print(c.cyan(f"{verb} {count} hunk(s) in '{target_section}'."))
            save_state(state_file, input_path, source_hash, hunks)
            cur_idx = next_pending_index(hunks, cur_idx + 1)

        elif cmd == "u":
            if not undo_stack:
                print(c.yellow("Nothing to undo."))
                continue
            hunks, cur_idx = undo_stack.pop()
            save_state(state_file, input_path, source_hash, hunks)
            print(c.magenta("Undid last action."))

        elif cmd == "/":
            pattern = raw_cmd[1:].strip()
            if not pattern:
                try:
                    pattern = input_fn("Search regex: ").strip()
                except EOFError:
                    continue
            if not pattern:
                continue
            try:
                rx = re.compile(pattern, re.IGNORECASE)
            except re.error as err:
                print(c.red(f"Invalid regex: {err}"))
                continue

            found = None
            n = len(hunks)
            for offset in range(1, n + 1):
                j = (cur_idx + offset) % n
                if hunks[j]["decision"] is None and (
                    rx.search(hunks[j]["text"]) or rx.search(hunks[j]["section"])
                ):
                    found = j
                    break
            if found is not None:
                cur_idx = found
            else:
                print(c.yellow(f"No pending hunk matched /{pattern}/."))

        elif cmd == "q":
            save_state(state_file, input_path, source_hash, hunks)
            remaining = sum(1 for h in hunks if h["decision"] is None)
            print(
                c.yellow(
                    f"\nSaved progress to '{state_file}' ({remaining} hunk(s) remaining). "
                    f"Re-run the same command to resume."
                )
            )
            return False

        elif cmd == "?":
            print_help(c)

        else:
            print(c.yellow(f"Unknown command '{raw_cmd}'. Enter '?' for help."))

    # All hunks decided — write final cut Markdown script
    final_md = render_kept_markdown(hunks)
    out_parent = os.path.dirname(os.path.abspath(output_path))
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_md)

    kept_count = sum(1 for h in hunks if h["decision"] == "keep")
    cut_count = sum(1 for h in hunks if h["decision"] == "cut")
    save_state(state_file, input_path, source_hash, hunks)

    print(
        c.green(
            f"\nDone! Kept {kept_count} hunk(s), cut {cut_count} hunk(s). "
            f"Wrote cut script to '{output_path}'."
        )
    )
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Interactive git-add-p style Markdown script stripper."
    )
    parser.add_argument("input_md", help="Path to raw Markdown script (.md)")
    parser.add_argument(
        "-o",
        "--output",
        default="script_cut.md",
        help="Output path for stripped Markdown script (default: script_cut.md)",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=20,
        help="Target maximum lines per review hunk before splitting (default: 20)",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Optional session state JSON path (default: <output>.strip_state.json)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Ignore any saved session state and start fresh",
    )
    parser.add_argument(
        "--re-review",
        action="store_true",
        help="Reset decisions on an existing state file and re-review all hunks",
    )
    parser.add_argument(
        "--drop-frontmatter",
        action="store_true",
        help="Automatically mark Front Matter hunks (before Act 1 / Scene 1) as cut",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI terminal colors",
    )

    args = parser.parse_args()
    completed = interactive_strip(
        args.input_md,
        args.output,
        max_lines=args.max_lines,
        state_path=args.state_file,
        reset=args.reset,
        re_review=args.re_review,
        drop_frontmatter=args.drop_frontmatter,
        no_color=args.no_color,
    )
    if not completed:
        sys.exit(10)


if __name__ == "__main__":
    main()
