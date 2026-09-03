"""Local OCR pre-read of the candidates, so the vision pass only sees what OCR cannot settle.

    python pipeline/tools/ocr.py <slug> [--print P] [--min-conf 0.8] [--dry-run]

RapidOCR (ONNX, Apache-2.0, no service and no key) reads every frame grab that
transcribe.py --prepare makes, and each candidate lands in one of three classes:

  empty      no text on the frame: almost certainly not a card. Recorded as type "none"
             with confidence "high", so --commit leaves it out. A long candidate that the
             dark rule found is held back as unsettled instead, because an illustrated or
             ornate card can defeat OCR.
  settled    OCR is confident and read at least two words. The reading goes in at the
             lowest trust tier: confidence "medium" at best, since a person still checks
             it in scrub.py.
  unsettled  everything else. These are the frames the vision readers get.

Consecutive candidates whose readings match are one card that extract.py split in two; the
later one is marked a duplicate of the first.

Writes out/<slug>/extract/ocr.yaml. transcribe.py --prepare batches the unsettled
candidates alone (--all ignores this file), and --merge takes the settled, empty, and
duplicate entries as readings below pass 1: any reader's response for the same id wins.
"""
from __future__ import annotations

import argparse
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from common import find_print, id_sort_key, load_film, parse_tc, rel
from transcribe import extract_dir, load_candidates, make_grabs

MIN_CONF = 0.8
DUPLICATE_RATIO = 0.9
SAFETY_SECONDS = 4.0          # a long dark candidate stays unsettled even when OCR reads nothing
MIN_ALNUM = 3                 # fewer alphanumerics than this counts as no text at all


# ---------------------------------------------------------------- reading

def read_grab(engine, path: Path) -> tuple[str, float, int]:
    """-> (text, min box confidence, line count) for one grab.

    RapidOCR returns one box per text run. Boxes are sorted top to bottom, then left to
    right, and boxes whose vertical centres sit within half a line height are joined into
    one line with a space, which is how a card's words come back as the card's lines.
    """
    res, _ = engine(str(path))
    boxes = []
    for row in res or []:
        box, text, conf = row[0], row[1], row[2]
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append({"text": str(text), "conf": float(conf),
                      "cy": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys), "x": min(xs)})
    if not boxes:
        return "", 0.0, 0
    line_h = sorted(b["h"] for b in boxes)[len(boxes) // 2] or 1.0
    boxes.sort(key=lambda b: (b["cy"], b["x"]))
    lines: list[list[dict]] = []
    for b in boxes:
        if lines and abs(b["cy"] - lines[-1][0]["cy"]) <= line_h / 2:
            lines[-1].append(b)
        else:
            lines.append([b])
    text = "\n".join(" ".join(b["text"] for b in sorted(line, key=lambda b: b["x"])) for line in lines)
    return text, min(b["conf"] for b in boxes), len(lines)


def norm(text: str) -> str:
    """Lower case, letters and digits and spaces only: what two readings are compared on."""
    t = (text or "").lower().replace("\n", " ")
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def words(text: str) -> int:
    return len(norm(text).split())


# ---------------------------------------------------------------- classifying

QUOTES = (('"', "double straight"), ("“", "double curly"), ("”", "double curly"),
          ("'", "single"), ("‘", "single"), ("’", "single"))


def quote_style(text: str) -> str:
    for mark, name in QUOTES:
        if mark in text:
            return name
    if text.lstrip().startswith(("-", "–", "—")):
        return "dash"
    return "none"


def guess_type(text: str) -> str:
    """OCR cannot tell an insert from a card, so this is the cheap guess a settled card
    carries into cards.yaml: speech marks or an opening dash mean dialogue."""
    head = text.lstrip()[:1]
    return "dialogue" if head in ('"', "“", "‘", "'", "-", "–", "—") else "narrative"


def settled_reading(text: str) -> dict:
    return {"text": text, "type": guess_type(text), "confidence": "medium",
            "style": {"frame": "", "align": "center", "quote_style": quote_style(text), "emphasis": ""},
            "doubt": "ocr"}


def rejected_reading(why: str) -> dict:
    return {"text": "", "type": "none", "confidence": "high",
            "style": {"frame": "", "align": "", "quote_style": "", "emphasis": ""},
            "doubt": why}


def duration(cand: dict) -> float:
    t0, t1 = parse_tc(cand.get("in")), parse_tc(cand.get("out"))
    return (t1 - t0) if (t0 is not None and t1 is not None) else 0.0


def classify(cand: dict, text: str, conf: float, min_conf: float) -> tuple[str, dict]:
    """-> (class, reading) for one candidate, before duplicates are looked for."""
    if len(re.sub(r"[^a-z0-9]", "", norm(text))) < MIN_ALNUM:
        dark = "dark" in str(cand.get("detected_by") or "")
        if dark and duration(cand) >= SAFETY_SECONDS:
            return "unsettled", {}
        return "empty", rejected_reading("ocr: no text on the frame")
    if conf >= min_conf and words(text) >= 2:
        return "settled", settled_reading(text)
    return "unsettled", {}


def find_duplicates(rows: list[dict]) -> None:
    """Mark each candidate whose reading repeats the one before it. extract.py splits a card
    in two when a flash or a shot change lands inside it; both halves read the same."""
    anchor: dict | None = None
    for row in rows:
        if row["class"] == "empty" or words(row["ocr_text"]) < 2:
            anchor = None
            continue
        prev, anchor = anchor, row
        if prev is None:
            continue
        ratio = SequenceMatcher(None, norm(prev["ocr_text"]), norm(row["ocr_text"])).ratio()
        if ratio >= DUPLICATE_RATIO:
            src = prev.get("duplicate_of") or prev["id"]
            row["class"] = "duplicate"
            row["duplicate_of"] = src
            row["reading"] = rejected_reading(f"ocr: duplicate of {src}")
            anchor = row


# ---------------------------------------------------------------- file

HEADER = ("OCR pre-read of the extract.py candidates (pipeline/tools/ocr.py). Derived file.\n"
          "class: empty = no text found, settled = OCR read it, unsettled = the vision pass\n"
          "reads it, duplicate = the same card as an earlier candidate. `reading` is what\n"
          "transcribe.py --merge takes when no reader has answered for that id.")


def ocr_path(slug: str) -> Path:
    return extract_dir(slug) / "ocr.yaml"


def load_ocr(slug: str) -> dict:
    p = ocr_path(slug)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def unsettled_ids(doc: dict) -> list[str]:
    return [str(c["id"]) for c in (doc.get("candidates") or []) if c.get("class") == "unsettled"]


def ocr_readings(doc: dict) -> dict[str, dict]:
    """id -> reading, for every candidate OCR settled or rejected."""
    return {str(c["id"]): c["reading"] for c in (doc.get("candidates") or []) if c.get("reading")}


# ---------------------------------------------------------------- main

def run(slug: str, a: argparse.Namespace) -> dict:
    film = load_film(slug)
    cands = load_candidates(slug).get("cards") or []
    if not cands:
        raise SystemExit("candidates.yaml has no cards")
    cands = sorted(cands, key=lambda c: id_sort_key(str(c["id"])))
    gdir = extract_dir(slug) / "grabs"
    missing = [c for c in cands if not (gdir / f"{c['id']}.png").exists()]
    if missing:
        print_path = find_print(slug, film.meta, a.print_path)
        if print_path is None:
            raise SystemExit("grabs are missing and no print found: pass --print, set print.file "
                             "in film.yaml, or put the print under prints/<slug>/")
        make_grabs(slug, cands, print_path, {str(c["id"]) for c in missing})

    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    rows = []
    for c in cands:
        cid = str(c["id"])
        text, conf, lines = read_grab(engine, gdir / f"{cid}.png")
        cls, reading = classify(c, text, conf, a.min_conf)
        rows.append({"id": cid, "class": cls, "ocr_text": text, "ocr_conf": round(conf, 4),
                     "ocr_lines": lines, "duplicate_of": "", "reading": reading})
    find_duplicates(rows)

    counts = {k: sum(1 for r in rows if r["class"] == k)
              for k in ("empty", "settled", "unsettled", "duplicate")}
    doc = {"film": slug, "min_conf": a.min_conf, "counts": counts, "candidates": rows}
    print(f"{slug}: {len(rows)} candidates  empty {counts['empty']}  settled {counts['settled']}  "
          f"unsettled {counts['unsettled']}  duplicate {counts['duplicate']}")
    left = [r["id"] for r in rows if r["class"] == "unsettled"]
    print(f"  unsettled ({len(left)}): {', '.join(left) if left else 'none'}")
    if a.dry_run:
        print("  dry run; ocr.yaml untouched")
        return doc
    text_out = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=10 ** 6)
    body = "".join(f"# {line}\n" for line in HEADER.split("\n")) + text_out
    p = ocr_path(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8", newline="\n")
    print(f"  -> {rel(p)}")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--print", dest="print_path", help="the reference copy (default: film.yaml / prints/)")
    ap.add_argument("--min-conf", type=float, default=MIN_CONF,
                    help="lowest box confidence that still settles a card")
    ap.add_argument("--dry-run", action="store_true", help="print the counts, write nothing")
    a = ap.parse_args()
    run(a.slug, a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
