"""
Stage 0c — Script + Cast List -> show_data.json Builder

Parses a Markdown theatrical script (with Act/Scene headings and bold or colon-delimited
character cues), combines it with a Markdown/text cast list and optional overrides.json,
and outputs show_data.json ready for optimize_mics.py and csv_to_wing_show.py.
"""

import argparse
from collections import defaultdict
import difflib
import json
import os
import re
import sys


ROMAN_NUMERALS = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
    "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20,
}

WORD_NUMERALS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}


def parse_number_token(token):
    """Parses digits, Roman numerals (I-XX), or English number words into an int."""
    t = token.strip().lower()
    if t.isdigit():
        return int(t)
    if t in ROMAN_NUMERALS:
        return ROMAN_NUMERALS[t]
    if t in WORD_NUMERALS:
        return WORD_NUMERALS[t]
    return None


def normalize_gender(raw):
    """Normalizes gender tokens to 'M', 'F', or None."""
    if not raw:
        return None
    val = str(raw).strip().lower()
    if val in ("m", "male", "guy", "guys", "boy", "boys", "man", "men"):
        return "M"
    if val in ("f", "female", "girl", "girls", "woman", "women", "lady", "ladies"):
        return "F"
    return None


def normalize_scene_key(raw_key):
    """
    Normalizes scene identifiers like:
      - 'act_1.scene_3'
      - 'act_1/scene_3'
      - 'A1.S3'
      - 'Act 1 - Scene 3'
    into ('act_1', 'scene_3').
    """
    text = str(raw_key).strip()
    m = re.match(
        r"^(?:act[_\s-]*|a)(\d+)[\s./:-]+(?:scene[_\s-]*|s)(\d+)$",
        text,
        re.IGNORECASE,
    )
    if m:
        return (f"act_{int(m.group(1))}", f"scene_{int(m.group(2))}")
    return None


def _strip_md_inline(text):
    """Removes simple Markdown inline wrappers (**bold**, *italic*, `code`)."""
    cleaned = text.strip()
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
    cleaned = re.sub(r"\*(.+?)\*", r"\1", cleaned)
    cleaned = re.sub(r"`(.+?)`", r"\1", cleaned)
    return cleaned.strip()


def _extract_trailing_gender(text):
    """
    Extracts an optional trailing gender marker such as '(M)', '[F]', '(Girl)', ', M'
    from an actor name string. Returns (clean_text, gender_or_None).
    """
    s = text.strip()
    m = re.search(
        r"\s*[\(\[]\s*(m|f|male|female|guy|guys|girl|girls|boy|boys|man|men|woman|women)\s*[\)\]]\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        return s[: m.start()].strip(), normalize_gender(m.group(1))

    m2 = re.search(r",\s*(m|f|male|female|guy|girl)\s*$", s, re.IGNORECASE)
    if m2:
        return s[: m2.start()].strip(), normalize_gender(m2.group(1))

    return s, None


def parse_cast_list(cast_path):
    """
    Parses a Markdown or plain-text cast list supporting:
      1. Section headers for gender (e.g., '## Guys', '## Girls')
      2. Markdown tables ('| Character | Actor | Gender |')
      3. Bullet or plain lines ('- Scrooge: Dean Stults (M)' or 'Scrooge - Dean Stults')
      4. Multiple comma/semicolon-separated characters mapped to one actor

    Returns:
      - char_map: {char_lower: {"character_name": str, "associated_actor": str, "gender": str|None}}
      - actor_gender: {actor_name: "M"|"F"}
      - actor_to_chars: {actor_lower: [canonical_char_names]}
    """
    with open(cast_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    char_map = {}
    actor_gender = {}
    actor_to_chars = defaultdict(list)

    current_section_gender = None
    table_col_order = None  # tuple of indices (char_idx, actor_idx, gender_idx_or_None)

    def register(raw_chars, raw_actor, explicit_gender=None):
        actor_clean, trailing_g = _extract_trailing_gender(_strip_md_inline(raw_actor))
        if not actor_clean:
            return
        gender = normalize_gender(explicit_gender) or trailing_g or current_section_gender
        if gender and actor_clean not in actor_gender:
            actor_gender[actor_clean] = gender

        # Split multiple characters listed on one line (e.g., "Caroler C, Villager 2")
        parts = [
            _strip_md_inline(p)
            for p in re.split(r"[,;]+", raw_chars)
            if _strip_md_inline(p)
        ]
        for char_name in parts:
            key = char_name.lower()
            entry = {
                "character_name": char_name,
                "associated_actor": actor_clean,
                "gender": gender,
            }
            char_map[key] = entry
            if char_name not in actor_to_chars[actor_clean.lower()]:
                actor_to_chars[actor_clean.lower()].append(char_name)

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("<!--"):
            table_col_order = None
            continue

        # Check Markdown headings for gender section context (e.g., "## Guys", "## Girls")
        heading_match = re.match(r"^#+\s+(.+?)\s*$", line)
        if heading_match:
            table_col_order = None
            heading_text = _strip_md_inline(heading_match.group(1)).lower()
            if any(tok in heading_text for tok in ("guy", "boy", "men", "male")) and not any(
                tok in heading_text for tok in ("girl", "women", "female")
            ):
                current_section_gender = "M"
            elif any(tok in heading_text for tok in ("girl", "women", "woman", "female", "ladies")):
                current_section_gender = "F"
            else:
                current_section_gender = None
            continue

        # Markdown table row
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            # Ignore separator rows like | --- | --- |
            if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c):
                continue

            lower_cells = [_strip_md_inline(c).lower() for c in cells]
            if any(h in lower_cells for h in ("character", "role", "character(s)", "part")) and any(
                h in lower_cells for h in ("actor", "cast", "cast member", "performer", "name")
            ):
                char_idx = next(
                    i for i, h in enumerate(lower_cells)
                    if h in ("character", "role", "character(s)", "part")
                )
                actor_idx = next(
                    i for i, h in enumerate(lower_cells)
                    if h in ("actor", "cast", "cast member", "performer", "name")
                )
                gender_idx = next(
                    (i for i, h in enumerate(lower_cells) if h in ("gender", "sex", "group")),
                    None,
                )
                table_col_order = (char_idx, actor_idx, gender_idx)
                continue

            if len(cells) >= 2:
                c_idx, a_idx, g_idx = table_col_order if table_col_order else (0, 1, 2 if len(cells) >= 3 else None)
                if c_idx < len(cells) and a_idx < len(cells):
                    g_val = cells[g_idx] if (g_idx is not None and g_idx < len(cells)) else None
                    register(cells[c_idx], cells[a_idx], g_val)
            continue

        table_col_order = None

        # Strip optional bullet or numbered list prefix
        item = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", line)

        # Match "Character: Actor" or "Character - Actor" or "Character — Actor" or tab-separated
        if "\t" in item:
            parts = [p.strip() for p in item.split("\t") if p.strip()]
            if len(parts) >= 2:
                register(parts[0], parts[1], parts[2] if len(parts) >= 3 else None)
                continue

        sep_match = re.match(r"^(.+?)\s*(?::|\s+[—–-]\s+|\s+as\s+)\s*(.+)$", item, re.IGNORECASE)
        if sep_match:
            left, right = sep_match.group(1).strip(), sep_match.group(2).strip()
            # Handle "Actor as Character" phrasing vs "Character: Actor" / "Character - Actor"
            if re.search(r"\s+as\s+", item, re.IGNORECASE) and ":" not in item:
                register(right, left)
            else:
                register(left, right)

    # Propagate any actor-level gender to all entries for that actor
    for entry in char_map.values():
        if not entry["gender"] and entry["associated_actor"] in actor_gender:
            entry["gender"] = actor_gender[entry["associated_actor"]]

    return char_map, actor_gender, dict(actor_to_chars)


def load_overrides(overrides_path):
    """Loads optional overrides.json with normalized keys and safe defaults."""
    defaults = {
        "aliases": {},
        "group_expansions": {},
        "scene_group_expansions": {},
        "ignore_cues": set(),
        "scene_weights": {},
        "add_characters": defaultdict(list),
        "remove_characters": defaultdict(set),
    }
    if not overrides_path or not os.path.exists(overrides_path):
        return defaults

    with open(overrides_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    for k, v in raw.get("aliases", {}).items():
        defaults["aliases"][k.strip().lower()] = v.strip()

    for k, v in raw.get("group_expansions", {}).items():
        scene_key = normalize_scene_key(k)
        if scene_key and isinstance(v, dict):
            defaults["scene_group_expansions"][scene_key] = {
                gk.strip().lower(): list(gv) for gk, gv in v.items()
            }
        elif isinstance(v, list):
            defaults["group_expansions"][k.strip().lower()] = list(v)

    for cue in raw.get("ignore_cues", []):
        defaults["ignore_cues"].add(str(cue).strip().lower())

    for raw_scene_key, cfg in raw.get("scene_weights", {}).items():
        sk = normalize_scene_key(raw_scene_key)
        if not sk:
            continue
        if isinstance(cfg, (int, float)):
            defaults["scene_weights"][sk] = {"weight": float(cfg)}
        elif isinstance(cfg, bool):
            defaults["scene_weights"][sk] = {"quick_scene": cfg}
        elif isinstance(cfg, dict):
            defaults["scene_weights"][sk] = dict(cfg)

    # Also accept shorthand "quick_scenes": ["act_1.scene_3", "A1.S4"]
    for raw_scene_key in raw.get("quick_scenes", []):
        sk = normalize_scene_key(raw_scene_key)
        if sk:
            defaults["scene_weights"].setdefault(sk, {})["quick_scene"] = True

    for raw_scene_key, items in raw.get("add_characters", {}).items():
        sk = normalize_scene_key(raw_scene_key)
        if sk and isinstance(items, list):
            defaults["add_characters"][sk].extend(items)

    for raw_scene_key, items in raw.get("remove_characters", {}).items():
        sk = normalize_scene_key(raw_scene_key)
        if sk and isinstance(items, list):
            for item in items:
                defaults["remove_characters"][sk].add(str(item).strip().lower())

    return defaults


def _clean_cue_name(raw_cue):
    """
    Normalizes a candidate speaker cue:
      - Strips trailing colon/period
      - Strips existing mic number annotation like '(12)'
      - Strips inline parenthetical stage directions like '(angrily)' or '(V.O.)'
    """
    text = raw_cue.strip()
    text = re.sub(r"[:.]+$", "", text).strip()
    text = re.sub(r"\s*\(\d+\)\s*$", "", text).strip()
    text = re.sub(r"\s*\([^)]*\)\s*", " ", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _split_compound_cue(cue_text, known_keys):
    """
    Splits compound speaker cues like 'Child A & Child B' or 'Caroler A/Caroler B'
    unless the entire cue already matches a known character/alias/group key.
    """
    low = cue_text.lower()
    if low in known_keys:
        return [cue_text]

    if re.search(r"\s*(?:&|/|\band\b)\s*", cue_text, re.IGNORECASE):
        parts = [
            p.strip()
            for p in re.split(r"\s*(?:&|/|\band\b)\s*", cue_text, flags=re.IGNORECASE)
            if p.strip()
        ]
        if len(parts) > 1:
            return parts

    return [cue_text]


def _extract_cues_from_line(line, known_keys, allow_plain_colon=True):
    """
    Extracts candidate speaker cues from a single Markdown line.
    Returns a list of (cue_text, is_strict_cue_position) tuples.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
        return []

    # Strip leading blockquote/list prefix when checking line-start position
    content = re.sub(r"^(?:>\s*|[-*+]\s+)", "", stripped)
    candidates = []

    bold_matches = list(re.finditer(r"\*\*(.+?)\*\*", content))
    if bold_matches:
        for idx, m in enumerate(bold_matches):
            raw_inner = m.group(1).strip()
            at_line_start = (idx == 0 and m.start() == 0)
            after_bold = content[m.end():].lstrip()
            has_colon = raw_inner.endswith(":") or after_bold.startswith(":")
            cleaned = _clean_cue_name(raw_inner)
            if not cleaned:
                continue

            if at_line_start or has_colon:
                candidates.append((cleaned, True))
            elif cleaned.lower() in known_keys:
                candidates.append((cleaned, False))
        return candidates

    # Fallback: unbolded "CHARACTER: dialogue" at start of line
    if allow_plain_colon:
        colon_match = re.match(r"^([A-Za-z][A-Za-z0-9 .'\-/&()]{0,42}):(?:\s+|$)", content)
        if colon_match:
            cleaned = _clean_cue_name(colon_match.group(1))
            if cleaned:
                # Avoid false positives on URLs/notes/long sentences unless known or ALL CAPS
                words = cleaned.split()
                is_known = cleaned.lower() in known_keys
                is_all_caps = cleaned.isupper() and len(words) <= 4
                if is_known or is_all_caps:
                    candidates.append((cleaned, True))

    return candidates


def build_show_data(script_md_path, cast_list_path, overrides_path=None):
    """
    Parses the script + cast list + overrides into:
      - show_data dict (compatible with optimize_mics.py & csv_to_wing_show.py)
      - diagnostics dict (unmatched_cues, unused_characters, empty_scenes, scene_counts)
    """
    char_map, actor_gender, actor_to_chars = parse_cast_list(cast_list_path)
    overrides = load_overrides(overrides_path)

    known_keys = (
        set(char_map.keys())
        | set(overrides["aliases"].keys())
        | set(overrides["group_expansions"].keys())
        | set(overrides["ignore_cues"])
    )
    for sc_groups in overrides["scene_group_expansions"].values():
        known_keys.update(sc_groups.keys())

    with open(script_md_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    act_heading_re = re.compile(
        r"^\s*#+\s*.*?\bact\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        re.IGNORECASE,
    )
    scene_heading_re = re.compile(
        r"^\s*#+\s*.*?\bscene\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        re.IGNORECASE,
    )
    prologue_re = re.compile(r"^\s*#+\s*.*?\bprologue\b", re.IGNORECASE)
    epilogue_re = re.compile(r"^\s*#+\s*.*?\bepilogue\b", re.IGNORECASE)

    current_act = 1
    current_scene = None
    scenes_order = []  # ordered list of (act_key, scene_key)
    scene_entries = defaultdict(list)  # (act_key, scene_key) -> list of cast dicts
    scene_seen_pairs = defaultdict(set)  # (act_key, scene_key) -> set of (actor_lower, char_lower)
    unmatched_cues = defaultdict(list)  # cue_text -> [scene_label, ...]
    matched_cast_chars = set()

    def ensure_scene(act_num, scene_num):
        key = (f"act_{act_num}", f"scene_{scene_num}")
        if key not in scene_entries:
            scene_entries[key] = []
            scenes_order.append(key)
        return key

    def add_resolved_entry(scene_key, entry):
        pair = (entry["associated_actor"].lower(), entry["character_name"].lower())
        if pair in scene_seen_pairs[scene_key]:
            return
        scene_seen_pairs[scene_key].add(pair)
        out = {
            "character_name": entry["character_name"],
            "associated_actor": entry["associated_actor"],
        }
        if entry.get("gender"):
            out["gender"] = entry["gender"]
        scene_entries[scene_key].append(out)
        matched_cast_chars.add(entry["character_name"].lower())

    def resolve_and_add_cue(scene_key, cue_text, is_strict):
        low = cue_text.lower()
        if low in overrides["ignore_cues"]:
            return

        # 1. Scene-specific or global group expansion
        sc_groups = overrides["scene_group_expansions"].get(scene_key, {})
        expanded_list = sc_groups.get(low) or overrides["group_expansions"].get(low)
        if expanded_list is not None:
            for member_name in expanded_list:
                resolve_and_add_cue(scene_key, member_name, is_strict=True)
            return

        # 2. Alias resolution
        canonical = overrides["aliases"].get(low, cue_text)
        c_low = canonical.lower()

        # 3. Direct character match
        if c_low in char_map:
            add_resolved_entry(scene_key, char_map[c_low])
            return

        # 4. Compound cue split (e.g. "Child A & Child B")
        sub_cues = _split_compound_cue(canonical, known_keys)
        if len(sub_cues) > 1:
            for sub in sub_cues:
                resolve_and_add_cue(scene_key, sub, is_strict=is_strict)
            return

        if is_strict:
            label = f"{scene_key[0]}.{scene_key[1]}"
            if label not in unmatched_cues[cue_text]:
                unmatched_cues[cue_text].append(label)

    for line in lines:
        act_m = act_heading_re.search(line)
        scene_m = scene_heading_re.search(line)

        if act_m:
            parsed_act = parse_number_token(act_m.group(1))
            if parsed_act is not None:
                current_act = parsed_act
                if not scene_m:
                    current_scene = None

        if scene_m:
            parsed_scene = parse_number_token(scene_m.group(1))
            if parsed_scene is not None:
                current_scene = ensure_scene(current_act, parsed_scene)
            continue

        if prologue_re.search(line):
            current_scene = ensure_scene(current_act, 0)
            continue

        if epilogue_re.search(line):
            existing_nums = [
                int(sk[1].split("_")[-1])
                for sk in scenes_order
                if sk[0] == f"act_{current_act}" and sk[1].split("_")[-1].isdigit()
            ]
            next_num = (max(existing_nums) + 1) if existing_nums else 1
            current_scene = ensure_scene(current_act, next_num)
            continue

        if act_m:
            continue

        if current_scene is None:
            continue

        for cue_text, is_strict in _extract_cues_from_line(line, known_keys):
            resolve_and_add_cue(current_scene, cue_text, is_strict=is_strict)

    # Apply manual add_characters overrides (including silent onstage actors)
    for sk, additions in overrides["add_characters"].items():
        if sk not in scene_entries:
            scene_entries[sk] = []
            scenes_order.append(sk)
        for item in additions:
            if isinstance(item, dict) and item.get("associated_actor"):
                entry = {
                    "character_name": item.get("character_name", "").strip(),
                    "associated_actor": item["associated_actor"].strip(),
                    "gender": normalize_gender(item.get("gender"))
                    or actor_gender.get(item["associated_actor"].strip()),
                }
                add_resolved_entry(sk, entry)
            elif isinstance(item, str):
                name = item.strip()
                n_low = name.lower()
                canonical = overrides["aliases"].get(n_low, name)
                c_low = canonical.lower()
                if c_low in char_map:
                    add_resolved_entry(sk, char_map[c_low])
                elif c_low in actor_to_chars:
                    # Allow adding by actor name (uses their first listed character)
                    first_char = actor_to_chars[c_low][0]
                    add_resolved_entry(sk, char_map[first_char.lower()])
                else:
                    label = f"{sk[0]}.{sk[1]} (overrides.add_characters)"
                    if label not in unmatched_cues[name]:
                        unmatched_cues[name].append(label)

    # Apply remove_characters overrides
    for sk, removals in overrides["remove_characters"].items():
        if sk not in scene_entries:
            continue
        scene_entries[sk] = [
            e
            for e in scene_entries[sk]
            if e["character_name"].lower() not in removals
            and e["associated_actor"].lower() not in removals
        ]

    # Sort scenes naturally by (act_num, scene_num) and assemble hierarchical show_data.json
    def scene_sort_key(sk):
        a_num = int(sk[0].split("_")[-1]) if sk[0].split("_")[-1].isdigit() else 999
        s_num = int(sk[1].split("_")[-1]) if sk[1].split("_")[-1].isdigit() else 999
        return (a_num, s_num)

    show_data = {}
    empty_scenes = []
    for sk in sorted(scenes_order, key=scene_sort_key):
        act_key, scene_key = sk
        cast_list = scene_entries[sk]
        if not cast_list:
            empty_scenes.append(f"{act_key}.{scene_key}")
        show_data.setdefault(act_key, {})

        weight_cfg = overrides["scene_weights"].get(sk)
        if weight_cfg:
            scene_obj = {}
            if weight_cfg.get("quick_scene"):
                scene_obj["quick_scene"] = True
            if "weight" in weight_cfg:
                scene_obj["weight"] = float(weight_cfg["weight"])
            scene_obj["cast"] = cast_list
            show_data[act_key][scene_key] = scene_obj
        else:
            show_data[act_key][scene_key] = cast_list

    # Build fuzzy-match suggestions for unmatched cues
    all_known_char_names = [e["character_name"] for e in char_map.values()]
    unmatched_report = {}
    for cue, locs in sorted(unmatched_cues.items()):
        suggestions = difflib.get_close_matches(cue, all_known_char_names, n=3, cutoff=0.55)
        unmatched_report[cue] = {
            "scenes": locs,
            "suggestions": suggestions,
        }

    unused_characters = [
        e["character_name"]
        for k, e in char_map.items()
        if k not in matched_cast_chars
    ]

    diagnostics = {
        "total_acts": len(show_data),
        "total_scenes": len(scenes_order),
        "total_cast_roles": len(char_map),
        "unmatched_cues": unmatched_report,
        "unused_characters": unused_characters,
        "empty_scenes": empty_scenes,
    }
    return show_data, diagnostics


def print_diagnostics(diagnostics):
    print(
        f"Parsed {diagnostics['total_acts']} act(s), {diagnostics['total_scenes']} scene(s), "
        f"and {diagnostics['total_cast_roles']} cast role(s)."
    )

    if diagnostics["unmatched_cues"]:
        print("\nWARNING — Unmatched speaker cues found in script (add to cast_list or overrides.json):")
        for cue, info in diagnostics["unmatched_cues"].items():
            sug = f"  -> did you mean: {', '.join(info['suggestions'])}?" if info["suggestions"] else ""
            print(f"  - '{cue}' in {', '.join(info['scenes'])}{sug}")

    if diagnostics["unused_characters"]:
        print(
            "\nNote — Cast list roles with 0 speaking scenes "
            "(add via overrides.json 'add_characters' if onstage without lines):"
        )
        print("  " + ", ".join(diagnostics["unused_characters"]))

    if diagnostics["empty_scenes"]:
        print("\nNote — Scenes with 0 detected characters:")
        print("  " + ", ".join(diagnostics["empty_scenes"]))


def main():
    parser = argparse.ArgumentParser(
        description="Build show_data.json from a Markdown script, Markdown/text cast list, and optional overrides.json."
    )
    parser.add_argument("script_md", help="Path to the cut Markdown script (.md)")
    parser.add_argument("cast_list", help="Path to the Markdown or text cast list (.md / .txt)")
    parser.add_argument(
        "--overrides",
        default=None,
        help="Optional overrides.json (aliases, group_expansions, quick_scenes, add_characters, remove_characters, ignore_cues)",
    )
    parser.add_argument(
        "--output",
        default="show_data.json",
        help="Output path for generated show_data.json (default: show_data.json)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any speaker cues in the script cannot be resolved against the cast list or overrides",
    )

    args = parser.parse_args()

    show_data, diagnostics = build_show_data(
        args.script_md, args.cast_list, overrides_path=args.overrides
    )
    print_diagnostics(diagnostics)

    if args.strict and diagnostics["unmatched_cues"]:
        print("\nError (--strict): Unresolved speaker cues found. Fix cast list or overrides.json.")
        sys.exit(2)

    out_parent = os.path.dirname(os.path.abspath(args.output))
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(show_data, f, indent=2)

    print(f"\nWrote show data to '{args.output}'.")


if __name__ == "__main__":
    main()
