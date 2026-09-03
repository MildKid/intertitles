"""Vision-pass transcription of the candidate cards that extract.py found.

The reading itself is done by vision-model sub-agents (or a person); this script makes that
pass efficient and repeatable. Everything lives under out/<slug>/extract/:

    python pipeline/tools/transcribe.py <slug> --prepare [--print P]   grabs/<id>.png + batches/NN.{md,json}
    python pipeline/tools/transcribe.py <slug> --grabs-only            grabs/<id>.png and nothing else
    python pipeline/tools/transcribe.py <slug> --merge                 batches/*.response.json -> transcribed.yaml
    python pipeline/tools/transcribe.py <slug> --second-pass [--include-ocr]   batches/p2-NN.{md,json} for low-confidence (and OCR-settled) cards
    python pipeline/tools/transcribe.py <slug> --adjudicate            batches/adjudicate.md for pass-1/pass-2 disagreements
    python pipeline/tools/transcribe.py <slug> --commit [--dry-run]    transcribed.yaml -> data/films/<slug>/cards.yaml

Each batches/NN.md is the exact prompt one reader gets: image paths, the JSON shape, and where
to write the answer (batches/NN.response.json). A batch can be rerun alone. --merge reads
every response present, in this order of trust: adjudication > pass-1/pass-2 agreement >
pass 1 alone > the OCR pre-read. A candidate with no reading keeps empty text and confidence
low; the model is never allowed to renumber or drop a card.

Run ocr.py first and the vision pass gets smaller: --prepare then batches only the
candidates OCR could not settle, and --merge fills the rest in from ocr.yaml at the lowest
trust tier. --all ignores ocr.yaml and batches every candidate. Without ocr.yaml, every
stage behaves as it always has.

--commit writes cards.yaml through common.write_cards (fixed key order, `|-` text blocks). A
card already marked verified: true is never overwritten. Candidates the readers marked
type "none" (not an intertitle) are left out unless --include-rejects; their ids stay
unused, which is fine: ids are stable, gaps are allowed.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from common import (ALIGNS, CARD_TYPES, CONFIDENCES, FILMS, FRAME_STYLES, OUT, dump_cards,
                    find_print, id_sort_key, load_cards_doc, load_film, parse_tc, rel, write_cards)

GRAB_MAX_W = 1280
CONF_RANK = {"high": 2, "medium": 1, "low": 0, "": -1}


# ---------------------------------------------------------------- paths

def extract_dir(slug: str) -> Path:
    return OUT / slug / "extract"


def load_candidates(slug: str) -> dict:
    p = extract_dir(slug) / "candidates.yaml"
    if not p.exists():
        raise SystemExit(f"{p} not found; run extract.py first")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------- grabs

def grab_frame(print_path: Path, t: float, dest: Path) -> None:
    """Full-resolution frame at t, autocontrast (tone-preserving), width capped, PNG."""
    from PIL import Image, ImageOps

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(print_path), "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        check=True, capture_output=True,
    ).stdout
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = ImageOps.autocontrast(img, cutoff=0.5, preserve_tone=True)
    if img.width > GRAB_MAX_W:
        img = img.resize((GRAB_MAX_W, round(img.height * GRAB_MAX_W / img.width)), Image.LANCZOS)
    img.save(dest, optimize=True)


def stamp_id(src: Path, dest: Path, cid: str) -> None:
    """The reader's copy of a grab carries the card id in its top-left corner, so a reader can
    never attach a reading to the wrong id. The clean grab stays for scrub.py."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(img)
    size = max(18, img.height // 24)
    try:
        font = ImageFont.truetype("arial.ttf", size)
    except OSError:
        font = ImageFont.load_default()
    label = f"id {cid}"
    box = d.textbbox((0, 0), label, font=font)
    pad = size // 3
    d.rectangle([0, 0, box[2] + 2 * pad, box[3] + 2 * pad], fill=(0, 0, 0))
    d.text((pad, pad), label, fill=(255, 220, 0), font=font)
    img.save(dest, optimize=True)


def make_grabs(slug: str, cands: list[dict], print_path: Path, only: set[str] | None) -> Path:
    gdir = extract_dir(slug) / "grabs"
    ldir = gdir / "labelled"
    ldir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for c in cands:
        cid = str(c["id"])
        dest = gdir / f"{cid}.png"
        if only is not None and cid not in only:
            continue
        if dest.exists() and only is None:
            continue
        mid = (parse_tc(c["in"]) + parse_tc(c["out"])) / 2
        jobs.append((mid, dest))
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(lambda j: grab_frame(print_path, *j), jobs))
    stamped = 0
    for c in cands:
        cid = str(c["id"])
        src, dest = gdir / f"{cid}.png", ldir / f"{cid}.png"
        if src.exists() and (not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime):
            stamp_id(src, dest, cid)
            stamped += 1
    print(f"grabs: {len(jobs)} new, {stamped} labelled -> {gdir}")
    return gdir


# ---------------------------------------------------------------- prompts

PROMPT = """# Transcription batch {name}: {title} ({year})

Read each image listed at the end and return ONLY a JSON array with one object per image, in
the order listed. Every id must appear exactly once. Do not skip, merge, split, or renumber.

The images are full frames from a silent-film print. Most are intertitle cards (text on a
dark card). Some are false positives: a dark scene, a blank frame, a fade, or a card cut
by a shot change. Ornaments and borders at the frame edges matter; look at the whole frame.

Each image carries its id in a yellow label in the top-left corner ("id 042"). Read the
label on the image and use that id for that image's object. If you cannot open an image,
still return its object with empty text, confidence "low", and doubt "image not readable".
Never write a reading for an image you did not open.

Fields per object:
- "id": the id given for the image, unchanged.
- "text": the words on the card, with "\\n" exactly where the card breaks a line. Keep the
  card's punctuation, including quotation marks, dashes, and ellipses. Write the text in
  normal sentence capitalization even when the card is set in capitals (see "case").
  Keep proper capitals (names, "I"). Empty string when there is no readable text.
- "case": "as-is" when the card mixes upper and lower case and you copied it exactly;
  "all-caps" when the card is set entirely in capitals and you wrote it in sentence case.
- "type": "title" (the film's title card), "narrative" (scene-setting narration, no
  speaker), "dialogue" (something a character says), "insert" (a letter, note, sign,
  newspaper, telegram, or clock face photographed in the scene), "credit" (studio, cast,
  production, or a modern distributor / music credit), or "none" (not an intertitle: a
  dark scene, a blank frame, a flash).
- "style": an object with
    "frame": "none" (plain card), "rule" (a plain line or simple border), "ornate"
             (decorative border, corner ornaments, a studio device), or "illustrated"
             (a drawing or photograph behind or beside the text);
    "align": "center" or "left";
    "quote_style": the marks around speech, e.g. "double straight", "double curly",
                   "single", "dash", "none"; "" when there is no speech;
    "emphasis": a short note on italics, a larger word, small caps, letter-spacing; "" if none.
- "confidence": "high" when every word is certain; "medium" when one or two words are a best
  guess; "low" when any part is unreadable, cut off, or the frame is damaged or blurred.
- "doubt": when confidence is not "high", one line naming the doubtful word(s) and the
  alternative reading; "" otherwise.

Do not correct spelling, punctuation, or grammar; the card may be old-fashioned. Do not add
words that are cut off. Do not translate. Do not describe the image outside the fields.

Example object:
{{"id": "007", "text": "\\"You stole my watch.\\"", "case": "as-is", "type": "dialogue",
 "style": {{"frame": "none", "align": "center", "quote_style": "double straight", "emphasis": ""}},
 "confidence": "high", "doubt": ""}}

When you have read every image, write the JSON array (nothing else, no markdown fence) to
this file with the Write tool:

    {response}

Then reply with the single word DONE and the number of objects written. Do not put the
JSON or the card texts in your reply.

Images ({count}):
{images}
"""


def write_batch(slug: str, name: str, pass_no: int, ids: list[str], meta: dict, note: str = "") -> Path:
    bdir = extract_dir(slug) / "batches"
    bdir.mkdir(parents=True, exist_ok=True)
    gdir = extract_dir(slug) / "grabs"
    response = bdir / f"{name}.response.json"
    images = "\n".join(f'{i + 1}. id "{cid}": {(gdir / f"{cid}.png").as_posix()}' for i, cid in enumerate(ids))
    prompt = PROMPT.format(name=name, title=meta.get("title", slug), year=meta.get("year", ""),
                           response=response.as_posix(), count=len(ids), images=images)
    if note:
        head, _, rest = prompt.partition("\n")
        prompt = f"{head}\n\n{note}\n{rest}"
    (bdir / f"{name}.md").write_text(prompt, encoding="utf-8", newline="\n")
    (bdir / f"{name}.json").write_text(json.dumps({
        "batch": name, "pass": pass_no, "ids": ids,
        "images": {cid: (gdir / f"{cid}.png").as_posix() for cid in ids},
        "prompt": (bdir / f"{name}.md").as_posix(), "response": response.as_posix(),
    }, indent=1), encoding="utf-8", newline="\n")
    return bdir / f"{name}.md"


def chunk(ids: list[str], size: int) -> list[list[str]]:
    """Batches of about `size`, with the remainder spread so no batch is tiny."""
    if not ids:
        return []
    n = max(1, round(len(ids) / size))
    per, extra = divmod(len(ids), n)
    out, i = [], 0
    for b in range(n):
        k = per + (1 if b < extra else 0)
        out.append(ids[i:i + k])
        i += k
    return out


def ocr_filter(slug: str, ids: list[str], use_ocr: bool) -> tuple[list[str], str]:
    """-> (the ids the readers get, a note for the batch prompts). The full list when there is
    no ocr.yaml or --all was given."""
    if not use_ocr:
        return ids, ""
    import ocr                                       # imported here: ocr.py imports this module

    doc = ocr.load_ocr(slug)
    if not doc:
        return ids, ""
    left = [cid for cid in ids if cid in set(ocr.unsettled_ids(doc))]
    note = f"These are {len(left)} of {len(ids)} candidates; the rest were settled or rejected by OCR."
    return left, note


def prepare(slug: str, a: argparse.Namespace, grabs_only: bool = False) -> None:
    film = load_film(slug)
    cands = load_candidates(slug).get("cards") or []
    if not cands:
        raise SystemExit("candidates.yaml has no cards")
    print_path = find_print(slug, film.meta, a.print_path)
    if print_path is None:
        raise SystemExit("no print found: pass --print, set print.file in film.yaml, or put it under prints/<slug>/")
    only = set(a.only) if a.only else None
    make_grabs(slug, cands, print_path, only)
    if grabs_only:
        return
    ids = [str(c["id"]) for c in cands]
    ids, note = ocr_filter(slug, ids, not a.all)
    if note:
        print(f"  {note}")
    if not ids:
        print("  every candidate was settled or rejected by OCR; no batches to write")
        return
    for i, group in enumerate(chunk(ids, a.batch_size), start=1):
        p = write_batch(slug, f"{i:02d}", 1, group, film.meta, note)
        print(f"  batch {i:02d}: {len(group)} cards  {rel(p)}")


# ---------------------------------------------------------------- readings

def norm_text(t: str) -> str:
    t = (t or "").replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    t = t.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", t).strip().casefold()


def clean_reading(r: dict) -> dict:
    """Coerce one model object into the card fields; unknown values become blank."""
    st = r.get("style") or {}
    frame = str(st.get("frame") or "").lower()
    align = str(st.get("align") or "").lower()
    typ = str(r.get("type") or "").lower()
    conf = str(r.get("confidence") or "").lower()
    case = str(r.get("case") or "").lower()
    all_caps = True if case == "all-caps" else (False if case == "as-is" else None)
    return {
        "text": str(r.get("text") or "").replace("\r\n", "\n").rstrip("\n"),
        "type": typ if typ in CARD_TYPES + ("none",) else "",
        "style": {
            "frame": frame if frame in FRAME_STYLES else "",
            "align": align if align in ALIGNS else "",
            "quote_style": str(st.get("quote_style") or ""),
            "emphasis": str(st.get("emphasis") or ""),
            "all_caps": all_caps,
        },
        "confidence": conf if conf in CONFIDENCES else "low",
        "doubt": str(r.get("doubt") or "").strip(),
    }


def load_responses(slug: str) -> tuple[dict, dict, dict, dict]:
    """-> (pass1, pass2, adjudicated, batch_of) keyed by card id."""
    bdir = extract_dir(slug) / "batches"
    p1, p2, adj, batch_of = {}, {}, {}, {}
    if not bdir.exists():
        return p1, p2, adj, batch_of
    for spec in sorted(bdir.glob("*.json")):
        if spec.name.endswith(".response.json"):
            continue
        meta = json.loads(spec.read_text(encoding="utf-8"))
        for cid in meta.get("ids", []):
            batch_of.setdefault(cid, []).append(meta["batch"])
    for resp in sorted(bdir.glob("*.response.json")):
        name = resp.name[: -len(".response.json")]
        try:
            data = json.loads(resp.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            print(f"  WARN {resp.name}: bad JSON ({e}); ignored")
            continue
        if isinstance(data, dict):
            data = data.get("cards") or data.get("readings") or [data]
        target = adj if name == "adjudicate" else (p2 if name.startswith("p2-") else p1)
        for r in data:
            if not isinstance(r, dict) or "id" not in r:
                continue
            cid = str(r["id"]).strip()
            if cid in target:
                print(f"  WARN {resp.name}: id {cid} given twice; keeping the first")
                continue
            target[cid] = clean_reading(r)
    return p1, p2, adj, batch_of


def merge_readings(r1: dict | None, r2: dict | None, ra: dict | None, batches: list[str],
                   r0: dict | None = None) -> dict:
    """r0 is the OCR pre-read: the lowest tier, used only when no reader answered for this id."""
    if r1 is None and r0 is not None and r2 is not None:
        # OCR settled it, then a second-pass reader looked: the reader wins, and agreement
        # with OCR is worth a step of confidence. Never adjudicated: OCR is not a reader.
        base = json.loads(json.dumps(r2))
        if norm_text(r0.get("text", "")) == norm_text(r2["text"]):
            base["confidence"] = max(r2["confidence"], "medium", key=lambda c: CONF_RANK[c])
            base["doubt"] = r2.get("doubt") or "ocr and pass 2 agree"
        else:
            base["doubt"] = "; ".join(d for d in [r2.get("doubt", ""), f"replaced ocr reading {r0.get('text', '')!r}"] if d)
        return base
    if r1 is None and r0 is not None:
        base = clean_reading(r0)
    elif r1 is None:
        base = clean_reading({})
        base["doubt"] = f"no reading returned (batch {batches[0] if batches else '?'})"
    else:
        base = json.loads(json.dumps(r1))
    if r2 is not None and r1 is not None:
        if norm_text(r1["text"]) == norm_text(r2["text"]):
            best = max(r1["confidence"], r2["confidence"], "medium", key=lambda c: CONF_RANK[c])
            if not r1["text"].strip():
                best = "low"                      # two readers agreeing they cannot read it is still unread
            base["confidence"] = best
            if best == "high":
                base["doubt"] = ""
            else:
                base["doubt"] = "; ".join(d for d in dict.fromkeys([r1["doubt"], r2["doubt"]]) if d) or "two passes agree"
            for k in ("frame", "align"):
                if not base["style"][k]:
                    base["style"][k] = r2["style"][k]
        else:
            base["confidence"] = "low"
            base["doubt"] = (f"pass 1: {r1['text']!r} | pass 2: {r2['text']!r}"
                             + "".join(f" | {d}" for d in (r1["doubt"], r2["doubt"]) if d))
            base["disagreement"] = {"pass1": r1["text"], "pass2": r2["text"]}
    if ra is not None:
        base["text"] = ra["text"]
        base["confidence"] = ra["confidence"]
        base["doubt"] = ra["doubt"]
        if ra["type"]:
            base["type"] = ra["type"]
        for k, v in ra["style"].items():
            if v not in ("", None):
                base["style"][k] = v
        base.pop("disagreement", None)
        base["adjudicated"] = True
    return base


def merge(slug: str, quiet: bool = False) -> dict:
    cdoc = load_candidates(slug)
    cands = cdoc.get("cards") or []
    p1, p2, adj, batch_of = load_responses(slug)
    import ocr                                       # imported here: ocr.py imports this module

    pre = ocr.ocr_readings(ocr.load_ocr(slug))
    known = {str(c["id"]) for c in cands}
    for src, name in ((p1, "pass 1"), (p2, "pass 2"), (adj, "adjudication")):
        for cid in src:
            if cid not in known:
                print(f"  WARN {name}: id {cid} is not a candidate; ignored")
    cards = []
    stats = {"total": len(cands), "high": 0, "medium": 0, "low": 0, "rejected": 0,
             "unread": 0, "ocr": 0, "disagree": 0, "adjudicated": 0}
    for c in cands:
        cid = str(c["id"])
        m = merge_readings(p1.get(cid), p2.get(cid), adj.get(cid), batch_of.get(cid, []), pre.get(cid))
        card = {"id": cid, "in": c.get("in"), "out": c.get("out"),
                "type": m["type"] or "dialogue", "text": m["text"], "context": "",
                "verified": False, "style": m["style"], "confidence": m["confidence"], "doubt": m["doubt"]}
        if m.get("disagreement"):
            card["disagreement"] = m["disagreement"]
            stats["disagree"] += 1
        if m.get("adjudicated"):
            stats["adjudicated"] += 1
        if cid not in p1:
            stats["ocr" if cid in pre else "unread"] += 1
        if card["type"] == "none":
            stats["rejected"] += 1
        else:
            stats[card["confidence"]] += 1
        cards.append(card)
    doc = {"film": slug, "print": cdoc.get("print", ""), "cards": cards}
    header = ("Merged vision readings for every extract.py candidate. Derived file: rerun\n"
              "`transcribe.py <slug> --merge` after any response changes.\n"
              "type none = not an intertitle; --commit leaves those out.")
    text_out = extract_dir(slug) / "transcribed.yaml"
    text_out.write_text(dump_cards(doc, header), encoding="utf-8", newline="\n")
    if not quiet:
        print(f"{slug}: {stats['total']} candidates -> {rel(text_out)}")
        print(f"  high {stats['high']}  medium {stats['medium']}  low {stats['low']}  "
              f"rejected {stats['rejected']}  unread {stats['unread']}  "
              f"from ocr {stats['ocr']}  "
              f"disagreements {stats['disagree']}  adjudicated {stats['adjudicated']}")
        lows = [c["id"] for c in cards if c["type"] != "none" and c["confidence"] == "low"]
        if lows:
            print(f"  low: {', '.join(lows)}")
        rej = [c["id"] for c in cards if c["type"] == "none"]
        if rej:
            print(f"  rejected: {', '.join(rej)}")
    return doc


# ---------------------------------------------------------------- second pass / adjudication

def second_pass(slug: str, a: argparse.Namespace) -> None:
    film = load_film(slug)
    doc = merge(slug, quiet=True)
    lows = [c["id"] for c in doc["cards"] if c["type"] != "none" and c["confidence"] == "low"]
    if a.include_ocr:
        # cards no reader has seen: OCR settled them (doubt "ocr"); a second-pass read checks them
        lows += [c["id"] for c in doc["cards"] if c["type"] != "none" and c["confidence"] != "low"
                 and str(c.get("doubt", "")).strip() == "ocr" and c["id"] not in lows]
        lows.sort(key=id_sort_key)
    if not lows:
        print("no low-confidence cards; nothing to do")
        return
    # A different grouping from pass 1: reversed order and a different batch size, so every
    # card sits next to different neighbours and no batch is a rerun of a pass-1 batch.
    size = max(4, a.batch_size - 3)
    groups = chunk(list(reversed(lows)), size)
    for i, group in enumerate(groups, start=1):
        p = write_batch(slug, f"p2-{i:02d}", 2, group, film.meta)
        print(f"  batch p2-{i:02d}: {len(group)} cards  {rel(p)}")
    print(f"{len(lows)} low-confidence cards in {len(groups)} second-pass batches")


ADJUDICATE = """# Adjudication: {title} ({year})

Two independent readers transcribed these intertitle frames and disagreed. For each card,
look at the image and decide the final reading. You may side with one reading, combine
them, or write a third reading if both are wrong. Line breaks: "\\n" exactly where the
card breaks a line. Keep the card's punctuation; sentence capitalization even when the
card is set in capitals; no corrections, no additions, no translation.

Return ONLY a JSON array, one object per card, in the order listed, every id exactly once:
{{"id": "...", "text": "...", "type": "title|narrative|dialogue|insert|credit|none",
 "confidence": "high|medium|low", "doubt": "one line: what stays uncertain, or empty"}}

Set confidence "high" only when you can read every word yourself. If the frame is
unreadable, keep the text empty and confidence "low" and say why in "doubt".

Write the JSON array (no markdown fence) to this file with the Write tool:

    {response}

Then reply with the single word DONE and the number of objects written.

Cards ({count}):
{cards}
"""


def adjudicate(slug: str) -> None:
    film = load_film(slug)
    doc = merge(slug, quiet=True)
    dis = [c for c in doc["cards"] if c.get("disagreement")]
    bdir = extract_dir(slug) / "batches"
    if not dis:
        print("no disagreements; nothing to adjudicate")
        return
    gdir = extract_dir(slug) / "grabs"
    blocks = []
    for i, c in enumerate(dis, start=1):
        d = c["disagreement"]
        blocks.append(f'{i}. id "{c["id"]}": {(gdir / (c["id"] + ".png")).as_posix()}\n'
                      f'   reading 1: {json.dumps(d["pass1"], ensure_ascii=False)}\n'
                      f'   reading 2: {json.dumps(d["pass2"], ensure_ascii=False)}\n'
                      f'   doubt: {c["doubt"]}')
    response = bdir / "adjudicate.response.json"
    text = ADJUDICATE.format(title=film.title, year=film.meta.get("year", ""), response=response.as_posix(),
                             count=len(dis), cards="\n".join(blocks))
    (bdir / "adjudicate.md").write_text(text, encoding="utf-8", newline="\n")
    (bdir / "adjudicate.json").write_text(json.dumps({"batch": "adjudicate", "pass": 3,
                                                      "ids": [c["id"] for c in dis],
                                                      "response": response.as_posix()}, indent=1),
                                          encoding="utf-8", newline="\n")
    print(f"{len(dis)} disagreements -> {rel(bdir / 'adjudicate.md')}")


# ---------------------------------------------------------------- commit

def set_extraction_status(slug: str, status: str) -> bool:
    """Text edit of film.yaml's extraction.status line, so comments and order survive."""
    p = FILMS / slug / "film.yaml"
    s = p.read_text(encoding="utf-8")
    new, n = re.subn(r"(?m)^(extraction:\s*\n(?:[ \t]+.*\n)*?[ \t]+status:)[ \t]*[\w-]*",
                     rf"\g<1> {status}", s, count=1)
    if n and new != s:
        p.write_text(new, encoding="utf-8", newline="\n")
    return bool(n)


def commit(slug: str, a: argparse.Namespace) -> None:
    src = extract_dir(slug) / "transcribed.yaml"
    if not src.exists():
        raise SystemExit(f"{src} not found; run --merge first")
    new_cards = (yaml.safe_load(src.read_text(encoding="utf-8")) or {}).get("cards") or []
    doc = load_cards_doc(slug)
    existing = {str(c["id"]): c for c in doc["cards"]}
    kept_verified, replaced, added, skipped, dropped = [], [], [], [], []
    merged: dict[str, dict] = dict(existing)
    for nc in new_cards:
        cid = str(nc["id"])
        if nc.get("type") == "none" and not a.include_rejects:
            skipped.append(cid)
            old = existing.get(cid)
            if old is not None and not old.get("verified"):
                # an earlier pass put this candidate in cards.yaml; the readers now reject it
                merged.pop(cid, None)
                dropped.append(cid)
            continue
        nc = dict(nc)
        nc.pop("disagreement", None)
        if nc.get("type") == "none":
            nc["type"] = "dialogue"
            nc["doubt"] = ("vision pass: not an intertitle; " + nc.get("doubt", "")).rstrip("; ")
        old = existing.get(cid)
        if old is not None and old.get("verified"):
            kept_verified.append(cid)
            continue
        if old is not None:
            for k in ("speaker", "context", "notes"):    # human-entered fields survive a re-transcription
                if old.get(k) and not nc.get(k):
                    nc[k] = old[k]
            replaced.append(cid)
        else:
            added.append(cid)
        merged[cid] = nc
    doc["cards"] = [merged[k] for k in sorted(merged, key=id_sort_key)]
    print(f"{slug}: {len(doc['cards'])} cards ({len(added)} new, {len(replaced)} replaced, "
          f"{len(kept_verified)} verified kept, {len(skipped)} rejects left out, "
          f"{len(dropped)} unverified cards dropped as rejects{': ' + ', '.join(dropped) if dropped else ''})")
    if a.dry_run:
        print("  dry run; cards.yaml untouched")
        return
    p = write_cards(slug, doc)
    if set_extraction_status(slug, "transcribed"):
        print("  film.yaml extraction.status: transcribed")
    print(f"  -> {rel(p)}")


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--prepare", action="store_true", help="make grabs and pass-1 batch prompts")
    ap.add_argument("--grabs-only", action="store_true", help="make the frame grabs, write no batches")
    ap.add_argument("--merge", action="store_true", help="merge every response into transcribed.yaml")
    ap.add_argument("--second-pass", action="store_true", help="batch prompts for low-confidence cards")
    ap.add_argument("--include-ocr", action="store_true", help="second pass also reads the cards OCR settled")
    ap.add_argument("--adjudicate", action="store_true", help="one prompt for pass-1/pass-2 disagreements")
    ap.add_argument("--commit", action="store_true", help="write transcribed.yaml into cards.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-rejects", action="store_true", help="commit candidates marked not-a-card too")
    ap.add_argument("--print", dest="print_path", help="the reference copy (default: film.yaml / prints/)")
    ap.add_argument("--batch-size", type=int, default=10, help="cards per reader, 8 to 12")
    ap.add_argument("--only", nargs="*", help="regrab just these ids")
    ap.add_argument("--all", action="store_true", help="batch every candidate, ignoring ocr.yaml")
    a = ap.parse_args()
    if not any((a.prepare, a.grabs_only, a.merge, a.second_pass, a.adjudicate, a.commit)):
        ap.error("pick one of --prepare, --grabs-only, --merge, --second-pass, --adjudicate, --commit")
    if a.prepare or a.grabs_only:
        prepare(a.slug, a, grabs_only=a.grabs_only and not a.prepare)
    if a.merge:
        merge(a.slug)
    if a.second_pass:
        second_pass(a.slug, a)
    if a.adjudicate:
        adjudicate(a.slug)
    if a.commit:
        commit(a.slug, a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
