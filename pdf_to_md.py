"""
Stage 0a — Local-Only PDF-to-Markdown Script Converter

Converts digital or scanned theatrical script PDFs into clean Markdown with
normalized Act/Scene headings (# Act 1, ## Scene 2) and bold speaker cues
(**Character**: dialogue), ready for strip_script.py and build_show_data.py.

Privacy Guarantee:
  100% local / offline. Uses local pdftotext, pdftoppm, macOS Apple Vision
  (on-device via Swift) or local Tesseract OCR, and an optional strictly
  localhost-only Ollama model (--local-llm). No data ever leaves this machine.
"""

import argparse
from collections import Counter
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request


ROMAN_MAP = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
    "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20,
}

WORD_MAP = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}

# Swift source for on-device macOS Apple Vision OCR (zero network calls, headless ImageIO)
APPLE_VISION_SWIFT = r"""
import Foundation
import Vision
import ImageIO

let args = Array(CommandLine.arguments.dropFirst())
var results: [[String: Any]] = []

for path in args {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cgImage = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        results.append(["path": path, "text": "", "error": "Could not decode image via ImageIO"])
        continue
    }

    var pageLines: [(y: CGFloat, x: CGFloat, text: String)] = []
    var reqError: String? = nil
    let request = VNRecognizeTextRequest { req, err in
        if let err = err {
            reqError = "\(err)"
            return
        }
        guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
        for item in obs {
            guard let top = item.topCandidates(1).first else { continue }
            let box = item.boundingBox
            pageLines.append((y: box.origin.y + box.size.height / 2.0, x: box.origin.x, text: top.string))
        }
    }
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        results.append(["path": path, "text": "", "error": "\(error)"])
        continue
    }

    if let reqError = reqError {
        results.append(["path": path, "text": "", "error": reqError])
        continue
    }

    // Sort top-to-bottom (higher y first in Vision normalized coords), then left-to-right
    pageLines.sort { a, b in
        if abs(a.y - b.y) > 0.012 {
            return a.y > b.y
        }
        return a.x < b.x
    }

    // Group observations on the same visual line
    var mergedLines: [String] = []
    var currentY: CGFloat? = nil
    var currentRow: [(x: CGFloat, text: String)] = []

    for item in pageLines {
        if let cy = currentY, abs(item.y - cy) <= 0.012 {
            currentRow.append((x: item.x, text: item.text))
        } else {
            if !currentRow.isEmpty {
                currentRow.sort { $0.x < $1.x }
                mergedLines.append(currentRow.map { $0.text }.joined(separator: " "))
            }
            currentY = item.y
            currentRow = [(x: item.x, text: item.text)]
        }
    }
    if !currentRow.isEmpty {
        currentRow.sort { $0.x < $1.x }
        mergedLines.append(currentRow.map { $0.text }.joined(separator: " "))
    }

    results.append(["path": path, "text": mergedLines.joined(separator: "\n")])
}

if let data = try? JSONSerialization.data(withJSONObject: results, options: []),
   let jsonStr = String(data: data, encoding: .utf8) {
    print(jsonStr)
}
"""


def parse_number_token(token):
    t = token.strip().lower()
    if t.isdigit():
        return int(t)
    if t in ROMAN_MAP:
        return ROMAN_MAP[t]
    if t in WORD_MAP:
        return WORD_MAP[t]
    return None


def parse_page_ranges(spec, total_pages):
    """Parses page range strings like '1-5,8,10-12' into sorted 1-based page numbers."""
    if not spec:
        return list(range(1, total_pages + 1))
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo = max(1, int(lo_s.strip()))
            hi = min(total_pages, int(hi_s.strip())) if hi_s.strip() else total_pages
            pages.update(range(lo, hi + 1))
        else:
            p = int(part)
            if 1 <= p <= total_pages:
                pages.add(p)
    return sorted(pages)


def get_pdf_page_count(pdf_path):
    """Uses local pdfinfo (Poppler) to read total PDF page count."""
    if shutil.which("pdfinfo"):
        res = subprocess.run(
            ["pdfinfo", pdf_path], capture_output=True, text=True, check=True
        )
        m = re.search(r"^Pages:\s*(\d+)", res.stdout, re.MULTILINE)
        if m:
            return int(m.group(1))

    # Fallback: count form-feeds in pdftotext output
    if shutil.which("pdftotext"):
        res = subprocess.run(
            ["pdftotext", pdf_path, "-"], capture_output=True, text=True, check=True
        )
        return max(1, res.stdout.count("\f"))

    raise RuntimeError("Neither 'pdfinfo' nor 'pdftotext' (poppler) was found in PATH.")


def extract_digital_page_text(pdf_path, page_num):
    """Extracts selectable text from a single PDF page via local pdftotext."""
    if not shutil.which("pdftotext"):
        return ""
    res = subprocess.run(
        ["pdftotext", "-f", str(page_num), "-l", str(page_num), "-layout", pdf_path, "-"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return ""
    return res.stdout.replace("\f", "")


def render_pages_to_png(pdf_path, page_nums, dpi, work_dir):
    """Renders specified 1-based PDF pages to PNG files using local pdftoppm."""
    if not shutil.which("pdftoppm"):
        raise RuntimeError("'pdftoppm' (poppler) is required to render scanned PDF pages for OCR.")

    rendered = {}
    for p in page_nums:
        prefix = os.path.join(work_dir, f"page_{p:04d}")
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), "-f", str(p), "-l", str(p), pdf_path, prefix],
            check=True,
            capture_output=True,
        )
        matches = sorted(
            os.path.join(work_dir, fn)
            for fn in os.listdir(work_dir)
            if fn.startswith(f"page_{p:04d}") and fn.endswith(".png")
        )
        if matches:
            rendered[p] = matches[0]
    return rendered


def run_apple_vision_ocr(image_paths, work_dir):
    """Runs on-device macOS Apple Vision OCR across a batch of PNG images in one Swift invocation."""
    if platform.system() != "Darwin" or not shutil.which("swift"):
        raise RuntimeError("Apple Vision OCR requires macOS with 'swift' available.")

    swift_file = os.path.join(work_dir, "vision_ocr.swift")
    mod_cache = os.path.join(tempfile.gettempdir(), "lav_chart_swift_modcache")
    os.makedirs(mod_cache, exist_ok=True)
    with open(swift_file, "w", encoding="utf-8") as f:
        f.write(APPLE_VISION_SWIFT)

    env = dict(os.environ)
    env["CLANG_MODULE_CACHE_PATH"] = mod_cache
    env["SWIFT_MODULECACHE_PATH"] = mod_cache

    res = subprocess.run(
        ["swift", "-module-cache-path", mod_cache, swift_file] + list(image_paths),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    data = json.loads(res.stdout)
    errors = [item["error"] for item in data if item.get("error")]
    if errors and all(not item.get("text", "").strip() for item in data):
        raise RuntimeError(errors[0])
    return {item["path"]: item.get("text", "") for item in data}


def run_tesseract_ocr(image_paths, psm=6):
    """Runs local Tesseract OCR across a batch of PNG images."""
    if not shutil.which("tesseract"):
        raise RuntimeError("'tesseract' was not found in PATH.")

    results = {}
    for img in image_paths:
        res = subprocess.run(
            ["tesseract", img, "stdout", "--psm", str(psm)],
            capture_output=True,
            text=True,
            check=True,
        )
        results[img] = res.stdout
    return results


def _ensure_localhost_url(url):
    """Enforces strict localhost-only constraint so copyrighted scripts never hit external servers."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError(
            f"Refusing non-local Ollama URL '{url}'. Scripts are copyrighted and must stay local."
        )


def refine_markdown_with_local_ollama(md_text, model, ollama_url="http://127.0.0.1:11434"):
    """
    Optional local-only Ollama cleanup pass.
    Processes script in chunks so long scripts stay within local context windows.
    """
    _ensure_localhost_url(ollama_url)
    endpoint = ollama_url.rstrip("/") + "/api/generate"

    # Split into chunks around ~3500 chars on blank lines
    paragraphs = md_text.split("\n\n")
    chunks = []
    cur = []
    cur_len = 0
    for para in paragraphs:
        if cur and cur_len + len(para) > 3500:
            chunks.append("\n\n".join(cur))
            cur = [para]
            cur_len = len(para)
        else:
            cur.append(para)
            cur_len += len(para) + 2
    if cur:
        chunks.append("\n\n".join(cur))

    system_prompt = (
        "You are cleaning OCR output of a theatrical play script into Markdown. "
        "Preserve ALL dialogue verbatim—never summarize or omit lines. "
        "Format Act headings as '# Act N', Scene headings as '## Scene N', "
        "character cues as '**Character Name**: dialogue', and stage directions in italics '*(...)*'. "
        "Fix obvious OCR typos only. Output ONLY the cleaned Markdown."
    )

    cleaned_chunks = []
    for idx, chunk in enumerate(chunks, start=1):
        print(f"  Local Ollama cleanup ({model}): chunk {idx}/{len(chunks)}...")
        payload = json.dumps({
            "model": model,
            "prompt": f"{system_prompt}\n\n---\n{chunk}\n---",
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        cleaned_chunks.append(body.get("response", chunk).strip())

    return "\n\n".join(cleaned_chunks) + "\n"


# -----------------------------------------------------------------------------
# Theatrical Script Layout -> Markdown Heuristics
# -----------------------------------------------------------------------------
PAGE_NUM_RE = re.compile(
    r"^\s*(?:[-–—]?\s*\d{1,4}\s*[-–—]?|page\s+\d{1,4}|[ivx]+-\d{1,4}|\d{1,2}-\d{1,3})\s*$",
    re.IGNORECASE,
)

ACT_SCENE_COMBINED_RE = re.compile(
    r"^\s*act\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s*[,:;\-–—\s]+\s*scene\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)(?:\s*[:.\-–—]\s*(.*))?$",
    re.IGNORECASE,
)
ACT_ONLY_RE = re.compile(
    r"^\s*act\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)(?:\s*[:.\-–—]\s*(.*))?$",
    re.IGNORECASE,
)
SCENE_ONLY_RE = re.compile(
    r"^\s*scene\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)(?:\s*[:.\-–—]\s*(.*))?$",
    re.IGNORECASE,
)
PROLOGUE_EPILOGUE_RE = re.compile(r"^\s*(prologue|epilogue)(?:\s*[:.\-–—]\s*(.*))?$", re.IGNORECASE)

# Inline cue: "SCROOGE: Bah!" or "SCROOGE. Bah!" or "BOB CRATCHIT (nervously). Yes!"
INLINE_CUE_RE = re.compile(
    r"^([A-Z][A-Z0-9 '\-/&]{0,34}(?:\s*\([^)]{1,30}\))?)\s*([.:])\s+(.*)$"
)
# Standalone ALL-CAPS speaker cue on its own line (Samuel French / MTI style)
STANDALONE_CAPS_CUE_RE = re.compile(
    r"^([A-Z][A-Z0-9 '\-/&]{1,34}(?:\s*\([^)]{1,30}\))?)\s*[:.]?$"
)

NON_SPEAKER_CAPS = {
    "END OF ACT", "END OF SCENE", "END OF PLAY", "THE END", "INTERMISSION",
    "BLACKOUT", "CURTAIN", "LIGHTS UP", "LIGHTS OUT", "FADE OUT", "FADE IN",
    "AT RISE", "SETTING", "TIME", "PLACE", "CAST OF CHARACTERS", "CHARACTERS",
    "MUSICAL NUMBERS", "SCENES", "ACT I", "ACT II", "ACT III",
}


def smart_title_character(raw_name):
    """Converts ALL-CAPS speaker names like 'MRS. CRATCHIT' or 'CAROLER A' into clean Title Case."""
    name = re.sub(r"\s+", " ", raw_name.strip())
    # Separate trailing parenthetical like "(angrily)"
    paren = ""
    pm = re.search(r"\s*(\([^)]*\))\s*$", name)
    if pm:
        paren = pm.group(1).strip()
        name = name[: pm.start()].strip()

    if not name.isupper():
        return name, paren

    small_words = {"of", "the", "and", "in", "on", "at", "to"}
    words = name.split()
    out_words = []
    for idx, w in enumerate(words):
        low = w.lower()
        if len(w) == 1 and w.isalpha():
            out_words.append(w.upper())
        elif low in ("mr.", "mrs.", "ms.", "dr.", "st.", "mr", "mrs", "ms", "dr"):
            out_words.append(low.capitalize())
        elif idx > 0 and low in small_words:
            out_words.append(low)
        else:
            out_words.append(w.capitalize())
    return " ".join(out_words), paren


def strip_running_headers_footers(pages_text):
    """Removes repeating top/bottom header/footer lines and standalone page numbers across pages."""
    page_lines = [p.splitlines() for p in pages_text]
    num_pages = len(page_lines)

    repeated_edges = set()
    if num_pages >= 3:
        edge_counter = Counter()
        for lines in page_lines:
            non_empty = [l.strip() for l in lines if l.strip()]
            if not non_empty:
                continue
            candidates = set(non_empty[:2] + non_empty[-2:])
            for cand in candidates:
                # Normalize digits so "Act I - 12" and "Act I - 13" count together
                norm = re.sub(r"\d+", "#", cand).strip().lower()
                if len(norm) <= 60:
                    edge_counter[norm] += 1
        threshold = max(2, int(num_pages * 0.4))
        repeated_edges = {k for k, v in edge_counter.items() if v >= threshold}

    cleaned_pages = []
    for lines in page_lines:
        # Trim leading/trailing blank lines
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()

        # Check top 2 and bottom 2 lines for page numbers or repeated running headers/footers
        kept = []
        total = len(lines)
        for idx, line in enumerate(lines):
            s = line.strip()
            is_edge = idx < 2 or idx >= total - 2
            if is_edge and s:
                if PAGE_NUM_RE.match(s):
                    continue
                norm = re.sub(r"\d+", "#", s).strip().lower()
                if norm in repeated_edges:
                    continue
            kept.append(line)
        cleaned_pages.append("\n".join(kept))

    return cleaned_pages


def _looks_like_standalone_speaker(line_stripped):
    if not STANDALONE_CAPS_CUE_RE.match(line_stripped):
        return False
    upper_clean = re.sub(r"\s*\([^)]*\)", "", line_stripped).rstrip(".:").strip().upper()
    if not upper_clean or len(upper_clean) < 2:
        return False
    if upper_clean in NON_SPEAKER_CAPS:
        return False
    if any(upper_clean.startswith(prefix) for prefix in ("END OF ", "LIGHTS ", "MUSIC ", "SONG ", "ACT ", "SCENE ")):
        return False
    # Reject lines with too many words (probably an ALL-CAPS stage direction or title banner)
    if len(upper_clean.split()) > 4:
        return False
    return True


def format_pages_to_markdown(pages_text):
    """Converts extracted raw page strings into clean, scene-structured theatrical Markdown."""
    cleaned_pages = strip_running_headers_footers(pages_text)
    all_lines = []
    for page in cleaned_pages:
        all_lines.extend(page.splitlines())
        all_lines.append("")  # page boundary blank line

    output_blocks = []
    i = 0
    n = len(all_lines)

    while i < n:
        raw = all_lines[i]
        stripped = raw.strip()

        if not stripped:
            i += 1
            continue

        # 1. Combined Act + Scene heading
        comb_m = ACT_SCENE_COMBINED_RE.match(stripped)
        if comb_m:
            a_num = parse_number_token(comb_m.group(1))
            s_num = parse_number_token(comb_m.group(2))
            subtitle = (comb_m.group(3) or "").strip()
            if a_num is not None and s_num is not None:
                output_blocks.append(f"# Act {a_num}")
                sc_title = f"## Scene {s_num}" + (f" - {subtitle}" if subtitle else "")
                output_blocks.append(sc_title)
                i += 1
                continue

        # 2. Act heading
        act_m = ACT_ONLY_RE.match(stripped)
        if act_m:
            a_num = parse_number_token(act_m.group(1))
            subtitle = (act_m.group(2) or "").strip()
            if a_num is not None:
                output_blocks.append(f"# Act {a_num}" + (f" - {subtitle}" if subtitle else ""))
                i += 1
                continue

        # 3. Scene heading
        sc_m = SCENE_ONLY_RE.match(stripped)
        if sc_m:
            s_num = parse_number_token(sc_m.group(1))
            subtitle = (sc_m.group(2) or "").strip()
            if s_num is not None:
                output_blocks.append(f"## Scene {s_num}" + (f" - {subtitle}" if subtitle else ""))
                i += 1
                continue

        # 4. Prologue / Epilogue
        pe_m = PROLOGUE_EPILOGUE_RE.match(stripped)
        if pe_m:
            label = pe_m.group(1).capitalize()
            subtitle = (pe_m.group(2) or "").strip()
            output_blocks.append(f"## {label}" + (f" - {subtitle}" if subtitle else ""))
            i += 1
            continue

        # 5. Pure parenthetical/bracketed stage direction block
        if (stripped.startswith("(") and stripped.endswith(")")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            output_blocks.append(f"*{stripped}*")
            i += 1
            continue

        # 6. Inline ALL-CAPS cue ("SCROOGE. Bah, humbug!" or "SCROOGE: Bah!")
        inline_m = INLINE_CUE_RE.match(stripped)
        if inline_m:
            raw_speaker, _, first_dialogue = inline_m.groups()
            upper_check = re.sub(r"\s*\([^)]*\)", "", raw_speaker).strip().upper()
            if upper_check not in NON_SPEAKER_CAPS and len(upper_check.split()) <= 4:
                speaker, paren = smart_title_character(raw_speaker)
                dialogue_parts = []
                if paren:
                    dialogue_parts.append(f"*{paren}*")
                if first_dialogue.strip():
                    dialogue_parts.append(first_dialogue.strip())

                # Gather wrapped continuation lines until blank line, heading, or next cue
                i += 1
                while i < n:
                    nxt = all_lines[i].strip()
                    if (
                        not nxt
                        or ACT_SCENE_COMBINED_RE.match(nxt)
                        or ACT_ONLY_RE.match(nxt)
                        or SCENE_ONLY_RE.match(nxt)
                        or PROLOGUE_EPILOGUE_RE.match(nxt)
                        or INLINE_CUE_RE.match(nxt)
                        or _looks_like_standalone_speaker(nxt)
                    ):
                        break
                    dialogue_parts.append(nxt)
                    i += 1

                output_blocks.append(f"**{speaker}**: {' '.join(dialogue_parts)}")
                continue

        # 7. Standalone ALL-CAPS speaker cue followed by dialogue on subsequent lines
        if _looks_like_standalone_speaker(stripped):
            # Look ahead past optional blank line to confirm dialogue follows
            j = i + 1
            while j < n and not all_lines[j].strip():
                j += 1
            if j < n and not (
                ACT_SCENE_COMBINED_RE.match(all_lines[j].strip())
                or ACT_ONLY_RE.match(all_lines[j].strip())
                or SCENE_ONLY_RE.match(all_lines[j].strip())
                or _looks_like_standalone_speaker(all_lines[j].strip())
            ):
                speaker, paren = smart_title_character(stripped.rstrip(".:"))
                dialogue_parts = []
                if paren:
                    dialogue_parts.append(f"*{paren}*")

                i = j
                while i < n:
                    nxt = all_lines[i].strip()
                    if (
                        not nxt
                        or ACT_SCENE_COMBINED_RE.match(nxt)
                        or ACT_ONLY_RE.match(nxt)
                        or SCENE_ONLY_RE.match(nxt)
                        or PROLOGUE_EPILOGUE_RE.match(nxt)
                        or INLINE_CUE_RE.match(nxt)
                        or _looks_like_standalone_speaker(nxt)
                    ):
                        break
                    if nxt.startswith("(") and nxt.endswith(")"):
                        dialogue_parts.append(f"*{nxt}*")
                    else:
                        dialogue_parts.append(nxt)
                    i += 1

                output_blocks.append(f"**{speaker}**: {' '.join(dialogue_parts)}")
                continue

        # 8. Regular paragraph / text block
        para_lines = [stripped]
        i += 1
        while i < n:
            nxt = all_lines[i].strip()
            if (
                not nxt
                or ACT_SCENE_COMBINED_RE.match(nxt)
                or ACT_ONLY_RE.match(nxt)
                or SCENE_ONLY_RE.match(nxt)
                or PROLOGUE_EPILOGUE_RE.match(nxt)
                or INLINE_CUE_RE.match(nxt)
                or _looks_like_standalone_speaker(nxt)
                or (nxt.startswith("(") and nxt.endswith(")"))
            ):
                break
            para_lines.append(nxt)
            i += 1
        output_blocks.append(" ".join(para_lines))

    return "\n\n".join(output_blocks).strip() + "\n"


def convert_pdf_to_markdown(
    pdf_path,
    output_path,
    ocr_engine="auto",
    dpi=300,
    pages_spec=None,
    force_ocr=False,
    min_text_chars=40,
    tesseract_psm=6,
    local_llm=None,
    ollama_url="http://127.0.0.1:11434",
):
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file '{pdf_path}' not found.")

    if local_llm:
        _ensure_localhost_url(ollama_url)

    total_pages = get_pdf_page_count(pdf_path)
    selected_pages = parse_page_ranges(pages_spec, total_pages)
    print(f"Processing '{pdf_path}' ({len(selected_pages)} of {total_pages} page(s))...")

    digital_pages = {}
    scanned_page_nums = []

    for p in selected_pages:
        if not force_ocr:
            txt = extract_digital_page_text(pdf_path, p)
            alnum_count = sum(ch.isalnum() for ch in txt)
            if alnum_count >= min_text_chars:
                digital_pages[p] = txt
                continue
        scanned_page_nums.append(p)

    print(
        f"  Digital text pages: {len(digital_pages)} | "
        f"Scanned pages needing local OCR: {len(scanned_page_nums)}"
    )

    ocr_pages = {}
    if scanned_page_nums:
        with tempfile.TemporaryDirectory(prefix="lav_ocr_") as work_dir:
            print(f"  Rendering {len(scanned_page_nums)} scanned page(s) at {dpi} DPI...")
            rendered_map = render_pages_to_png(pdf_path, scanned_page_nums, dpi, work_dir)
            ordered_imgs = [rendered_map[p] for p in scanned_page_nums if p in rendered_map]

            chosen_engine = ocr_engine
            if chosen_engine == "auto":
                if platform.system() == "Darwin" and shutil.which("swift"):
                    chosen_engine = "vision"
                else:
                    chosen_engine = "tesseract"

            print(f"  Running local OCR engine: {chosen_engine}...")
            if chosen_engine == "vision":
                try:
                    img_to_text = run_apple_vision_ocr(ordered_imgs, work_dir)
                except Exception as err:
                    print(f"  Warning: Apple Vision OCR failed ({err}); falling back to local Tesseract.")
                    img_to_text = run_tesseract_ocr(ordered_imgs, psm=tesseract_psm)
            else:
                img_to_text = run_tesseract_ocr(ordered_imgs, psm=tesseract_psm)

            for p, img_path in rendered_map.items():
                ocr_pages[p] = img_to_text.get(img_path, "")

    ordered_page_texts = [
        digital_pages[p] if p in digital_pages else ocr_pages.get(p, "")
        for p in selected_pages
    ]

    md_text = format_pages_to_markdown(ordered_page_texts)

    if local_llm:
        md_text = refine_markdown_with_local_ollama(md_text, local_llm, ollama_url=ollama_url)

    out_parent = os.path.dirname(os.path.abspath(output_path))
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_text)

    print(f"Wrote Markdown script to '{output_path}' ({len(md_text.splitlines())} lines).")


def main():
    parser = argparse.ArgumentParser(
        description="Local-only PDF (digital or scanned) to theatrical Markdown script converter."
    )
    parser.add_argument("pdf_file", help="Path to input PDF script")
    parser.add_argument(
        "-o",
        "--output",
        default="script_raw.md",
        help="Output Markdown path (default: script_raw.md)",
    )
    parser.add_argument(
        "--ocr",
        choices=["auto", "vision", "tesseract"],
        default="auto",
        help="Local OCR engine for scanned pages (default: auto -> Apple Vision on macOS, else Tesseract)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Render DPI for scanned pages (default: 300)",
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="Optional 1-based page ranges to convert, e.g. '1-5,10-12'",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Force OCR even on pages that contain embedded digital text",
    )
    parser.add_argument(
        "--min-text-chars",
        type=int,
        default=40,
        help="Minimum alphanumeric chars on a page to treat it as digital text (default: 40)",
    )
    parser.add_argument(
        "--tesseract-psm",
        type=int,
        default=6,
        help="Tesseract Page Segmentation Mode when using --ocr tesseract (default: 6)",
    )
    parser.add_argument(
        "--local-llm",
        default=None,
        help="Optional local Ollama model name (e.g., 'llama3.2') for post-OCR Markdown cleanup",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434",
        help="Localhost-only Ollama URL (default: http://127.0.0.1:11434)",
    )

    args = parser.parse_args()
    convert_pdf_to_markdown(
        args.pdf_file,
        args.output,
        ocr_engine=args.ocr,
        dpi=args.dpi,
        pages_spec=args.pages,
        force_ocr=args.force_ocr,
        min_text_chars=args.min_text_chars,
        tesseract_psm=args.tesseract_psm,
        local_llm=args.local_llm,
        ollama_url=args.ollama_url,
    )


if __name__ == "__main__":
    main()
