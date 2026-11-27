# TODOs

## Completed

- [x] **Local-only PDF scan/digital to Markdown (`pdf_to_md.py`):** Per-page digital text (`pdftotext`) + local OCR (on-device macOS Apple Vision / `tesseract`) + optional strictly localhost-only Ollama cleanup, normalizing Act/Scene headers and bold character cues.
- [x] **Interactive script stripper (`strip_script.py`):** `git add -p`-style hunk reviewer (`y/n/s/e/a/d/u///q/?`) with `$EDITOR` editing and resumable `.strip_state.json`.
- [x] **Cast list + script parser to `show_data.json` (`build_show_data.py`):** Parses Markdown/text cast lists (`cast_list.md`) + `script_cut.md` + persistent `overrides.json` (aliases, group expansions, quick scenes, non-speaking onstage actors) with fuzzy-match diagnostics.
- [x] **Chained pipeline with Input/Output folders (`run_lavs.sh`):** Orchestrates all stages from `Inputs/` into auto-incrementing `Outputs/vN/` directories with `Outputs/latest` symlink and `Outputs/manifest.json`.
- [x] **Embedded versioning metadata:** Stamps version tag, timestamp, and revision `--note` into CSV/Excel subtitles, Excel print footers & workbook properties, Behringer WING `.show` filename & Soundcheck cue info, and annotated Markdown headers.
