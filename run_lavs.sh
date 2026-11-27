#!/usr/bin/env bash
# Lav Chart — End-to-End Versioned Pipeline Runner
# Chains existing stages together using Inputs/ and versioned Outputs/vN/ folders.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults
INPUT_DIR="${SCRIPT_DIR}/Inputs"
OUTPUT_DIR="${SCRIPT_DIR}/Outputs"
SHOW_NAME="SHOW_LAVS"
MICS=28
PASSES=75
ITERS=5000
PERF_DAYS=""
STYLE="character"
CAST_FILE=""
GROUP_CONFIG=""
INIT_SNAP=""
SCRIPT_MD=""
VERSION_LABEL=""
OVERWRITE=0
NOTE=""
SEED=""
SKIP_WING=0
SKIP_ANNOTATE=0

usage() {
  cat <<'EOF'
Usage: ./run_lavs.sh [OPTIONS]

Options:
  --inputs DIR         Input directory (default: ./Inputs)
  --outputs DIR        Output base directory (default: ./Outputs)
  --show-name NAME     Show name for Behringer WING .show file (default: SHOW_LAVS)
  --mics N             Number of available wireless mic packs (default: 28)
  --passes N           Simulated annealing passes (default: 75)
  --iters N            Iterations per annealing pass (default: 5000)
  --perf-days LIST     Comma-separated check-off columns for Top-of-Show sheet
  --style STYLE        WING scribble style: character|char_actor|actor_first|actor_full
  --cast-file PATH     Optional gender JSON (auto-detects <inputs>/cast_gender.json)
  --group-config PATH  Optional WING layer groups JSON (auto-detects <inputs>/layer_groups.json)
  --init-snap PATH     Optional WING initial .snap file
  --script PATH        Optional cut Markdown script (auto-detects <inputs>/script_cut.md)
  --version LABEL      Explicit version label (default: auto-increment v1, v2, ...)
  --overwrite          Overwrite the latest version folder instead of incrementing
  --note TEXT          Revision note embedded in subtitles, metadata, and manifest.json
  --seed INT           Random seed for reproducible optimization (auto-generated if omitted)
  --skip-wing          Skip Stage 2 (Behringer WING show generation)
  --skip-annotate      Skip Stage 3 (Markdown script annotation)
  -h, --help           Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inputs)       INPUT_DIR="$2"; shift 2 ;;
    --outputs)      OUTPUT_DIR="$2"; shift 2 ;;
    --show-name)    SHOW_NAME="$2"; shift 2 ;;
    --mics)         MICS="$2"; shift 2 ;;
    --passes)       PASSES="$2"; shift 2 ;;
    --iters)        ITERS="$2"; shift 2 ;;
    --perf-days)    PERF_DAYS="$2"; shift 2 ;;
    --style)        STYLE="$2"; shift 2 ;;
    --cast-file)    CAST_FILE="$2"; shift 2 ;;
    --group-config) GROUP_CONFIG="$2"; shift 2 ;;
    --init-snap)    INIT_SNAP="$2"; shift 2 ;;
    --script)       SCRIPT_MD="$2"; shift 2 ;;
    --version)      VERSION_LABEL="$2"; shift 2 ;;
    --overwrite)    OVERWRITE=1; shift ;;
    --note)         NOTE="$2"; shift 2 ;;
    --seed)         SEED="$2"; shift 2 ;;
    --skip-wing)    SKIP_WING=1; shift ;;
    --skip-annotate) SKIP_ANNOTATE=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *)
      echo "Error: Unknown option '$1'" >&2
      usage >&2
      exit 1
      ;;
  esac
done

# Select Python interpreter (prefer local .venv if present)
if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="$PYTHON_BIN"
elif [[ -x "${SCRIPT_DIR}/.venv/bin/python3" ]]; then
  PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
else
  PYTHON="python3"
fi

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Error: Python interpreter '$PYTHON' not found." >&2
  exit 1
fi

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "Error: Input directory '$INPUT_DIR' does not exist." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

# Auto-detect optional inputs when not explicitly passed
if [[ -z "$CAST_FILE" && -f "${INPUT_DIR}/cast_gender.json" ]]; then
  CAST_FILE="${INPUT_DIR}/cast_gender.json"
fi

if [[ -z "$GROUP_CONFIG" && -f "${INPUT_DIR}/layer_groups.json" ]]; then
  GROUP_CONFIG="${INPUT_DIR}/layer_groups.json"
fi

if [[ -z "$SCRIPT_MD" ]]; then
  if [[ -f "${INPUT_DIR}/script_cut.md" ]]; then
    SCRIPT_MD="${INPUT_DIR}/script_cut.md"
  else
    for candidate in "${INPUT_DIR}"/*script*.md; do
      if [[ -f "$candidate" ]]; then
        SCRIPT_MD="$candidate"
        break
      fi
    done
  fi
fi

# Resolve version label (auto-increment v1, v2, ... or reuse latest with --overwrite)
if [[ -z "$VERSION_LABEL" ]]; then
  VERSION_LABEL="$("$PYTHON" - "$OUTPUT_DIR" "$OVERWRITE" <<'PY'
import json, os, re, sys

out_dir = sys.argv[1]
overwrite = sys.argv[2] == "1"
manifest_path = os.path.join(out_dir, "manifest.json")

max_num = 0
if os.path.exists(manifest_path):
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("builds", []):
            m = re.fullmatch(r"v(\d+)", str(entry.get("version", "")))
            if m:
                max_num = max(max_num, int(m.group(1)))
    except Exception:
        pass

if os.path.isdir(out_dir):
    for name in os.listdir(out_dir):
        m = re.fullmatch(r"v(\d+)", name)
        if m and os.path.isdir(os.path.join(out_dir, name)):
            max_num = max(max_num, int(m.group(1)))

if overwrite and max_num > 0:
    print(f"v{max_num}")
else:
    print(f"v{max_num + 1}")
PY
)"
fi

# Generate a reproducible RNG seed if none was provided
if [[ -z "$SEED" ]]; then
  SEED="$("$PYTHON" -c 'import random; print(random.randint(1, 2_000_000_000))')"
fi

RUN_DIR="${OUTPUT_DIR}/${VERSION_LABEL}"
mkdir -p "$RUN_DIR"

SHOW_JSON_SRC="${INPUT_DIR}/show_data.json"
SHOW_JSON_RUN="${RUN_DIR}/show_data.json"

if [[ ! -f "$SHOW_JSON_SRC" ]]; then
  echo "Error: Required input '${SHOW_JSON_SRC}' not found." >&2
  exit 1
fi

cp "$SHOW_JSON_SRC" "$SHOW_JSON_RUN"

TRACKING_CSV="${RUN_DIR}/mic_tracking_sheet.csv"
TRACKING_XLSX="${RUN_DIR}/mic_tracking_sheet.xlsx"
HANDOFFS_XLSX="${RUN_DIR}/backstage_mic_handoffs.xlsx"
TOP_XLSX="${RUN_DIR}/top_of_show_preset.xlsx"
WING_DIR="${RUN_DIR}/WING_SHOW"
ANNOTATED_MD="${RUN_DIR}/script_annotated.md"

echo "============================================================"
echo "Lav Chart Pipeline — ${VERSION_LABEL}"
echo "  Inputs:    ${INPUT_DIR}"
echo "  Output:    ${RUN_DIR}"
echo "  Show Name: ${SHOW_NAME}"
echo "  Mics:      ${MICS} (${PASSES} passes x ${ITERS} iters, seed=${SEED})"
if [[ -n "$NOTE" ]]; then
  echo "  Note:      ${NOTE}"
fi
echo "============================================================"

# Stage 1: Wireless Microphone Allocation Optimizer
OPT_ARGS=(
  "${SCRIPT_DIR}/optimize_mics.py"
  "$SHOW_JSON_RUN"
  --mics "$MICS"
  --passes "$PASSES"
  --iters "$ITERS"
  --seed "$SEED"
  --version "$VERSION_LABEL"
  --output "$TRACKING_CSV"
  --output-xlsx "$TRACKING_XLSX"
  --top-xlsx "$TOP_XLSX"
  --handoffs-xlsx "$HANDOFFS_XLSX"
)
if [[ -n "$NOTE" ]]; then
  OPT_ARGS+=(--note "$NOTE")
fi
if [[ -n "$CAST_FILE" ]]; then
  OPT_ARGS+=(--cast-file "$CAST_FILE")
fi
if [[ -n "$PERF_DAYS" ]]; then
  OPT_ARGS+=(--perf-days "$PERF_DAYS")
fi

echo ""
echo "[Stage 1] Running microphone optimizer..."
"$PYTHON" "${OPT_ARGS[@]}"

# Stage 2: Behringer WING Show & Snippet Generator
if [[ "$SKIP_WING" -eq 0 ]]; then
  WING_ARGS=(
    "${SCRIPT_DIR}/csv_to_wing_show.py"
    "$TRACKING_CSV"
    "$SHOW_JSON_RUN"
    --outdir "$WING_DIR"
    --show-name "$SHOW_NAME"
    --style "$STYLE"
    --version "$VERSION_LABEL"
  )
  if [[ -n "$NOTE" ]]; then
    WING_ARGS+=(--note "$NOTE")
  fi
  if [[ -n "$GROUP_CONFIG" ]]; then
    WING_ARGS+=(--group-config "$GROUP_CONFIG")
  fi
  if [[ -n "$INIT_SNAP" ]]; then
    WING_ARGS+=(--init-snap "$INIT_SNAP")
  fi

  echo ""
  echo "[Stage 2] Generating Behringer WING show & snippets..."
  "$PYTHON" "${WING_ARGS[@]}"
else
  echo ""
  echo "[Stage 2] Skipped (--skip-wing)."
fi

# Stage 3: Markdown Script Mic Annotator
ANNOTATED_PRODUCED=0
if [[ "$SKIP_ANNOTATE" -eq 0 && -n "$SCRIPT_MD" && -f "$SCRIPT_MD" ]]; then
  ANN_ARGS=(
    "${SCRIPT_DIR}/annotate_script_mics.py"
    "$SCRIPT_MD"
    --csv "$TRACKING_CSV"
    --output "$ANNOTATED_MD"
    --version "$VERSION_LABEL"
  )
  if [[ -n "$NOTE" ]]; then
    ANN_ARGS+=(--note "$NOTE")
  fi

  echo ""
  echo "[Stage 3] Annotating Markdown script (${SCRIPT_MD})..."
  "$PYTHON" "${ANN_ARGS[@]}"
  ANNOTATED_PRODUCED=1
else
  echo ""
  if [[ "$SKIP_ANNOTATE" -eq 1 ]]; then
    echo "[Stage 3] Skipped (--skip-annotate)."
  else
    echo "[Stage 3] Skipped (no cut script Markdown found in ${INPUT_DIR})."
  fi
fi

# Write build_info.json, update Outputs/manifest.json, and update Outputs/latest symlink
"$PYTHON" - "$OUTPUT_DIR" "$RUN_DIR" "$VERSION_LABEL" "$SHOW_NAME" "$MICS" "$PASSES" "$ITERS" "$SEED" "$NOTE" "$SHOW_JSON_SRC" "$CAST_FILE" "$GROUP_CONFIG" "$SCRIPT_MD" "$ANNOTATED_PRODUCED" <<'PY'
from datetime import datetime
import hashlib
import json
import os
import sys

(
    out_dir,
    run_dir,
    version,
    show_name,
    mics,
    passes,
    iters,
    seed,
    note,
    show_json_src,
    cast_file,
    group_config,
    script_md,
    annotated_produced,
) = sys.argv[1:]

def sha256_file(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

timestamp = datetime.now().isoformat(timespec="seconds")

inputs_meta = {}
for key, path in [
    ("show_data", show_json_src),
    ("cast_file", cast_file),
    ("group_config", group_config),
    ("script_md", script_md),
]:
    if path and os.path.isfile(path):
        inputs_meta[key] = {
            "path": os.path.abspath(path),
            "sha256": sha256_file(path),
        }

generated_files = sorted(
    os.path.relpath(os.path.join(root, fname), run_dir)
    for root, _, files in os.walk(run_dir)
    for fname in files
    if fname != "build_info.json"
)

build_info = {
    "version": version,
    "timestamp": timestamp,
    "show_name": show_name,
    "note": note,
    "parameters": {
        "mics": int(mics),
        "passes": int(passes),
        "iters": int(iters),
        "seed": int(seed),
    },
    "inputs": inputs_meta,
    "annotated_script": annotated_produced == "1",
    "outputs": generated_files,
}

with open(os.path.join(run_dir, "build_info.json"), "w", encoding="utf-8") as f:
    json.dump(build_info, f, indent=2)

manifest_path = os.path.join(out_dir, "manifest.json")
manifest = {"latest": version, "builds": []}
if os.path.exists(manifest_path):
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        manifest = {"latest": version, "builds": []}

builds = [b for b in manifest.get("builds", []) if b.get("version") != version]
builds.append({
    "version": version,
    "timestamp": timestamp,
    "show_name": show_name,
    "note": note,
    "seed": int(seed),
    "path": version,
})
manifest["latest"] = version
manifest["builds"] = builds

with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
PY

# Atomic update of Outputs/latest -> <VERSION_LABEL>
ln -sfn "$VERSION_LABEL" "${OUTPUT_DIR}/latest"

echo ""
echo "============================================================"
echo "Build ${VERSION_LABEL} complete!"
echo "  Folder:   ${RUN_DIR}"
echo "  Latest:   ${OUTPUT_DIR}/latest -> ${VERSION_LABEL}"
echo "  Manifest: ${OUTPUT_DIR}/manifest.json"
echo "============================================================"
