"""
Theatrical Lav Solver & Microphone Allocation Optimizer
Solves resource-constrained wireless microphone assignments and produces operational paperwork:
  1. Master Sound Tracking Sheet (Matrix with 'X', '-', blank, and split shared rows)
  2. Backstage Quick-Change & Handoff Cue Sheet (A2 / wardrobe dressing cue list)
  3. Top-of-Show / Pre-Show Rigging Preset Sheet (Channels 1 to N pre-show call)
"""

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
import random
import sys

try:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False


# -----------------------------------------------------------------------------
# 1. Parsing & Show Timeline Construction
# -----------------------------------------------------------------------------
def parse_show_data(show_json):
    """Parses hierarchical JSON into an ordered timeline with act-break boundary tags."""
    scenes = []
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

            # Support both standard list format and dict format with metadata
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
    return scenes


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
    show_json, num_mics=28, num_passes=8, iterations_per_pass=3500
):
    scenes = parse_show_data(show_json)
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

    return scenes, best_global_assignment, best_global_cost, actor_total_scenes


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
# 6. Deliverable 1: Master Sound Tracking Sheet (CSV & Styled XLSX)
# -----------------------------------------------------------------------------
def export_master_tracking_csv(
    scenes, actor_stints, num_mics, output_filename="mic_tracking_sheet.csv"
):
    """Exports master matrix with repeated mic rows for shared channels."""
    num_scenes = len(scenes)

    with open(output_filename, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["MASTER THEATRICAL SOUND TRACKING SHEET"])
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
    scenes, actor_stints, num_mics, output_filename="mic_tracking_sheet.xlsx"
):
    """Exports master matrix into a styled Excel workbook with visual color indicators

    and alternating row zebra-striping by physical mic number (odd vs even).
    """
    if not OPENPYXL_AVAILABLE:
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Master Mic Matrix"
    ws.views.sheetView[0].showGridLines = True

    # Palette - Headers
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

    headers = (
        ["Lav #", "Actor Name", "Character(s)"]
        + [sc["label"] for sc in scenes]
    )
    ws.append(headers)

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )

    for r_idx, stint in enumerate(actor_stints, start=2):
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

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 10), 32)

    ws.freeze_panes = "D2"
    wb.save(output_filename)
    print(f"Exported Master Tracking Excel: {output_filename}")

# -----------------------------------------------------------------------------
# 7. Deliverable 2: Backstage Handoff & Quick-Change Cue Sheet
# -----------------------------------------------------------------------------
def extract_backstage_handoffs(scenes, assignment, num_mics):
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

                    # if has_intermission:
                    #     window_desc = "INTERMISSION BREAK"
                    #     urgency = "☕ Intermission Swap"
                    # elif gap == 1:
                    #     buffer_sc = scenes[prev_off_t + 1]["label"]
                    #     window_desc = f"1 Scene Buffer ({buffer_sc})"
                    #     urgency = "⚡ QUICK CHANGE"
                    # else:
                    #     window_desc = f"{gap} Scenes Buffer"
                    #     urgency = f"⏱️ Standard Swap ({gap} scenes)"
                    buffer_weight = sum(scenes[k]["weight"] for k in range(prev_off_t + 1, t))
                    if has_intermission:
                        window_desc = "INTERMISSION BREAK"
                        urgency = "☕ Intermission Swap"
                    elif buffer_weight <= 1.0:
                        buffer_names = " + ".join(scenes[k]["label"] for k in range(prev_off_t + 1, t))
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
                        "off_characters": off_chars,
                        "on_scene_idx": t,
                        "on_scene_label": on_scene["label"],
                        "on_actor": curr_actor,
                        "on_characters": on_chars,
                        "window_desc": window_desc,
                        "urgency": urgency,
                    })

                prev_actor = curr_actor
                prev_off_t = t

    handoffs.sort(
        key=lambda x: (x["off_scene_idx"], x["on_scene_idx"], x["mic"])
    )
    return handoffs

def export_backstage_handoffs_excel(
    handoffs, output_filename="backstage_mic_handoffs.xlsx"
):
    if not OPENPYXL_AVAILABLE:
        return

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

    ws.append(["BACKSTAGE MIC HANDOFFS"])
    ws.cell(row=1, column=1).font = font_title
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
        ws.cell(row=r_num, column=3).alignment = Alignment(
            horizontal="center", vertical="center"
        )
        ws.cell(row=r_num, column=5).alignment = Alignment(
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

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 28)

    ws.freeze_panes = "A5"
    wb.save(output_filename)
    print(f"Exported Backstage Handoffs Excel: {output_filename}")


# -----------------------------------------------------------------------------
# 8. Deliverable 3: Top-of-Show / Pre-Show Rigging Preset Sheet
# -----------------------------------------------------------------------------
def extract_top_of_show_presets(scenes, assignment, num_mics):
    """Generates pre-show rigging assignments for Channels 1 through N."""
    presets = []

    for mic in range(1, num_mics + 1):
        first_t = None
        first_actor = None

        for t in range(len(scenes)):
            for actor, m in assignment[t].items():
                if m == mic:
                    first_t = t
                    first_actor = actor
                    break
            if first_t is not None:
                break

        if first_t is None:
            presets.append({
                "mic": mic,
                "actor": "—",
                "character": "—",
                "status": "Spare / Idle on Rack",
                "entrance_scene": "None",
            })
        else:
            sc = scenes[first_t]
            char_list = sc["actor_chars"].get(first_actor, [])
            status_desc = (
                "Active at Curtain Up"
                if first_t == 0
                else f"Pre-rigged (Enters {sc['label']})"
            )
            presets.append({
                "mic": mic,
                "actor": first_actor,
                "character": ", ".join(char_list) if char_list else "—",
                "status": status_desc,
                "entrance_scene": sc["label"],
            })

    return presets


def export_top_of_show_csv(
    presets, output_filename="top_of_show_preset.csv"
):
    with open(output_filename, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["TOP-OF-SHOW / PRE-SHOW RIGGING PRESET SHEET"])
        writer.writerow([
            "Physical Channel Rigging Status Before House Opens (Mics 1 to N)"
        ])
        writer.writerow([])
        writer.writerow([
            "Mic Channel",
            "Actor's Name",
            "Character(s)",
            "Top-of-Show Status",
            "First Entrance Scene",
            "Checklist",
        ])

        for p in presets:
            writer.writerow([
                f"Mic {p['mic']}",
                p["actor"],
                p["character"],
                p["status"],
                p["entrance_scene"],
                "[ ] Mic Rigged  [ ] Line Check",
            ])

    print(f"Exported Top-of-Show Preset CSV: {output_filename}")


def export_top_of_show_excel(
    presets, output_filename="top_of_show_preset.xlsx"
):
    if not OPENPYXL_AVAILABLE:
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Top of Show Preset"
    ws.views.sheetView[0].showGridLines = True

    font_title = Font(name="Calibri", size=14, bold=True, color="1F497D")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(
        start_color="1F497D", end_color="1F497D", fill_type="solid"
    )

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    ws.append(["TOP-OF-SHOW / PRE-SHOW RIGGING PRESET SHEET"])
    ws.cell(row=1, column=1).font = font_title
    ws.append([])

    headers = [
        "Mic Channel",
        "Actor's Name",
        "Character(s)",
        "Top-of-Show Status",
        "First Entrance Scene",
        "Pre-Show Checklist",
    ]
    ws.append(headers)

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=3, column=col_idx)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for idx, p in enumerate(presets, 1):
        row_vals = [
            f"Mic {p['mic']}",
            p["actor"],
            p["character"],
            p["status"],
            p["entrance_scene"],
            "[ ] Rigged  [ ] Line Check",
        ]
        ws.append(row_vals)
        r_num = 3 + idx

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

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 14), 38)

    ws.freeze_panes = "A4"
    wb.save(output_filename)
    print(f"Exported Top-of-Show Preset Excel: {output_filename}")


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
        "--top-csv",
        default="top_of_show_preset.csv",
        help="Pre-show preset CSV path",
    )
    parser.add_argument(
        "--top-xlsx",
        default="top_of_show_preset.xlsx",
        help="Pre-show preset Excel path",
    )
    parser.add_argument(
        "--handoffs-csv",
        default="backstage_mic_handoffs.csv",
        help="Backstage quick-change CSV path",
    )
    parser.add_argument(
        "--handoffs-xlsx",
        default="backstage_mic_handoffs.xlsx",
        help="Backstage quick-change Excel path",
    )

    args = parser.parse_args()

    try:
        with open(args.json_file, "r", encoding="utf-8") as f:
            show_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: JSON file '{args.json_file}' not found.")
        sys.exit(1)

    print(
        f"Loaded '{args.json_file}'. Optimizing for {args.mics} mic packs across"
        f" {args.passes} passes ({args.iters} iters/pass)..."
    )
    scenes, best_assignment, cost, actor_freq = (
        optimize_microphone_assignments(
            show_data,
            num_mics=args.mics,
            num_passes=args.passes,
            iterations_per_pass=args.iters,
        )
    )

    if not best_assignment:
        print(
            "Error: Could not find a feasible assignment satisfying"
            " hard turnaround constraints."
        )
        sys.exit(1)

    # Deliverable 1: Master Tracking Sheet (split shared rows, 'X', '-', change window)
    stints = build_actor_stints(scenes, best_assignment, args.mics)
    export_master_tracking_csv(scenes, stints, args.mics, args.output)
    if OPENPYXL_AVAILABLE:
        export_master_tracking_excel(
            scenes, stints, args.mics, args.output_xlsx
        )

    # Deliverable 2: Backstage Handoffs & Quick Changes
    handoffs = extract_backstage_handoffs(scenes, best_assignment, args.mics)
    if OPENPYXL_AVAILABLE:
        export_backstage_handoffs_excel(handoffs, args.handoffs_xlsx)

    # Deliverable 3: Top-of-Show / Pre-Show Presets (Channels 1 to N)
    presets = extract_top_of_show_presets(scenes, best_assignment, args.mics)
    #export_top_of_show_csv(presets, args.top_csv)
    if OPENPYXL_AVAILABLE:
        export_top_of_show_excel(presets, args.top_xlsx)

    print(
        f"Finished optimization. Minimized objective cost: {cost:.2f}. All"
        " operational paperwork generated."
    )


if __name__ == "__main__":
    main()