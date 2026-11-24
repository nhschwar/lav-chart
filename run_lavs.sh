#!/bin/zsh

# Stop the script immediately if any command fails
set -e

python3 optimize_mics.py --passes 75 --iters 5000 

python3 csv_to_wing_show.py mic_tracking_sheet.csv show_data.json \
  --show-name SCROOGE \
  --group-config layer_groups.json

python3 annotate_script_mics.py scrooge_script_cut.md \
    --csv mic_tracking_sheet.csv \
    --output scrooge_annotated.md 