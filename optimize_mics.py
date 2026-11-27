"""
Theatrical Lav Solver & Microphone Allocation Optimizer
Solves resource-constrained wireless microphone assignments and produces operational paperwork:
  1. Master Sound Tracking Sheet (Matrix with 'X', '-', blank, split shared rows, odd/even mic striping)
  2. Backstage Quick-Change & Handoff Cue Sheet (A2 / wardrobe cue list with Blue/Pink gender fills)
  3. Top-of-Show / Pre-Show Rigging Preset Sheet (Separate 'Guys' and 'Girls' Excel tabs matching rack check-in layout)
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import json
import math
import os
import random
import re
import sys

try:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False


# -----------------------------------------------------------------------------
# Gender Detection & Normalization Helpers
# -----------------------------------------------------------------------------
COMMON_MALE_FIRST_NAMES = {
    "aaron", "adam", "alan", "albert", "alex", "alexander", "andrew", "anthony",
    "arthur", "art", "austin", "ben", "benjamin", "billy", "bob", "bobby",
    "brad", "brandon", "brian", "brighton", "bruce", "bryan", "caleb", "cameron",
    "carl", "carlos", "charles", "charlie", "chris", "christian", "christopher",
    "cole", "colin", "connor", "cory", "craig", "daniel", "danny", "david",
    "dean", "dennis", "derek", "devon", "dominic", "donald", "douglas", "dylan",
    "edward", "eli", "elijah", "elliot", "eric", "ethan", "evan", "felix",
    "frank", "fred", "gabriel", "gary", "george", "gerald", "graham", "grant",
    "greg", "gregory", "harrison", "harry", "henry", "hunter", "ian", "isaac",
    "jack", "jackson", "jacob", "jake", "james", "jason", "jeff", "jeffrey",
    "jeremy", "jesse", "joe", "joel", "john", "johnny", "jonathan", "jordan",
    "joseph", "josh", "joshua", "julian", "justin", "keith", "kenneth", "kevin",
    "kingsley", "kobe", "kory", "kyle", "lance", "larry", "lawrence", "leo",
    "liam", "lincoln", "locke", "logan", "louis", "lucas", "luke", "marcus",
    "mark", "marshall", "martin", "mason", "matthew", "matt", "max", "michael",
    "mike", "miles", "mitchell", "nathan", "nathaniel", "nicholas", "nick",
    "noah", "nolan", "oliver", "oscar", "owen", "patrick", "paul", "peter",
    "philip", "quinten", "quentin", "raymond", "richard", "rick", "robert",
    "roger", "ronald", "ross", "roy", "russell", "ryan", "sam", "samuel",
    "scott", "sean", "sebastian", "seth", "shane", "shawn", "simon", "spencer",
    "stephen", "steve", "steven", "stuart", "thomas", "tim", "timothy", "toby",
    "todd", "tom", "tommy", "tony", "travis", "trevor", "tristan", "tyler",
    "victor", "vincent", "walter", "warren", "wayne", "wesley", "will",
    "william", "wyatt", "zach", "zachary"
}

COMMON_FEMALE_FIRST_NAMES = {
    "abigail", "abby", "addison", "adriana", "alexis", "alice", "alicia",
    "alison", "allison", "alyssa", "amanda", "amber", "amelia", "amy", "andrea",
    "angela", "anna", "anne", "annie", "ashley", "audrey", "autumn", "ava",
    "avery", "beatrice", "bella", "beth", "bethany", "betty", "beverly",
    "bianca", "brenda", "brianna", "bridget", "brittany", "brooke", "caitlin",
    "camila", "caroline", "carolyn", "carrie", "catherine", "charlotte",
    "chloe", "christina", "christine", "claire", "clara", "courtney", "crystal",
    "cynthia", "danielle", "dawn", "deborah", "debra", "delilah", "denise",
    "diana", "diane", "donna", "dorothy", "eleanor", "elena", "elizabeth",
    "ella", "ellie", "emily", "emma", "erica", "erin", "eva", "evelyn",
    "faith", "fiona", "frances", "gabriella", "gabrielle", "gemma", "georgia",
    "grace", "gwyneth", "hailey", "hannah", "harper", "hazel", "heather",
    "helen", "holly", "hope", "isabella", "isabelle", "jacqueline", "jade", "jamie",
    "jane", "janet", "janice", "jasmine", "jenna", "jennifer", "jenny",
    "jessica", "jill", "joan", "joanna", "jocelyn", "jordan", "josephine",
    "joy", "joyce", "judith", "judy", "julia", "julie", "kaitlyn", "karen",
    "katherine", "kathleen", "kathryn", "katie", "kayla", "kelly", "kendra",
    "kennedy", "kimberly", "kory", "kristen", "kristin", "kylie", "landyn", "laura", "lauren",
    "leah", "lillian", "lily", "linda", "lindsay", "lisa", "lucy", "lydia",
    "madeline", "madeleine","madison", "maggie", "makayla", "margaret", "maria", "mariah",
    "marie", "marilyn", "martha", "mary", "maya", "megan", "melanie",
    "melissa", "michelle", "molly", "monica", "morgan", "nancy", "natalie",
    "nicole", "nina", "nora", "olivia", "paige", "pamela", "patricia", "patti", "paula",
    "penelope", "peyton", "phoebe", "rachel", "rebecca", "riley", "robin",
    "rose", "ruby", "ruth", "sabrina", "sadie", "samantha", "sandra", "sara",
    "sarah", "savannah", "scarlett", "shannon", "sharon", "shelby", "shirley",
    "sierra", "sofia", "sophia", "sophie", "stacy", "stephanie", "summer",
    "susan", "sydney", "taylor", "teresa", "tessa", "tiffany", "tracy",
    "valerie", "vanessa", "veronica", "victoria", "violet", "virginia",
    "vivian", "wendy", "whitney", "zoe", "zoey"
}


def normalize_gender_value(raw_val):
    """Normalizes gender strings into 'M' (Guys) or 'F' (Girls), or None if unknown."""
    if not raw_val:
        return None
    val = str(raw_val).strip().lower()
    if val in ("m", "male", "guy", "guys", "boy", "boys", "man", "men"):
        return "M"
    if val in ("f", "female", "girl", "girls", "woman", "women", "lady", "ladies"):
        return "F"
    return None


def infer_actor_gender(actor_name, characters=None):
    """Infers 'M' or 'F' from character titles or common first names as a fallback."""
    if characters:
        for char in characters:
            c_low = char.lower()
            if any(
                tok in c_low
                for tok in ("mrs.", "mrs ", "miss ", "ms.", "lady ", "queen ", "mother", "sister", "girl", "woman", "laundress", "belle", "martha", "belinda")
            ):
                return "F"
            if any(
                tok in c_low
                for tok in ("mr.", "mr ", "sir ", "lord ", "king ", "father", "brother", "boy", "man", "gentleman", "scrooge", "fezziwig", "marley", "bob cratchit", "tiny tim", "tim cratchit", "fred")
            ):
                return "M"

    first_name = actor_name.strip().split()[0].lower() if actor_name.strip() else ""
    if first_name in COMMON_FEMALE_FIRST_NAMES and first_name not in COMMON_MALE_FIRST_NAMES:
        return "F"
    if first_name in COMMON_MALE_FIRST_NAMES and first_name not in COMMON_FEMALE_FIRST_NAMES:
        return "M"
    # Default fallback to 'M' if completely ambiguous so every actor lands on a sheet
    return "M"


def load_cast_gender_file(cast_file_path):
    """Loads an optional JSON mapping of actor_name -> gender ('M'/'F'/'guy'/'girl')."""
    if not cast_file_path or not os.path.exists(cast_file_path):
        return {}
    with open(cast_file_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    mapping = {}
    if isinstance(raw, dict):
        # Support either {"Dean Stults": "M", ...} or {"guys": [...], "girls": [...]}
        if "guys" in raw or "girls" in raw:
            for a in raw.get("guys", []):
                mapping[a.strip()] = "M"
            for a in raw.get("girls", []):
                mapping[a.strip()] = "F"
        else:
            for actor, g in raw.items():
                norm = normalize_gender_value(g)
                if norm:
                    mapping[actor.strip()] = norm
    return mapping


# -----------------------------------------------------------------------------
# 1. Parsing & Show Timeline Construction
# -----------------------------------------------------------------------------
def parse_show_data(show_json, external_gender_map=None):
    """
    Parses hierarchical JSON into an ordered timeline with act-break boundary tags,
    quick-scene weights, and actor gender classifications ('M' for Guys, 'F' for Girls).
    """
    scenes = []
    actor_gender = dict(external_gender_map) if external_gender_map else {}
    actor_all_chars = defaultdict(list)

    act_keys = sorted(
        show_json.keys(),
        key=lambda x: int(x.split("_")[-1]) if x.split("_")[-1].isdigit() else str(x),
    )

    for a_idx, act_key in enumerate(act_keys):
        scene_dict = show_json[act_key]
        scene_keys = sorted(
            scene_dict.keys(),
            key=lambda x: (
                int(x.split("_")[-1]) if x.split("_")[-1].isdigit() else str(x)
            ),
        )

        for s_idx, scene_key in enumerate(scene_keys):
            raw_scene = scene_dict[scene_key]

            # Support both standard list format and dict format with quick_scene/weight metadata
            if isinstance(raw_scene, dict):
                cast = raw_scene.get("cast", [])
                is_quick = raw_scene.get("quick_scene", False)
                scene_weight = raw_scene.get("weight", 0.5 if is_quick else 1.0)
            else:
                cast = raw_scene
                scene_weight = 1.0

            actor_to_chars = defaultdict(list)
            for entry in cast:
                actor = entry["associated_actor"].strip()
                char = entry.get("character_name", "").strip()
                if char and char not in actor_to_chars[actor]:
                    actor_to_chars[actor].append(char)
                if char and char not in actor_all_chars[actor]:
                    actor_all_chars[actor].append(char)

                # Check if gender was explicitly provided in show_data.json
                explicit_g = normalize_gender_value(
                    entry.get("gender") or entry.get("sex") or entry.get("group")
                )
                if explicit_g and actor not in actor_gender:
                    actor_gender[actor] = explicit_g

            actors = sorted(list(actor_to_chars.keys()))
            is_last_scene_of_act = s_idx == len(scene_keys) - 1
            is_last_act = a_idx == len(act_keys) - 1

            scenes.append({
                "act": act_key,
                "scene": scene_key,
                "label": f"{act_key.replace('act_', 'A').title()}.{scene_key.replace('scene_', 'S').title()}",
                "actors": actors,
                "actor_chars": dict(actor_to_chars),
                "weight": float(scene_weight),
                "act_break_after": (is_last_scene_of_act and not is_last_act),
            })

    # Resolve gender for any actor not explicitly tagged
    inferred_count = 0
    for actor, chars in actor_all_chars.items():
        if actor not in actor_gender:
            actor_gender[actor] = infer_actor_gender(actor, chars)
            inferred_count += 1

    if inferred_count > 0:
        print(
            f"Note: Auto-classified gender for {inferred_count} cast member(s). "
            f"You can override any actor via 'gender': 'M'/'F' in show_data.json or --cast-file."
        )

    return scenes, actor_gender, dict(actor_all_chars)


# -----------------------------------------------------------------------------
# 2. Hard Constraints & Objective Cost Function
# -----------------------------------------------------------------------------
def has_sufficient_turnaround(scenes, off_t, on_t, min_buffer_weight=1.0):
    """
    Returns True if an intermission occurs between off_t and on_t, OR if the
    sum of scene weights strictly between off_t and on_t is >= min_buffer_weight.
    """
    if on_t <= off_t:
        return False

    # Intermission / Act break lifts the buffer restriction
    if any(scenes[k]["act_break_after"] for k in range(off_t, on_t)):
        return True

    # Sum the weights of the intervening scenes (off_t + 1 through on_t - 1)
    buffer_weight = sum(scenes[k]["weight"] for k in range(off_t + 1, on_t))
    return buffer_weight >= (min_buffer_weight - 1e-9)


def check_validity_and_cost(
    assignment, scenes, num_mics=28, actor_total_scenes=None
):
    """Validates capacity and turnaround buffers; calculates multi-objective cost."""
    num_scenes = len(scenes)
    mic_usage = {m: [None] * num_scenes for m in range(1, num_mics + 1)}

    # Constraint 1 & 2: Capacity & Mutual Exclusivity
    for t, sc in enumerate(scenes):
        for actor in sc["actors"]:
            mic = assignment.get(t, {}).get(actor)
            if not mic:
                return False, float("inf")
            if mic_usage[mic][t] is not None:
                return False, float("inf")
            mic_usage[mic][t] = actor

    # Constraint 3 & 4: Weighted turnaround buffer within an act (Intermission exempt)
    for mic, timeline in mic_usage.items():
        prev_actor = None
        prev_t = -999
        for t in range(num_scenes):
            curr_actor = timeline[t]
            if curr_actor is not None:
                if prev_actor is not None and prev_actor != curr_actor:
                    if not has_sufficient_turnaround(scenes, prev_t, t, min_buffer_weight=1.0):
                        return False, float("inf")
                prev_actor = curr_actor
                prev_t = t

    cost = 0.0

    # Objective B.1 & B.4: Buffer maximization & swap relegation to high packs
    for mic, timeline in mic_usage.items():
        prev_actor = None
        prev_scene_idx = -999

        for t in range(num_scenes):
            curr_actor = timeline[t]
            if curr_actor is not None:
                if prev_actor is not None and prev_actor != curr_actor:
                    # Sum the weights of the scenes strictly between prev_scene_idx and t
                    gap_weight = sum(
                        scenes[k]["weight"] for k in range(prev_scene_idx + 1, t)
                    )
                    buffer_penalty = max(0.0, 4.0 - gap_weight) * 80.0

                    pack_position_weight = ((num_mics - mic + 1) / num_mics) ** 2
                    mic_swap_penalty = 120.0 + (pack_position_weight * 900.0)
                    cost += mic_swap_penalty + buffer_penalty

                prev_actor = curr_actor
                prev_scene_idx = t

            if scenes[t]["act_break_after"]:
                prev_actor = None
                prev_scene_idx = -999

    # Objective B.2: Actor continuity (locking actors to the same pack number)
    actor_active_mics = defaultdict(list)
    for t, sc in enumerate(scenes):
        for actor in sc["actors"]:
            actor_active_mics[actor].append((t, assignment[t][actor]))

    for actor, uses in actor_active_mics.items():
        for i in range(len(uses) - 1):
            m1 = uses[i][1]
            m2 = uses[i + 1][1]
            if m1 != m2:
                cost += 500.0

    # Objective B.1: Priority channel hierarchy (Leads on Mic 1, 2, ...)
    if actor_total_scenes:
        for t, sc in enumerate(scenes):
            for actor in sc["actors"]:
                mic = assignment[t][actor]
                freq = actor_total_scenes[actor]
                cost += (mic - 1) * (freq * 0.45)

    # Objective B.5: Scene-ensemble grouping (keep co-appearing characters clustered)
    for t, sc in enumerate(scenes):
        assigned_mics = [
            assignment[t][actor]
            for actor in sc["actors"]
            if actor in assignment[t]
        ]
        if len(assigned_mics) > 1:
            assigned_mics.sort()
            spread = assigned_mics[-1] - assigned_mics[0]
            cost += (spread / num_mics) * 8.0

    return True, cost


# -----------------------------------------------------------------------------
# 3. Constructive Greedy Initialization
# -----------------------------------------------------------------------------
def generate_initial_assignment(scenes, num_mics=28, actor_total_scenes=None):
    sorted_actors = [a for a, _ in actor_total_scenes.most_common()]
    primary_mic = {
        actor: i + 1 for i, actor in enumerate(sorted_actors[:num_mics])
    }

    assignment = {t: {} for t in range(len(scenes))}
    mic_last_used = {m: -999 for m in range(1, num_mics + 1)}
    mic_last_actor = {m: None for m in range(1, num_mics + 1)}

    for t, sc in enumerate(scenes):
        scene_actors = sorted(
            sc["actors"], key=lambda a: -actor_total_scenes[a]
        )
        assigned_in_scene = set()

        # Step A: Retain primary mic if available and safe
        for actor in scene_actors:
            preferred = primary_mic.get(actor)
            if preferred and preferred not in assigned_in_scene:
                last_t = mic_last_used[preferred]
                last_act = mic_last_actor[preferred]

                is_safe = (
                    last_act == actor
                    or last_act is None
                    or has_sufficient_turnaround(scenes, last_t, t, min_buffer_weight=1.0)
                )

                if is_safe:
                    assignment[t][actor] = preferred
                    assigned_in_scene.add(preferred)

        # Step B: Allocate available channel (preferring higher channels for temporary cast)
        for actor in scene_actors:
            if actor in assignment[t]:
                continue

            is_frequent = actor_total_scenes[actor] >= 3
            candidate_mics = (
                range(1, num_mics + 1)
                if is_frequent
                else range(num_mics, 0, -1)
            )

            best_mic = None
            for m in candidate_mics:
                if m in assigned_in_scene:
                    continue
                last_t = mic_last_used[m]
                last_act = mic_last_actor[m]

                is_safe = (
                    last_act == actor
                    or last_act is None
                    or has_sufficient_turnaround(scenes, last_t, t, min_buffer_weight=1.0)
                )

                if is_safe:
                    best_mic = m
                    break

            if best_mic is not None:
                assignment[t][actor] = best_mic
                assigned_in_scene.add(best_mic)
            else:
                for m in range(1, num_mics + 1):
                    if m not in assigned_in_scene:
                        assignment[t][actor] = m
                        assigned_in_scene.add(m)
                        break

        for actor, m in assignment[t].items():
            mic_last_used[m] = t
            mic_last_actor[m] = actor

    return assignment


# -----------------------------------------------------------------------------
# 4. Multi-Pass Simulated Annealing Optimization
# -----------------------------------------------------------------------------
def optimize_microphone_assignments(
    show_json,
    num_mics=28,
    num_passes=8,
    iterations_per_pass=3500,
    external_gender_map=None,
):
    scenes, actor_gender, actor_all_chars = parse_show_data(
        show_json, external_gender_map=external_gender_map
    )
    actor_total_scenes = Counter()
    for sc in scenes:
        for actor in sc["actors"]:
            actor_total_scenes[actor] += 1

    best_global_assignment = None
    best_global_cost = float("inf")

    for p in range(num_passes):
        current_assignment = generate_initial_assignment(
            scenes, num_mics, actor_total_scenes
        )
        is_valid, current_cost = check_validity_and_cost(
            current_assignment, scenes, num_mics, actor_total_scenes
        )

        temp = 100.0
        cooling = 0.996

        for _ in range(iterations_per_pass):
            t = random.randint(0, len(scenes) - 1)
            actors_in_scene = scenes[t]["actors"]
            if not actors_in_scene:
                continue

            candidate = {
                time: dict(assigns)
                for time, assigns in current_assignment.items()
            }

            if random.random() < 0.45:
                # Perturbation 1: Swap two actors within the same scene
                if len(actors_in_scene) >= 2:
                    a1, a2 = random.sample(actors_in_scene, 2)
                    candidate[t][a1], candidate[t][a2] = (
                        candidate[t][a2],
                        candidate[t][a1],
                    )
            else:
                # Perturbation 2: Reassign actor to an unused channel in this scene
                a = random.choice(actors_in_scene)
                used_mics = set(candidate[t].values())
                available_mics = [
                    m for m in range(1, num_mics + 1) if m not in used_mics
                ]
                if available_mics:
                    candidate[t][a] = random.choice(available_mics)

            cand_valid, cand_cost = check_validity_and_cost(
                candidate, scenes, num_mics, actor_total_scenes
            )

            if cand_valid:
                delta = cand_cost - current_cost
                if delta < 0 or random.random() < (
                    math.exp(-delta / max(temp, 1e-4))
                ):
                    current_assignment = candidate
                    current_cost = cand_cost
                    if current_cost < best_global_cost:
                        best_global_cost = current_cost
                        best_global_assignment = {
                            time: dict(assigns)
                            for time, assigns in candidate.items()
                        }

            temp *= cooling

    return (
        scenes,
        best_global_assignment,
        best_global_cost,
        actor_total_scenes,
        actor_gender,
        actor_all_chars,
    )


# -----------------------------------------------------------------------------
# 5. Usage Lifecycle Modeling (Hold, Stage 'X', & Handoffs)
# -----------------------------------------------------------------------------
def build_actor_stints(scenes, assignment, num_mics):
    """Computes actor wearing intervals per mic.

    Determines when the pack is on stage ('X'), being held between scenes ('-'),
    handed off, or completely unassigned (blank).
    """
    num_scenes = len(scenes)
    actor_stints = []

    for mic in range(1, num_mics + 1):
        timeline_actors = []
        for t in range(num_scenes):
            found_actor = None
            for actor, m in assignment[t].items():
                if m == mic:
                    found_actor = actor
                    break
            timeline_actors.append(found_actor)

        # Segment consecutive usages by the same actor
        stints_for_mic = []
        curr_actor = None
        stint_start = None
        stint_end = None
        active_scenes = set()

        for t in range(num_scenes):
            actor = timeline_actors[t]
            if actor is not None:
                if curr_actor is None:
                    curr_actor = actor
                    stint_start = t
                    stint_end = t
                    active_scenes = {t}
                elif curr_actor == actor:
                    stint_end = t
                    active_scenes.add(t)
                else:
                    stints_for_mic.append({
                        "actor": curr_actor,
                        "start_t": stint_start,
                        "end_t": stint_end,
                        "active_scenes": active_scenes,
                    })
                    curr_actor = actor
                    stint_start = t
                    stint_end = t
                    active_scenes = {t}

        if curr_actor is not None:
            stints_for_mic.append({
                "actor": curr_actor,
                "start_t": stint_start,
                "end_t": stint_end,
                "active_scenes": active_scenes,
            })

        # Calculate de-mic and handoff ranges between stints on the same mic
        for i, s in enumerate(stints_for_mic):
            # Calculate unique characters played by this actor
            all_chars = []
            for t_idx in s["active_scenes"]:
                for c in scenes[t_idx]["actor_chars"].get(s["actor"], []):
                    if c not in all_chars:
                        all_chars.append(c)

            # Determine handoff window
            next_start = (
                stints_for_mic[i + 1]["start_t"]
                if i + 1 < len(stints_for_mic)
                else None
            )

            # Hold range: from start_t to end_t
            # Change window: between end_t and next_start
            change_scenes = (
                list(range(s["end_t"] + 1, next_start))
                if next_start is not None
                else []
            )

            actor_stints.append({
                "mic": mic,
                "actor": s["actor"],
                "characters": ", ".join(all_chars),
                "start_t": s["start_t"],
                "end_t": s["end_t"],
                "active_scenes": s["active_scenes"],
                "change_scenes": change_scenes,
                "next_actor": (
                    stints_for_mic[i + 1]["actor"]
                    if i + 1 < len(stints_for_mic)
                    else None
                ),
            })

    # Sort by mic number, then stint start time
    actor_stints.sort(key=lambda x: (x["mic"], x["start_t"]))
    return actor_stints


# -----------------------------------------------------------------------------
# 6. Version Metadata & Deliverable 1: Master Sound Tracking Sheet (CSV & XLSX)
# -----------------------------------------------------------------------------
def ensure_parent_dir(filepath):
    """Creates parent directories for an output file if needed."""
    parent = os.path.dirname(os.path.abspath(filepath))
    if parent:
        os.makedirs(parent, exist_ok=True)


def format_version_banner(version=None, timestamp=None, note=""):
    """Formats a concise version/timestamp/note subtitle string."""
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = []
    if version:
        parts.append(f"Version {version}")
    parts.append(f"Generated {ts}")
    if note and note.strip():
        parts.append(note.strip())
    return " — ".join(parts)


def apply_workbook_version_metadata(wb, version=None, timestamp=None, note=""):
    """Embeds version metadata into Excel workbook properties and print footers."""
    banner = format_version_banner(version=version, timestamp=timestamp, note=note)
    if version:
        wb.properties.version = str(version)
    wb.properties.description = banner
    if note and note.strip():
        wb.properties.comments = note.strip()

    for ws in wb.worksheets:
        ws.oddFooter.left.text = banner
        ws.oddFooter.right.text = "Page &P of &N"


def export_master_tracking_csv(
    scenes,
    actor_stints,
    num_mics,
    output_filename="mic_tracking_sheet.csv",
    version=None,
    note="",
    timestamp=None,
):
    """Exports master matrix with repeated mic rows for shared channels."""
    ensure_parent_dir(output_filename)
    num_scenes = len(scenes)
    banner = format_version_banner(version=version, timestamp=timestamp, note=note)

    with open(output_filename, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["MASTER THEATRICAL SOUND TRACKING SHEET"])
        writer.writerow([banner])
        writer.writerow([
            "Notation: 'X' = Onstage performing | '-' = Mic held on actor |"
            " [CHANGE] = Mic de-rigging/transfer window | Blank = Mic not with"
            " actor"
        ])
        writer.writerow([])

        # Table headers
        headers = ["Mic #", "Actor Name", "Character(s)"]
        for sc in scenes:
            headers.append(sc["label"])
        writer.writerow(headers)

        for stint in actor_stints:
            row = [
                f"Mic {stint['mic']}",
                stint["actor"],
                stint["characters"] if stint["characters"] else "—",
            ]

            for t in range(num_scenes):
                if t in stint["active_scenes"]:
                    row.append("X")
                elif stint["start_t"] <= t <= stint["end_t"]:
                    row.append("-")
                elif t in stint["change_scenes"]:
                    row.append("[CHANGE]")
                else:
                    row.append("")  # Blank when actor does not have the pack
            writer.writerow(row)

    print(f"Exported Master Tracking CSV: {output_filename}")


def export_master_tracking_excel(
    scenes,
    actor_stints,
    num_mics,
    output_filename="mic_tracking_sheet.xlsx",
    version=None,
    note="",
    timestamp=None,
):
    """Exports master matrix into a styled Excel workbook with odd/even mic row striping."""
    if not OPENPYXL_AVAILABLE:
        return

    ensure_parent_dir(output_filename)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Master Mic Matrix"
    ws.views.sheetView[0].showGridLines = True

    # Palette - Title & Headers
    font_title = Font(name="Calibri", size=14, bold=True, color="1F497D")
    font_subtitle = Font(name="Calibri", size=10, italic=True, color="595959")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(
        start_color="1F497D", end_color="1F497D", fill_type="solid"
    )

    # Base Row Backgrounds (Zebra striping by physical Mic channel)
    fill_odd_row = PatternFill(
        start_color="FFFFFF", end_color="FFFFFF", fill_type="solid"
    )  # Pure White
    fill_even_row = PatternFill(
        start_color="F2F5F9", end_color="F2F5F9", fill_type="solid"
    )  # Subtle Cool Gray/Blue Tint

    # Cell Status Styles
    font_x = Font(name="Calibri", size=11, bold=True, color="002060")
    fill_x = PatternFill(
        start_color="D9E1F2", end_color="D9E1F2", fill_type="solid"
    )  # Soft blue

    font_dash = Font(name="Calibri", size=11, color="595959")
    fill_dash = PatternFill(
        start_color="EBEBEB", end_color="EBEBEB", fill_type="solid"
    )  # Neutral gray

    font_change = Font(name="Calibri", size=10, bold=True, color="C00000")
    fill_change = PatternFill(
        start_color="FCE4D6", end_color="FCE4D6", fill_type="solid"
    )  # Warm peach/orange

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    banner = format_version_banner(version=version, timestamp=timestamp, note=note)
    ws.append(["MASTER MIC TRACKING SHEET"])
    ws.cell(row=1, column=1).font = font_title
    ws.append([banner])
    ws.cell(row=2, column=1).font = font_subtitle

    headers = (
        ["Lav #", "Actor Name", "Character(s)"]
        + [sc["label"] for sc in scenes]
    )
    ws.append(headers)

    header_row = 3
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )

    for r_idx, stint in enumerate(actor_stints, start=header_row + 1):
        row_vals = [
            f"{stint['mic']}",
            stint["actor"],
            stint["characters"] if stint["characters"] else "—",
        ]
        ws.append(row_vals)

        # Determine alternating base fill based on odd vs even physical mic number
        row_base_fill = (
            fill_odd_row if (stint["mic"] % 2 != 0) else fill_even_row
        )

        # Style first 3 metadata columns
        c_mic = ws.cell(row=r_idx, column=1)
        c_mic.alignment = Alignment(horizontal="center", vertical="center")
        c_mic.font = Font(name="Calibri", size=10, bold=True)
        c_mic.fill = row_base_fill
        c_mic.border = thin_border

        c_actor = ws.cell(row=r_idx, column=2)
        c_actor.alignment = Alignment(vertical="center")
        c_actor.fill = row_base_fill
        c_actor.border = thin_border

        c_char = ws.cell(row=r_idx, column=3)
        c_char.alignment = Alignment(vertical="center")
        c_char.fill = row_base_fill
        c_char.border = thin_border

        # Style timeline scene columns
        for t in range(len(scenes)):
            col_pos = t + 4
            cell = ws.cell(row=r_idx, column=col_pos)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center", vertical="center")

            if t in stint["active_scenes"]:
                cell.value = "X"
                cell.font = font_x
                cell.fill = fill_x
            elif stint["start_t"] <= t <= stint["end_t"]:
                cell.value = "-"
                cell.font = font_dash
                cell.fill = fill_dash
            elif t in stint["change_scenes"]:
                cell.value = "-"
                cell.font = font_change
                cell.fill = fill_change
            else:
                cell.value = ""
                # Idle / unassigned scenes adopt the row's alternating odd/even background
                cell.fill = row_base_fill

    for col_idx in range(1, len(headers) + 1):
        col_cells = [
            ws.cell(row=r, column=col_idx)
            for r in range(header_row, ws.max_row + 1)
        ]
        max_len = max(len(str(cell.value or "")) for cell in col_cells)
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 10), 32)

    ws.freeze_panes = "D4"
    apply_workbook_version_metadata(wb, version=version, timestamp=timestamp, note=note)
    wb.save(output_filename)
    print(f"Exported Master Tracking Excel: {output_filename}")


# -----------------------------------------------------------------------------
# 7. Deliverable 2: Backstage Handoff & Quick-Change Cue Sheet
#    (Guys names filled Blue, Girls names filled Pink)
# -----------------------------------------------------------------------------
def extract_backstage_handoffs(scenes, assignment, num_mics, actor_gender):
    """Chronologically orders physical mic swaps for backstage dressers and A2 crew."""
    handoffs = []
    for mic in range(1, num_mics + 1):
        prev_actor = None
        prev_off_t = None

        for t, sc in enumerate(scenes):
            curr_actor = None
            for actor, m in assignment[t].items():
                if m == mic:
                    curr_actor = actor
                    break

            if curr_actor is not None:
                if prev_actor is not None and prev_actor != curr_actor:
                    off_scene = scenes[prev_off_t]
                    on_scene = sc
                    gap = t - prev_off_t - 1

                    has_intermission = any(
                        scenes[k]["act_break_after"]
                        for k in range(prev_off_t, t)
                    )
                    buffer_weight = sum(
                        scenes[k]["weight"] for k in range(prev_off_t + 1, t)
                    )

                    if has_intermission:
                        window_desc = "INTERMISSION BREAK"
                        urgency = "☕ Intermission Swap"
                    elif buffer_weight <= 1.0:
                        buffer_names = " + ".join(
                            scenes[k]["label"]
                            for k in range(prev_off_t + 1, t)
                        )
                        window_desc = f"{gap} Scene(s) ({buffer_names})"
                        urgency = "⚡ QUICK CHANGE"
                    else:
                        window_desc = f"{gap} Scenes Buffer"
                        urgency = f"⏱️ Standard Swap ({gap} scenes)"

                    off_chars = ", ".join(
                        off_scene["actor_chars"].get(prev_actor, [])
                    )
                    on_chars = ", ".join(
                        on_scene["actor_chars"].get(curr_actor, [])
                    )

                    handoffs.append({
                        "mic": mic,
                        "off_scene_idx": prev_off_t,
                        "off_scene_label": off_scene["label"],
                        "off_actor": prev_actor,
                        "off_gender": actor_gender.get(prev_actor, "M"),
                        "off_characters": off_chars,
                        "on_scene_idx": t,
                        "on_scene_label": on_scene["label"],
                        "on_actor": curr_actor,
                        "on_gender": actor_gender.get(curr_actor, "M"),
                        "on_characters": on_chars,
                        "window_desc": window_desc,
                        "urgency": urgency,
                    })

                prev_actor = curr_actor
                prev_off_t = t

    handoffs.sort(
        key=lambda x: (x["on_scene_idx"], x["off_scene_idx"], x["mic"])
    )
    return handoffs

def export_backstage_handoffs_excel(
    handoffs,
    output_filename="backstage_mic_handoffs.xlsx",
    version=None,
    note="",
    timestamp=None,
):
    """
    Exports Backstage Handoff sheet with Blue background fill for Guys' names
    and Pink background fill for Girls' names so tech teams can spot their cues instantly.
    """
    if not OPENPYXL_AVAILABLE:
        return

    ensure_parent_dir(output_filename)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Backstage Handoffs"
    ws.views.sheetView[0].showGridLines = True

    font_title = Font(name="Calibri", size=14, bold=True, color="1F497D")
    font_subtitle = Font(name="Calibri", size=10, italic=True, color="595959")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(
        start_color="1F497D", end_color="1F497D", fill_type="solid"
    )

    # Gender Fills for Actor Names (Blue for Guys, Pink for Girls)
    fill_guy = PatternFill(
        start_color="CFE2F3", end_color="CFE2F3", fill_type="solid"
    )  # Soft Sky Blue
    font_guy = Font(name="Calibri", size=10, bold=True, color="0B3954")

    fill_girl = PatternFill(
        start_color="FADBD8", end_color="FADBD8", fill_type="solid"
    )  # Soft Rose Pink
    font_girl = Font(name="Calibri", size=10, bold=True, color="78281F")

    # Urgency Fills
    fill_quick = PatternFill(
        start_color="FCE4D6", end_color="FCE4D6", fill_type="solid"
    )
    font_quick = Font(name="Calibri", size=10, bold=True, color="C00000")

    fill_intermission = PatternFill(
        start_color="E2EFDA", end_color="E2EFDA", fill_type="solid"
    )
    font_intermission = Font(name="Calibri", size=10, color="276A3C")

    fill_standard = PatternFill(
        start_color="DDEBF7", end_color="DDEBF7", fill_type="solid"
    )
    font_standard = Font(name="Calibri", size=10, color="1F4E78")

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    banner = format_version_banner(version=version, timestamp=timestamp, note=note)
    ws.append(["BACKSTAGE MIC HANDOFFS"])
    ws.cell(row=1, column=1).font = font_title
    ws.append([banner])
    ws.cell(row=2, column=1).font = font_subtitle
    ws.append([])

    headers = [
        "Mic",
        "REMOVE FROM",
        "Remove",
        "ATTACH TO",
        "MUST BE ON BY Scene",
        "Urgency Alert",
    ]
    ws.append(headers)

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=4, column=col_idx)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center")

    if not handoffs:
        ws.append([
            "—",
            "-",
            "No physical mic handoffs required across the production.",
            "—",
            "—",
            "—",
        ])
    else:
        for idx, h in enumerate(handoffs, 1):
            off_name = (
                f"{h['off_actor']} ({h['off_characters']})"
                if h["off_characters"]
                else h["off_actor"]
            )
            on_name = (
                f"{h['on_actor']} ({h['on_characters']})"
                if h["on_characters"]
                else h["on_actor"]
            )
            row_vals = [
                f"Mic {h['mic']}",
                off_name,
                f"After {h['off_scene_label']}",
                on_name,
                f"Before {h['on_scene_label']}",
                h["urgency"],
            ]
            ws.append(row_vals)
            r_num = 4 + idx

            for c_idx in range(1, len(headers) + 1):
                c = ws.cell(row=r_num, column=c_idx)
                c.border = thin_border
                c.alignment = Alignment(vertical="center")

            ws.cell(row=r_num, column=1).alignment = Alignment(
                horizontal="center", vertical="center"
            )
            ws.cell(row=r_num, column=1).font = Font(
                name="Calibri", size=10, bold=True
            )
            ws.cell(row=r_num, column=2).alignment = Alignment(
                horizontal="center", vertical="center"
            )
            ws.cell(row=r_num, column=2).font = Font(
                name="Calibri", size=10
            )

            # Column 3: REMOVE FROM actor cell -> Blue for Guys, Pink for Girls
            off_cell = ws.cell(row=r_num, column=2)
            if h["off_gender"] == "F":
                off_cell.fill = fill_girl
                off_cell.font = font_girl
            else:
                off_cell.fill = fill_guy
                off_cell.font = font_guy

            ws.cell(row=r_num, column=4).alignment = Alignment(
                horizontal="center", vertical="center"
            )

            # Column 5: ATTACH TO actor cell -> Blue for Guys, Pink for Girls
            on_cell = ws.cell(row=r_num, column=4)
            if h["on_gender"] == "F":
                on_cell.fill = fill_girl
                on_cell.font = font_girl
            else:
                on_cell.fill = fill_guy
                on_cell.font = font_guy

            ws.cell(row=r_num, column=6).alignment = Alignment(
                horizontal="center", vertical="center"
            )

            alert_c = ws.cell(row=r_num, column=6)
            if "QUICK CHANGE" in h["urgency"]:
                alert_c.fill = fill_quick
                alert_c.font = font_quick
            elif "Intermission" in h["urgency"]:
                alert_c.fill = fill_intermission
                alert_c.font = font_intermission
            else:
                alert_c.fill = fill_standard
                alert_c.font = font_standard

    for col_idx in range(1, len(headers) + 1):
        col_cells = [
            ws.cell(row=r, column=col_idx)
            for r in range(4, ws.max_row + 1)
        ]
        max_len = max(len(str(cell.value or "")) for cell in col_cells)
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 30)

    ws.freeze_panes = "A5"
    apply_workbook_version_metadata(wb, version=version, timestamp=timestamp, note=note)
    wb.save(output_filename)
    print(f"Exported Backstage Handoffs Excel (Blue/Pink Gender Fills): {output_filename}")


# -----------------------------------------------------------------------------
# 8. Deliverable 3: Top-of-Show / Pre-Show Rigging Preset Sheet
#    Two separate Excel tabs ('Guys' and 'Girls') formatted like the printed rack sheet:
#      Cast Member | Pack # | Tech | Sat | Mon | Tues | Wed | Thurs | Frid | Sat | Sat
# -----------------------------------------------------------------------------
def extract_top_of_show_presets(
    scenes, assignment, num_mics, actor_gender, actor_all_chars
):
    """
    Maps each mic (1..N) to its initial top-of-show wearer (if any), and builds
    complete alphabetical cast rosters for 'Guys' and 'Girls' (including cast
    members who start without a pack at the top of the show, leaving Pack # blank).
    Also flags 'is_dedicated_whole_show' when an actor keeps that same mic pack
    for the entire show without any pack changes or swaps.
    """
    actor_initial_mic = {}
    actor_first_scene_idx = {}
    actors_per_mic = defaultdict(set)
    mics_per_actor = defaultdict(set)

    for t in range(len(scenes)):
        for actor, m in assignment[t].items():
            actors_per_mic[m].add(actor)
            mics_per_actor[actor].add(m)

    for mic in range(1, num_mics + 1):
        for t in range(len(scenes)):
            found_actor = None
            for actor, m in assignment[t].items():
                if m == mic:
                    found_actor = actor
                    break
            if found_actor is not None:
                # Only assign as top-of-show pack if this actor doesn't already have a lower initial mic
                if found_actor not in actor_initial_mic:
                    actor_initial_mic[found_actor] = mic
                    actor_first_scene_idx[found_actor] = t
                break

    guys_list = []
    girls_list = []

    for actor in sorted(actor_all_chars.keys(), key=lambda x: x.lower()):
        gender = actor_gender.get(actor, "M")
        pack_num = actor_initial_mic.get(actor)  # None if they start without a mic
        first_t = actor_first_scene_idx.get(actor)
        chars = ", ".join(actor_all_chars.get(actor, []))

        is_dedicated_whole_show = (
            pack_num is not None
            and len(actors_per_mic[pack_num]) == 1
            and len(mics_per_actor[actor]) == 1
        )

        if pack_num is None:
            status = "No Top-of-Show Pack (Receives Swap Later)"
            entrance_scene = "Later Swap"
        elif is_dedicated_whole_show:
            status = "Dedicated Pack (Whole Show - No Changes)"
            entrance_scene = scenes[first_t]["label"]
        else:
            status = "Shared Pack (Changes During Show)"
            entrance_scene = scenes[first_t]["label"]

        entry = {
            "actor": actor,
            "gender": gender,
            "pack_num": pack_num,
            "first_t": first_t,
            "is_dedicated_whole_show": is_dedicated_whole_show,
            "characters": chars,
            "status": status,
            "entrance_scene": entrance_scene,
        }

        if gender == "F":
            girls_list.append(entry)
        else:
            guys_list.append(entry)

    return guys_list, girls_list

def export_top_of_show_excel(
    guys_list,
    girls_list,
    perf_days,
    output_filename="top_of_show_preset.xlsx",
    version=None,
    note="",
    timestamp=None,
):
    """
    Exports Top-of-Show Preset Excel workbook with TWO sheets ('Guys' and 'Girls'),
    styled after the printed backstage check-off chart:
      - Bright yellow header row with bold black text and full black borders:
        Cast Member | Pack # | Tech | Sat | Mon | Tues | Wed | Thurs | Frid | Sat | Sat
      - Alphabetical cast list (including cast members without an initial pack left blank)
      - Light blue fill on Pack # cells for Curtain-Up packs, white fill for Pre-Rigged packs.
    """
    if not OPENPYXL_AVAILABLE:
        return

    ensure_parent_dir(output_filename)
    wb = openpyxl.Workbook()
    # Remove default sheet; we'll create 'Guys' and 'Girls'
    wb.remove(wb.active)

    # Exact visual styling matching the reference photo
    fill_yellow_header = PatternFill(
        start_color="FFFF00", end_color="FFFF00", fill_type="solid"
    )
    font_header_main = Font(name="Calibri", size=14, bold=True, color="111111")
    font_header_day = Font(name="Calibri", size=11, bold=True, color="111111")

    font_actor = Font(name="Calibri", size=12, bold=True, color="111111")
    font_pack = Font(name="Calibri", size=12, bold=True, color="111111")

    fill_guy_dedicated_pack = PatternFill(
        start_color="CFE2F3", end_color="CFE2F3", fill_type="solid"
    )  # Soft sky blue when a guy keeps the pack the whole show without changes
    fill_girl_dedicated_pack = PatternFill(
        start_color="FADBD8", end_color="FADBD8", fill_type="solid"
    )  # Soft rose pink when a girl keeps the pack the whole show without changes
    fill_white = PatternFill(
        start_color="FFFFFF", end_color="FFFFFF", fill_type="solid"
    )

    black_thin = Side(style="thin", color="000000")
    black_medium = Side(style="medium", color="000000")

    grid_border = Border(
        left=black_thin,
        right=black_thin,
        top=black_thin,
        bottom=black_thin,
    )
    header_border = Border(
        left=black_thin,
        right=black_thin,
        top=black_medium,
        bottom=black_medium,
    )

    headers = ["Cast Member", "Pack #"] + list(perf_days)

    for sheet_name, roster in [("Guys", guys_list), ("Girls", girls_list)]:
        ws = wb.create_sheet(title=sheet_name)
        ws.views.sheetView[0].showGridLines = True

        # Page setup for clean landscape printing
        ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
        ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 1

        # Header Row (Row 1)
        ws.append(headers)
        ws.row_dimensions[1].height = 28

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = fill_yellow_header
            cell.border = header_border
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.font = (
                font_header_main if col_idx <= 2 else font_header_day
            )

        # Data Rows
        for r_offset, entry in enumerate(roster):
            row_num = 2 + r_offset
            ws.row_dimensions[row_num].height = 22

            pack_val = entry["pack_num"] if entry["pack_num"] is not None else ""
            row_vals = [entry["actor"], pack_val] + [""] * len(perf_days)
            ws.append(row_vals)

            # Col 1: Cast Member
            c_actor = ws.cell(row=row_num, column=1)
            c_actor.font = font_actor
            c_actor.fill = fill_white
            c_actor.border = grid_border
            c_actor.alignment = Alignment(horizontal="left", vertical="center", indent=1)

            # Col 2: Pack #
            c_pack = ws.cell(row=row_num, column=2)
            c_pack.font = font_pack
            c_pack.border = grid_border
            c_pack.alignment = Alignment(horizontal="center", vertical="center")
            # Shade blue if a guy keeps the pack the whole show without changes,
            # pink if a girl keeps the pack the whole show without changes, white otherwise
            if entry["pack_num"] is not None and entry.get("is_dedicated_whole_show"):
                if entry["gender"] == "F":
                    c_pack.fill = fill_girl_dedicated_pack
                else:
                    c_pack.fill = fill_guy_dedicated_pack
            else:
                c_pack.fill = fill_white

            # Cols 3..N: Performance check-off boxes
            for day_idx in range(len(perf_days)):
                c_day = ws.cell(row=row_num, column=3 + day_idx)
                c_day.fill = fill_white
                c_day.border = grid_border
                c_day.alignment = Alignment(horizontal="center", vertical="center")

        # Column widths matching the printed sheet proportions
        max_name_len = max([len(e["actor"]) for e in roster] + [16])
        ws.column_dimensions["A"].width = max(max_name_len + 6, 24)
        ws.column_dimensions["B"].width = 13
        for day_idx in range(len(perf_days)):
            col_letter = get_column_letter(3 + day_idx)
            ws.column_dimensions[col_letter].width = 7.5

        ws.freeze_panes = "C2"

    apply_workbook_version_metadata(wb, version=version, timestamp=timestamp, note=note)
    wb.save(output_filename)
    print(f"Exported Top-of-Show Preset Excel with 'Guys' & 'Girls' tabs: {output_filename}")


# -----------------------------------------------------------------------------
# 9. CLI Interface
# -----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Theatrical Wireless Microphone Allocation Optimizer."
    )
    parser.add_argument(
        "json_file",
        nargs="?",
        default="show_data.json",
        help="Path to show input JSON file",
    )
    parser.add_argument(
        "--cast-file",
        default=None,
        help="Optional JSON file mapping actor names to gender ('M'/'F' or 'guys'/'girls')",
    )
    parser.add_argument(
        "--mics",
        type=int,
        default=28,
        help="Number of available wireless mic packs (default: 28)",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=8,
        help="Number of optimization restarts (default: 8)",
    )
    parser.add_argument(
        "--iters",
        type=int,
        default=3500,
        help="Iterations per annealing pass (default: 3500)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for reproducible simulated annealing results",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Optional build version label (e.g., 'v4') embedded in output subtitles & metadata",
    )
    parser.add_argument(
        "--note",
        default="",
        help="Optional revision note embedded alongside version metadata",
    )
    parser.add_argument(
        "--perf-days",
        default="Tech,Sat,Mon,Tues,Wed,Thurs,Frid,Sat,Sat",
        help="Comma-separated performance columns for the Top-of-Show check-off sheets",
    )
    parser.add_argument(
        "--output",
        default="mic_tracking_sheet.csv",
        help="Master tracking CSV path",
    )
    parser.add_argument(
        "--output-xlsx",
        default="mic_tracking_sheet.xlsx",
        help="Master tracking Excel path",
    )
    parser.add_argument(
        "--top-xlsx",
        default="top_of_show_preset.xlsx",
        help="Pre-show preset Excel path (with 'Guys' and 'Girls' tabs)",
    )
    parser.add_argument(
        "--handoffs-xlsx",
        default="backstage_mic_handoffs.xlsx",
        help="Backstage quick-change Excel path (with Blue/Pink actor fills)",
    )

    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    run_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        with open(args.json_file, "r", encoding="utf-8") as f:
            show_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: JSON file '{args.json_file}' not found.")
        sys.exit(1)

    external_gender_map = load_cast_gender_file(args.cast_file)
    perf_days = [d.strip() for d in args.perf_days.split(",") if d.strip()]

    version_tag = f" [{args.version}]" if args.version else ""
    print(
        f"Loaded '{args.json_file}'{version_tag}. Optimizing for {args.mics} mic packs across"
        f" {args.passes} passes ({args.iters} iters/pass)..."
    )
    (
        scenes,
        best_assignment,
        cost,
        actor_freq,
        actor_gender,
        actor_all_chars,
    ) = optimize_microphone_assignments(
        show_data,
        num_mics=args.mics,
        num_passes=args.passes,
        iterations_per_pass=args.iters,
        external_gender_map=external_gender_map,
    )

    if not best_assignment:
        print(
            "Error: Could not find a feasible assignment satisfying"
            " hard turnaround constraints."
        )
        sys.exit(1)

    # Deliverable 1: Master Tracking Sheet
    stints = build_actor_stints(scenes, best_assignment, args.mics)
    export_master_tracking_csv(
        scenes,
        stints,
        args.mics,
        args.output,
        version=args.version,
        note=args.note,
        timestamp=run_timestamp,
    )
    if OPENPYXL_AVAILABLE:
        export_master_tracking_excel(
            scenes,
            stints,
            args.mics,
            args.output_xlsx,
            version=args.version,
            note=args.note,
            timestamp=run_timestamp,
        )

    # Deliverable 2: Backstage Handoffs & Quick Changes (Blue = Guys, Pink = Girls)
    handoffs = extract_backstage_handoffs(
        scenes, best_assignment, args.mics, actor_gender
    )
    if OPENPYXL_AVAILABLE:
        export_backstage_handoffs_excel(
            handoffs,
            args.handoffs_xlsx,
            version=args.version,
            note=args.note,
            timestamp=run_timestamp,
        )

    # Deliverable 3: Top-of-Show / Pre-Show Presets ('Guys' and 'Girls' sheets)
    guys_list, girls_list = extract_top_of_show_presets(
        scenes, best_assignment, args.mics, actor_gender, actor_all_chars
    )
    if OPENPYXL_AVAILABLE:
        export_top_of_show_excel(
            guys_list,
            girls_list,
            perf_days,
            args.top_xlsx,
            version=args.version,
            note=args.note,
            timestamp=run_timestamp,
        )

    if not OPENPYXL_AVAILABLE:
        print(
            "Warning: openpyxl is not installed; skipped .xlsx exports. "
            "Install it with 'pip install openpyxl' to generate Excel workbooks."
        )

    print(
        f"Finished optimization. Minimized objective cost: {cost:.2f}. All"
        " operational paperwork generated."
    )


if __name__ == "__main__":
    main()
