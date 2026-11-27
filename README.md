# Lav Chart — Theatrical Wireless Microphone Allocation & Show Pipeline

**Lav Chart** is an end-to-end theatrical sound design and show-control toolkit built for sound designers, A2s, board operators, and backstage wardrobe/audio crews. When a production has more performers than available wireless microphone packs (e.g., 38+ actors sharing 28 wireless packs), the pipeline solves the constrained scheduling problem, generates print-ready FOH and backstage paperwork, exports ready-to-load **Behringer WING** show files with dynamic custom fader layers, and annotates Markdown scripts with scene-accurate microphone numbers.

---

## Pipeline Overview

1. **Stage 1 — Wireless Microphone Allocation Optimizer (`optimize_mics.py`):** Uses a frequency-aware greedy constructor and multi-pass simulated annealing to assign wireless channels across every act and scene—minimizing physical pack swaps, maximizing dressing turnaround buffers, locking leads onto dedicated lower channels, and exporting the master `.csv` plus three styled `.xlsx` operational workbooks.


2. **Stage 2 — Behringer WING Show & Snippet Generator (`csv_to_wing_show.py`):** Converts the master tracking chart and show breakdown into a native Behringer WING / Wing-Edit `showfile.1` (`.show`) playlist and sequential `.snip` files with scene-specific character scribble strips, color-coded stage status, and dynamic **USER 1** custom fader banks.


3. **Stage 3 — Markdown Script Mic Annotator (`annotate_script_mics.py`):** Cross-references the master tracking chart with a Markdown script (`.md`) to append each character's scene-specific wireless channel number to their **bolded** dialogue cues (e.g., `**Protagonist**` $\rightarrow$ `**Protagonist (1)**`).



---

## Requirements & Installation

* **Python 3.8+** (`annotate_script_mics.py` runs on **Python 3.7+** using only the Python Standard Library—`argparse`, `csv`, `re`, `collections`—with zero external packages required).


* **`openpyxl`** (required for styled, color-coded `.xlsx` spreadsheet exports; `.csv` files use Python's built-in `csv` module):



```bash
pip install openpyxl

```

---

## Input File Formats

### 1. Show Breakdown (`show_data.json`)

Defines your production's cast-by-scene breakdown hierarchically by Act (`act_1`, `act_2`, ...) and Scene (`scene_1`, `scene_2`, ...):

* **Standard Scene:** A list of cast objects (`associated_actor` and `character_name`), defaulting to a scene turnaround weight of `$1.0$`.


* **Quick / Short Scene:** A dictionary with `"quick_scene": true` (assigning a turnaround weight of `$0.5$`, or a custom numeric `"weight"`) and a `"cast"` array so rapid transitions are never treated as sufficient time for a single-scene physical pack transfer.


* **Optional Gender Tag:** Add `"gender": "M"` or `"gender": "F"` (also accepts `"guy"`/`"girl"` or `"male"`/`"female"`) once per actor; the parser remembers it across the entire production and auto-infers any untagged cast members from character titles and common first names.



```json
{
  "act_1": {
    "scene_1": [
      {
        "character_name": "Protagonist",
        "associated_actor": "Alex Rivera",
        "gender": "M"
      },
      {
        "character_name": "Narrator",
        "associated_actor": "Jordan Taylor",
        "gender": "M"
      },
      {
        "character_name": "Chorus A",
        "associated_actor": "Samantha Patel",
        "gender": "F"
      }
    ],
    "scene_2": [
      {
        "character_name": "Protagonist",
        "associated_actor": "Alex Rivera"
      },
      {
        "character_name": "Mentor",
        "associated_actor": "Mike Smith",
        "gender": "M"
      }
    ],
    "scene_3": {
      "quick_scene": true,
      "cast": [
        {
          "character_name": "Child A",
          "associated_actor": "Riley Chen",
          "gender": "F"
        },
        {
          "character_name": "Child B",
          "associated_actor": "Taylor Kim",
          "gender": "M"
        },
        {
          "character_name": "Apprentice",
          "associated_actor": "Jamie Ortiz",
          "gender": "M"
        }
      ]
    }
  },
  "act_2": {
    "scene_1": [
      {
        "character_name": "Protagonist",
        "associated_actor": "Alex Rivera"
      }
    ]
  }
}

```

### 2. Cast Roster / Gender Mapping (`cast_gender.json` — *Optional*)

If you prefer to keep gender classifications separate from `show_data.json`, pass an explicit JSON mapping via `--cast-file` (supports either `"guys"`/`"girls"` lists or individual `"Actor Name": "M"/"F"` pairs):

```json
{
  "guys": ["Alex Rivera", "Jordan Taylor", "Mike Smith"],
  "girls": ["Sam Patel", "Casey Brooks", "Riley Chen"]
}

```

### 3. Custom Fader Layer Grouping (`layer_groups.json` — *Optional*)

Controls the left-to-right fader ordering on the Behringer WING's custom **USER 1** layers so families, groups and ensembles stay grouped together on adjacent faders:

```json
{
  "group_order": {
    "Leads": 1,
    "Principal Family": 10,
    "Villagers": 20,
    "Chorus": 30
  },
  "character_to_group": {
    "Protagonist": "Leads",
    "Mentor": "Principal Family",
    "Apprentice": "Principal Family",
    "Villager 1": "Villagers",
    "Villager 2": "Villagers",
    "Chorus A": "Chorus",
    "Chorus B": "Chorus"
  }
}

```

### 4. Markdown Script (`script.md` — *Optional, for Script Annotation*)

A Markdown script containing Act/Scene headings (`# Act 1`, `## Scene 2`, `### Act 1 - Scene 2`) and **bolded** character cues:

```markdown
## Act 1 - Scene 2

**Narrator**: The curtain rises on the town square just before dawn.

**Protagonist:** We must make haste before the clock strikes noon.

**Mentor**: Right behind you—let's gather the rest of the company!

```

---

## Stage 1: Wireless Microphone Allocation Optimizer (`optimize_mics.py`)

### Scheduling Rules & Optimization Model

#### Hard Constraints (Strictly Enforced)

1. **Capacity:** Every actor onstage in a scene is assigned exactly one wireless microphone pack.


2. **Mutual Exclusivity:** No two actors can share the same microphone pack in the same scene.


3. **Weighted Turnaround Buffer Within an Act:** When a pack changes from one actor to another within the same act, the sum of scene weights strictly between the outgoing scene and incoming scene must be `$\ge 1.0$`. One full scene (`$1.0$`) satisfies this requirement, whereas a single quick scene (`$0.5$`) is too short on its own and requires at least two quick scenes (or one quick scene plus one full scene).


4. **Intermission Exemption:** Act transitions (`act_1` `$\rightarrow$` `act_2`) automatically lift the turnaround buffer restriction, allowing immediate pack handoffs across intermission.



#### Optimization Objectives (Cost Minimization)

1. **Actor Hierarchy:** Actors with the highest cumulative scene counts across the production are prioritized for lower channel numbers (`Mic 1`, `Mic 2`, ...).


2. **Pack Continuity:** Applies a high penalty (`$500.0$`) if an actor is moved to a different pack number during the show, keeping performers locked to a single channel whenever possible.


3. **Turnaround Buffer Maximization:** Penalizes tight swaps (`$< 4.0$` cumulative scene weight) so the solver actively prefers wider breathing room for backstage dressers.


4. **Quadratic Swap Relegation:** Pack transfers on low channels (`Mic 1`–`Mic 5`) incur steep quadratic penalties, pushing unavoidable swaps onto the highest-numbered swing channels (`Mic 20`–`Mic 28`).


5. **Scene-Ensemble Clustering:** Rewards grouping characters who appear together in the same scene onto adjacent channel numbers.



#### Algorithmic Pipeline

1. **Frequency Ranking:** Parses acts and scenes in natural numerical order and ranks performers by total scene appearances.


2. **Greedy Constructive Seeding:** Assigns top-ranked performers to primary channels while validating weighted turnaround buffers and act breaks.


3. **Multi-Pass Simulated Annealing:** Runs `$P$` independent passes (default: `$8$`) of `$I$` iterations each (default: `$3500$`) using a geometric cooling schedule (`$T \leftarrow T \times 0.996$`), testing intra-scene actor swaps and open-channel reassignments to retain the lowest-cost valid schedule.



---

### Generated Operational Deliverables

Running `optimize_mics.py` produces the master `.csv` tracking sheet (used by Stages 2 and 3) plus three styled Excel (`.xlsx`) operational deliverables:

#### 1. Master Sound Tracking Sheet (`mic_tracking_sheet.csv` / `.xlsx`)

Designed for the Front-of-House (FOH) sound engineer:

* **Split Shared Rows:** Each actor stint on a shared mic receives its own dedicated row (`Mic # | Actor Name | Character(s)`).


* **Stage & Hold Notation:**
* **`X`** (Soft Blue fill): Actor is physically onstage performing in the scene.


* **`-`** (Neutral Gray fill): Actor is offstage but still wearing/holding the pack between appearances.


* **`[CHANGE]` / `-**` (Warm Peach/Red fill): Handoff buffer window where the pack is de-rigged, sanitized, and transferred to the next actor.


* **Blank:** Pack is not on that actor.




* **Zebra Striping:** Alternating row shading is tied to odd vs. even physical mic numbers so split rows belonging to the same pack stay visually grouped.



#### 2. Backstage Handoff & Quick-Change Cue Sheet (`backstage_mic_handoffs.xlsx`)

Designed for A2s and backstage wardrobe dressers:

* Sorted chronologically by the exact end-of-scene cue when a pack comes off an actor.


* Displays **`REMOVE FROM`**, **`Remove`**, **`ATTACH TO`**, **`MUST BE ON BY Scene`**, and a **`Urgency Alert`** (`⚡ QUICK CHANGE`, `⏱️ Standard Swap`, `☕ Intermission Swap`)


* **Gender Color Coding:** Actor cells are shaded **Sky Blue** for Guys and **Rose Pink** for Girls so each tech crew can spot their handoffs immediately.



#### 3. Top-of-Show / Pre-Show Rigging Preset (`top_of_show_preset.xlsx`)

Designed for half-hour pre-show rigging and line checks:

* Creates two separate Excel tabs (**`Guys`** and **`Girls`**) styled with a yellow header bar (`Cast Member | Pack # | Tech | Sat | Mon | Tues | Wed | Thurs | Frid | Sat | Sat`).


* Lists every cast member in that group alphabetically; cast members starting without a pack at the top of the show have their `Pack #` cell left blank.


* **Dedicated Pack Highlighting:** If an actor keeps their assigned pack for the **entire show without any changes**, their `Pack #` cell is shaded **Blue** on the `Guys` sheet and **Pink** on the `Girls` sheet. Shared packs (and unassigned top-of-show cast) remain white.



---

### Usage & CLI Options (`optimize_mics.py`)

```bash
python optimize_mics.py show_data.json \
  --cast-file cast_gender.json \
  --mics 28 \
  --passes 8 \
  --iters 3500 \
  --perf-days "Tech,Sat,Mon,Tues,Wed,Thurs,Frid,Sat,Sat"

```

| Flag | Default | Description |
| --- | --- | --- |
| `json_file` | `show_data.json` | Path to the input show breakdown JSON file.

 |
| `--cast-file` | `None` | Optional JSON file mapping actors to gender (`M`/`F` or `guys`/`girls`).

 |
| `--mics` | `28` | Total number of available wireless microphone packs.

 |
| `--passes` | `8` | Number of independent simulated annealing restarts.

 |
| `--iters` | `3500` | Optimization iterations per annealing pass. |
| `--seed` | `None` | Optional RNG seed for reproducible simulated annealing runs. |
| `--version` | `None` | Optional build version label (e.g., `v4`) embedded in subtitles, print footers, and workbook metadata. |
| `--note` | `""` | Optional revision note embedded alongside version metadata. |
| `--perf-days` | `Tech,Sat,Mon,Tues,Wed,Thurs,Frid,Sat,Sat` | Comma-separated performance check-off columns for the Top-of-Show sheets. |
| `--output` | `mic_tracking_sheet.csv` | Output path for the Master Sound Tracking CSV. |
| `--output-xlsx` | `mic_tracking_sheet.xlsx` | Output path for the Master Sound Tracking Excel workbook. |
| `--top-xlsx` | `top_of_show_preset.xlsx` | Output path for the Top-of-Show Preset Excel workbook (`Guys` & `Girls` tabs). |
| `--handoffs-xlsx` | `backstage_mic_handoffs.xlsx` | Output path for the Backstage Handoffs Excel workbook. |

---

## Stage 2: Behringer WING Show & Snippet Generator (`csv_to_wing_show.py`)

Converts `mic_tracking_sheet.csv` and `show_data.json` into a USB-ready folder containing a native Behringer WING / Wing-Edit `showfile.1` (`.show`) playlist and sequential `.snip` files (`00_SOUNDCHECK.snip` plus every scene in the show, including placeholder scenes where no packs swap).

### Console Features & Architecture

* **Safe Snippet Recall (`ae_data.ch`):** Only updates channel scribble strip names and colors—leaving preamp gains, routing, EQ, compression, and bus sends untouched.


* **Scene-Specific Character Labels:** Cross-references `show_data.json` so multi-role actors display the exact character they are playing in that specific scene.


* **Pre-Rigged Top-of-Show Lookahead:** Eliminates `"Idle"` labels in early scenes by pre-populating packs with the character of the first actor scheduled to wear them, and looks ahead to the next entrance while an actor holds a pack offstage (`-`).


* **Color-Coded Scribble Strips:**
* **Cyan (`6`):** `00_SOUNDCHECK.snip` line-check preset.


* **Green (`4`):** Actor is onstage (`X`) in the current scene.


* **Blue (`8`):** Actor is holding the pack offstage (`-`) or pre-rigged before entrance.


* **Yellow (`3`):** Pack is actively swapping backstage (`[CHANGE]`) or just swapped onto a new actor.




* **Dynamic Custom Fader Layers (`ce_data.layer`):**
* Automatically selects and populates **Left USER 1** (Layer `6`) with only the active onstage actors (`X`) in the current scene (up to 12 channels), spilling overflow actors (`13–24`) onto **Center USER 1** (Layer `5`) and blanking unused slots (`"type": "OFF"`).


* Supports custom family/ensemble grouping via `layer_groups.json` (keeping ensembles together, and sorting numbered roles `1–6` and lettered roles `A–E` sequentially).





### Usage & CLI Options (`csv_to_wing_show.py`)

```bash
python csv_to_wing_show.py mic_tracking_sheet.csv show_data.json \
  --outdir WING_SHOW \
  --show-name MY_SHOW \
  --style character \
  --group-config layer_groups.json

```

| Flag | Default | Description |
| --- | --- | --- |
| `csv_file` | `mic_tracking_sheet.csv` | Path to the master tracking CSV generated by Stage 1.

 |
| `json_file` | `show_data.json` | Path to `show_data.json` for scene-specific character names.

 |
| `--outdir` | `WING_SHOW` | Output directory containing the `.show` file and all `.snip` files.

 |
| `--show-name` | `SHOW_LAVS` | Filename for the generated `.show` file.

 |
| `--style` | `character` | Scribble strip format: `character`, `char_actor`, `actor_first`, or `actor_full`.

 |
| `--group-config` | `layer_groups.json` | Optional JSON file defining custom fader group ordering.

 |
| `--init-snap` | `None` | Optional base console snapshot (e.g., `init.snap`) to place as Scene 1. |
| `--version` | `None` | Optional build version label (e.g., `v4`) appended to the `.show` filename and embedded in `00_SOUNDCHECK` cue info. |
| `--note` | `""` | Optional revision note embedded in `00_SOUNDCHECK` cue info. |

### Loading onto the Behringer WING

1. Copy the entire `WING_SHOW` folder (keeping the `.show` file and all `.snip` files together in the same directory) onto a FAT32 USB drive—or transfer directly to the WING's internal storage using **USB Disk Mode** (`SETUP` $\rightarrow$ `GENERAL` $\rightarrow$ `DISK`).


2. Press **LIBRARY** on the WING surface, navigate to the **SHOW** tab, select `MY_SHOW.show`, and tap **LOAD**.


3. Ensure **Recall Safes** (`SHOW` $\rightarrow$ `SAFE`) do not block **Channel Name/Color** or **Surface Layer** changes.


4. Assign two **Custom Control** buttons to **Prev** and **Go + Next** to step cleanly from `00_SOUNDCHECK` through every scene in the production.



---

## Stage 3: Markdown Script Mic Annotator (`annotate_script_mics.py`)

Cross-references `mic_tracking_sheet.csv` with a Markdown script (`.md`) and automatically appends each character's assigned wireless microphone number to their **bolded** dialogue cues.

### Features

* **Scene-Aware Channel Lookup:** Tracks Act and Scene Markdown headings (`# Act 1`, `## Scene 2`, `### Act 1 - Scene 2`) so that if an actor or character shares or switches packs across the show, the script pulls the exact mic assigned in that specific scene.


* **Intelligent Fallback:** If a bolded character appears in a scene without an explicit `X` or `-` entry in that scene's column on the tracking sheet, it falls back to that character's primary assigned mic channel.


* **Formatting Preservation:** Only modifies `**bold**` text matching a known character name from your tracking CSV—leaving stage directions, dialogue text, and non-character bolded emphasis completely untouched.


* **Idempotent Execution:** Automatically detects existing mic tags like `**Protagonist (1)**` so running the script multiple times will never double-tag names (e.g., `**Protagonist (1) (1)**`).


* **Colon & Punctuation Safe:** Handles colons placed either inside or outside bold tags (`**Protagonist:**` becomes `**Protagonist (1):**`, and `**Protagonist**:` becomes `**Protagonist (1)**:`).



### Usage & CLI Options (`annotate_script_mics.py`)

```bash
# Basic command (reads mic_tracking_sheet.csv, outputs script_with_mics.md)
python annotate_script_mics.py script.md

# Custom paths
python annotate_script_mics.py path/to/script.md \
  --csv path/to/mic_tracking_sheet.csv \
  --output path/to/annotated_script.md

```

| Flag | Default | Description |
| --- | --- | --- |
| `script_md` | *(Required)* | Path to the input Markdown script (`.md`).

 |
| `--csv` | `mic_tracking_sheet.csv` | Path to the microphone tracking CSV file.

 |
| `--output` | `script_with_mics.md` | Output path for the annotated Markdown script. |
| `--version` | `None` | Optional build version label (e.g., `v4`) embedded at the top of the annotated script (idempotent on re-runs). |
| `--note` | `""` | Optional revision note embedded alongside version metadata. |

---

## Automated Versioned Pipeline (`run_lavs.sh`)

Run all stages together reading from `Inputs/` and writing into auto-incrementing versioned directories (`Outputs/v1/`, `Outputs/v2/`, ... + `Outputs/latest` symlink + `Outputs/manifest.json`):

```bash
./run_lavs.sh \
  --inputs Inputs \
  --outputs Outputs \
  --show-name SCROOGE \
  --mics 28 \
  --passes 75 \
  --iters 5000 \
  --note "Initial Scrooge build"
```

* Auto-detects `Inputs/layer_groups.json`, `Inputs/cast_gender.json`, and `Inputs/script_cut.md` (or `*script*.md`) when present.
* Stamps the version label, timestamp, and `--note` into CSV/Excel subtitles, Excel print footers & workbook properties, the Behringer WING `.show` filename & Soundcheck cue, and the annotated Markdown header.
* Saves an exact `show_data.json` snapshot and `build_info.json` (including RNG `--seed` and SHA-256 input hashes) inside each `Outputs/vN/` directory.
* Pass `--overwrite` to re-run into the latest version folder without incrementing, or `--version <label>` to set an explicit version tag.

### Example Input & Annotated Output

Given a `mic_tracking_sheet.csv` containing:

```csv
Mic #,Actor Name,Character(s),Act 1 - Scene 1,Act 1 - Scene 2,Act 1 - Scene 3
Mic 1,Alex Rivera,Protagonist,,X,
Mic 2,Jordan Taylor,Narrator,X,X,
Mic 3,Mike Smith,Mentor,,X,X

```

Running `annotate_script_mics.py` on the `script.md` example produces `script_with_mics.md`:

```markdown
## Act 1 - Scene 2

**Narrator (2)**: The curtain rises on the town square just before dawn.

**Protagonist (1):** We must make haste before the clock strikes noon.

**Mentor (3)**: Right behind you—let's gather the rest of the company!

```