import argparse
import csv
from datetime import datetime
import os
import re
from collections import defaultdict


def parse_tracking_chart(csv_path):
    """
    Parses mic_tracking_sheet.csv and builds:
      1. scene_char_to_mic[(act_num, scene_num)][char_lower] = mic_num
      2. scene_idx_char_to_mic[scene_idx][char_lower] = mic_num
      3. global_char_to_mic[char_lower] = mic_num (fallback)
    """
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = list(csv.reader(f))

    header_idx = None
    scene_headers = []
    for idx, row in enumerate(reader):
        if row and len(row) >= 4 and row[0].strip() == "Mic #" and "Actor" in row[1]:
            header_idx = idx
            scene_headers = [col.strip() for col in row[3:] if col.strip()]
            break

    if header_idx is None:
        raise ValueError("Could not find the 'Mic #' header row in the CSV file.")

    # Map column index -> (act_num, scene_num) tuple (e.g. "Act 1 - Scene 2" -> (1, 2))
    col_to_act_scene = {}
    for s_idx, label in enumerate(scene_headers):
        nums = [int(n) for n in re.findall(r"\d+", label)]
        if len(nums) >= 2:
            col_to_act_scene[s_idx] = (nums[0], nums[1])
        elif len(nums) == 1:
            col_to_act_scene[s_idx] = (1, nums[0])

    scene_char_to_mic = defaultdict(dict)
    scene_idx_char_to_mic = defaultdict(dict)
    global_char_to_mic = {}

    for row in reader[header_idx + 1:]:
        if not row or not row[0].strip().startswith("Mic "):
            if row and row[0].strip().startswith("==="):
                break
            continue

        mic_num = int(re.sub(r"[^\d]", "", row[0].strip()))
        # Characters column can be comma-separated if an actor plays multiple roles
        raw_chars = row[2].strip()
        if not raw_chars or raw_chars == "—":
            continue

        chars = [c.strip() for c in raw_chars.split(",") if c.strip()]
        cells = row[3:]

        for c in chars:
            c_lower = c.lower()
            if c_lower not in global_char_to_mic:
                global_char_to_mic[c_lower] = mic_num

            for s_idx in range(len(scene_headers)):
                val = cells[s_idx].strip() if s_idx < len(cells) else ""
                # Active ('X') or holding ('-') on that mic in this scene
                if val in ("X", "-"):
                    scene_idx_char_to_mic[s_idx][c_lower] = mic_num
                    if s_idx in col_to_act_scene:
                        scene_char_to_mic[col_to_act_scene[s_idx]][c_lower] = mic_num

    return scene_char_to_mic, scene_idx_char_to_mic, global_char_to_mic


def _strip_existing_version_header(lines):
    """Removes a previously injected lav-chart version header so runs stay idempotent."""
    idx = 0
    if idx < len(lines) and lines[idx].startswith("<!-- lav-chart-meta:"):
        idx += 1
        if idx < len(lines) and lines[idx].startswith("> *Lav Chart "):
            idx += 1
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
    return lines[idx:]


def annotate_markdown_script(md_path, csv_path, output_path, version=None, note=""):
    scene_char_to_mic, scene_idx_char_to_mic, global_char_to_mic = parse_tracking_chart(csv_path)

    with open(md_path, mode="r", encoding="utf-8") as f:
        lines = _strip_existing_version_header(f.readlines())

    current_act = 1
    current_scene = None
    scene_counter = -1
    replacements_made = 0

    # Regex to detect Act / Scene headings in Markdown (e.g., "# Act 1", "## Scene 2", "### Act 1, Scene 3")
    act_pattern = re.compile(r"^\s*#+\s*.*?\bact\s+(\d+)", re.IGNORECASE)
    scene_pattern = re.compile(r"^\s*#+\s*.*?\bscene\s+(\d+)", re.IGNORECASE)

    # Regex to match **Bold Text**
    bold_pattern = re.compile(r"\*\*(.+?)\*\*")

    def replace_bold_match(match):
        nonlocal replacements_made
        raw_text = match.group(1)
        # Strip trailing colons/punctuation inside bold tags if present (e.g., **Scrooge:**)
        stripped = raw_text.strip()
        suffix = ""
        if stripped.endswith(":"):
            stripped = stripped[:-1].strip()
            suffix = ":"

        # Avoid double-tagging if the script was already run
        if re.search(r"\(\d+\)$", stripped):
            return match.group(0)

        key = stripped.lower()
        mic_num = None

        # 1. Try exact (Act, Scene) match
        if current_scene is not None and (current_act, current_scene) in scene_char_to_mic:
            mic_num = scene_char_to_mic[(current_act, current_scene)].get(key)

        # 2. Try sequential scene counter match
        if mic_num is None and scene_counter in scene_idx_char_to_mic:
            mic_num = scene_idx_char_to_mic[scene_counter].get(key)

        # 3. Fallback to global character-to-mic lookup
        if mic_num is None:
            mic_num = global_char_to_mic.get(key)

        if mic_num is not None:
            replacements_made += 1
            return f"**{stripped} ({mic_num}){suffix}**"

        return match.group(0)

    output_lines = []
    if version or (note and note.strip()):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        meta_fields = [f"generated={ts}"]
        display_parts = []
        if version:
            meta_fields.insert(0, f"version={version}")
            display_parts.append(f"Lav Chart {version}")
        else:
            display_parts.append("Lav Chart")
        display_parts.append(f"Generated {ts}")
        if note and note.strip():
            clean_note = note.strip().replace("-->", "--")
            meta_fields.append(f"note={clean_note}")
            display_parts.append(clean_note)
        output_lines.append(f"<!-- lav-chart-meta: {' | '.join(meta_fields)} -->\n")
        output_lines.append(f"> *{' — '.join(display_parts)}*\n\n")

    for line in lines:
        act_match = act_pattern.search(line)
        if act_match:
            current_act = int(act_match.group(1))

        scene_match = scene_pattern.search(line)
        if scene_match:
            current_scene = int(scene_match.group(1))
            scene_counter += 1

        updated_line = bold_pattern.sub(replace_bold_match, line)
        output_lines.append(updated_line)

    out_parent = os.path.dirname(os.path.abspath(output_path))
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)

    with open(output_path, mode="w", encoding="utf-8") as f:
        f.writelines(output_lines)

    print(f"Done! Annotated {replacements_made} character cues and saved to '{output_path}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Append mic numbers from mic_tracking_sheet.csv to bold character names in a Markdown script."
    )
    parser.add_argument("script_md", help="Path to the input Markdown script (.md)")
    parser.add_argument(
        "--csv",
        default="mic_tracking_sheet.csv",
        help="Path to mic_tracking_sheet.csv (default: mic_tracking_sheet.csv)",
    )
    parser.add_argument(
        "--output",
        default="script_with_mics.md",
        help="Path for the annotated output Markdown file (default: script_with_mics.md)",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Optional build version label (e.g., 'v4') embedded at the top of the annotated script",
    )
    parser.add_argument(
        "--note",
        default="",
        help="Optional revision note embedded alongside version metadata",
    )

    args = parser.parse_args()
    annotate_markdown_script(
        args.script_md,
        args.csv,
        args.output,
        version=args.version,
        note=args.note,
    )