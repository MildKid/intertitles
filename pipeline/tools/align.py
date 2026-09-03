"""Map the timecodes of one print onto another copy of the same film.

    python pipeline/tools/align.py <slug> --print NEW-FILE [--window S] [--apply] [--json PATH]

Timecodes belong to a specific print (docs/design.md, "Why timecodes are tied to a specific
print"). When a better copy turns up, the text and the translations survive untouched and only
`in`/`out` and the `print:` block have to move. This measures that move.

For each timed card it takes a small grayscale thumbnail of the card's own frames in the OLD
print, finds where those frames sit in the NEW one, then walks frame by frame to the first and
last frame that still match, so the reported `in` is the card's start and not just the best
match somewhere in the middle. It also grabs a frame from just before the card, which is what
"not this card" looks like, and uses the gap between the two to set the match threshold.

Without `--apply` nothing on disk changes: read the table, decide whether the offset is one
constant (a different leader) or a drift (a different cut), then run it again with `--apply`.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

from common import (ROOT, find_print, fmt_tc, load_cards_doc, load_film, probe, sha256_head,
                    write_cards, write_film_print)

W, H = 64, 36                       # thumbnail size; one frame is W*H bytes of gray
FRAME_BYTES = W * H
COARSE_FPS = 2.0                    # first pass step, 0.5 s
NORM_CONTRAST = 32.0                # thumbnails are scaled to this spread before comparing
NEG_LEAD = 1.0                      # seconds before `in` for the "not the card" frame
EDGE_FRAC = 0.35                    # how far from the card towards "not the card" still counts
OFFSET_TOL = 0.5                    # flag a card whose offset differs from the median by more
MIN_CONFIDENCE = 0.25               # flag a match this flat
MAX_EDGE_WALK = 20.0                # never walk more than this far looking for a card edge


# ---------------------------------------------------------------- frames

def _run(args: list[str]) -> bytes:
    p = subprocess.run(args, capture_output=True)
    if p.returncode != 0 and not p.stdout:
        raise RuntimeError((p.stderr or b"").decode("utf-8", "replace").strip()[-400:])
    return p.stdout


def prep(buf: bytes) -> list[float]:
    """A thumbnail, mean-centred and scaled to a fixed contrast.

    Two transfers of the same film differ in brightness and contrast, so raw pixel values
    would report a difference where the picture is the same. What survives grading is the
    shape of the light and dark: normalising each thumbnail compares that instead.
    """
    n = len(buf)
    mean = sum(buf) / n
    spread = (sum((x - mean) ** 2 for x in buf) / n) ** 0.5
    gain = NORM_CONTRAST / spread if spread > 1.0 else 1.0
    return [(x - mean) * gain for x in buf]


def grab(path: Path, t: float) -> list[float] | None:
    """One normalised W*H gray frame at t seconds, or None past the end of the file."""
    out = _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
                "-frames:v", "1", "-an", "-sn", "-vf", f"scale={W}:{H},format=gray",
                "-f", "rawvideo", "-"])
    return prep(out[:FRAME_BYTES]) if len(out) >= FRAME_BYTES else None


def sequence(path: Path, start: float, length: float, rate: float) -> list[tuple[float, list[float]]]:
    """Decode one segment once and slice it into (time, frame) pairs at `rate` frames a second."""
    start = max(0.0, start)
    if length <= 0:
        return []
    out = _run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
                "-i", str(path), "-an", "-sn",
                "-vf", f"fps={rate:.6f},scale={W}:{H},format=gray", "-f", "rawvideo", "-"])
    n = len(out) // FRAME_BYTES
    return [(start + i / rate, prep(out[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]))
            for i in range(n)]


def distance(a: list[float], b: list[float]) -> float:
    """Mean absolute difference between two normalised thumbnails. Lower is a better match."""
    total = 0.0
    for x, y in zip(a, b):
        total += x - y if x > y else y - x
    return total / len(a)


# ---------------------------------------------------------------- matching

def coarse(frames: list[tuple[float, list[float]]], ref: list[float], span: float) -> tuple[float, float, float]:
    """Best time, its distance, and the best distance clear of the card it landed on.

    The runner-up has to come from outside the matched card, otherwise it would just be the
    frame next door inside the same card and every match would look decisive.
    """
    scored = [(distance(f, ref), t) for t, f in frames]
    best_d, best_t = min(scored)
    keep_out = max(2.0, span)
    others = [d for d, t in scored if abs(t - best_t) >= keep_out]
    return best_t, best_d, (min(others) if others else best_d)


def walk(frames: list[tuple[float, list[float]]], ref: list[float], seed: int, threshold: float,
         step: int) -> int:
    """From `seed`, keep going while the frames still match, and return the last index that does."""
    i = seed
    while 0 <= i + step < len(frames) and distance(frames[i + step][1], ref) <= threshold:
        i += step
        if abs(frames[i][0] - frames[seed][0]) > MAX_EDGE_WALK:
            break
    return i


def nearest_match(frames: list[tuple[float, list[float]]], ref: list[float], around: float,
                  threshold: float) -> int:
    """The index closest to `around` that matches, falling back to the plain best match."""
    hits = [i for i, (t, f) in enumerate(frames) if distance(f, ref) <= threshold]
    if hits:
        return min(hits, key=lambda i: abs(frames[i][0] - around))
    return min(range(len(frames)), key=lambda i: distance(frames[i][1], ref))


def align_card(old: Path, new: Path, tc_in: float, tc_out: float | None, window: float,
               fps: float, new_duration: float) -> dict | None:
    """Where this card sits in the new print. None when its frames cannot be read at all."""
    span = (tc_out - tc_in) if tc_out is not None else 2.0
    ref_in = grab(old, min(tc_in + 0.5, tc_in + span * 0.5))
    if ref_in is None:
        return None
    ref_out = grab(old, max(tc_out - 0.5, tc_in + span * 0.5)) if tc_out is not None else None
    neg = grab(old, max(0.0, tc_in - NEG_LEAD))

    # pass 1: half-second steps across the search window
    frames = sequence(new, tc_in - window, 2 * window + span, COARSE_FPS)
    if not frames:
        return None
    best_t, best_d, second_d = coarse(frames, ref_in, span)
    confidence = (second_d - best_d) / max(best_d, 1.0)

    # how different "not the card" is sets how far a frame may drift and still be the card
    contrast = max((distance(ref_in, neg) if neg else 0.0) - best_d, 2.0)
    threshold = best_d + EDGE_FRAC * min(contrast, 60.0)

    # pass 2: the film's own frame rate, over a segment long enough to hold the whole card
    back = min(span, 8.0) + 1.5
    fine = sequence(new, best_t - back, back + span + 4.0, fps)
    if not fine:
        return None
    seed = nearest_match(fine, ref_in, best_t, threshold)
    first = walk(fine, ref_in, seed, threshold, -1)
    last = walk(fine, ref_in, seed, threshold, +1)
    new_in = fine[first][0]

    new_out = None
    if ref_out is not None:
        tail = min(range(first, last + 1), key=lambda i: distance(fine[i][1], ref_out))
        thr_out = max(threshold, distance(fine[tail][1], ref_out) + EDGE_FRAC * min(contrast, 60.0))
        new_out = fine[walk(fine, ref_out, tail, thr_out, +1)][0] + 1.0 / fps
    if new_out is None or new_out <= new_in:
        new_out = fine[last][0] + 1.0 / fps
    new_out = min(new_out, new_duration) if new_duration else new_out

    return {"new_in": new_in, "new_out": new_out, "distance": round(best_d, 2),
            "confidence": round(confidence, 3), "threshold": round(threshold, 2),
            "truncated": first == 0 or last == len(fine) - 1}


# ---------------------------------------------------------------- report

def flags_for(row: dict, median: float) -> list[str]:
    f = []
    if row.get("offset") is not None and abs(row["offset"] - median) > OFFSET_TOL:
        f.append("offset")
    if row.get("confidence", 0) < MIN_CONFIDENCE:
        f.append("flat")
    if row.get("truncated"):
        f.append("edge")
    return f


def table(rows: list[dict], median: float) -> None:
    head = f"{'id':<6} {'old in':>12} {'new in':>12} {'offset':>8} {'old dur':>8} {'new dur':>8} {'conf':>6}  flags"
    print(head)
    print("-" * len(head))
    for r in rows:
        if r.get("new_in") is None:
            print(f"{r['id']:<6} {r['old_in']:>12} {'-':>12} {'-':>8} {'-':>8} {'-':>8} {'-':>6}  no match")
            continue
        print(f"{r['id']:<6} {r['old_in']:>12} {r['new_in_tc']:>12} {r['offset']:>+8.3f} "
              f"{r['old_duration']:>8.3f} {r['new_duration']:>8.3f} {r['confidence']:>6.2f}"
              f"  {' '.join(r['flags'])}")


# ---------------------------------------------------------------- apply

def apply_cards(slug: str, rows: list[dict]) -> Path:
    moved = {r["id"]: r for r in rows if r.get("new_in") is not None}
    doc = load_cards_doc(slug)
    for raw in doc["cards"]:
        r = moved.get(str(raw.get("id")))
        if not r:
            continue
        raw["in"] = r["new_in_tc"]
        raw["out"] = r["new_out_tc"]              # verified is never touched: a person set it
    return write_cards(slug, doc)


def apply_film(slug: str, new: Path, info: dict, args) -> Path:
    try:
        rel = str(new.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        rel = str(new.resolve()).replace("\\", "/")
    values = {
        "file": rel,
        "sha256": sha256_head(new),
        "fps": info["fps"],
        "width": info["width"],
        "height": info["height"],
        "duration": fmt_tc(info["duration"]),
        "size_bytes": new.stat().st_size,
        "source": args.source or "",
        "source_file": args.source_file or "",
        "downloaded": args.downloaded or "",
        "why": args.why or "",
    }
    return write_film_print(slug, values)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--print", dest="new_print", required=True, help="the copy to align onto")
    ap.add_argument("--window", type=float, default=30.0, help="search +/- this many seconds (default 30)")
    ap.add_argument("--apply", action="store_true", help="write the new timecodes and print: block")
    ap.add_argument("--json", dest="json_path", help="write the same report as JSON")
    ap.add_argument("--source", default="", help="with --apply: print.source for the new file")
    ap.add_argument("--source-file", default="", help="with --apply: print.source_file")
    ap.add_argument("--downloaded", default="", help="with --apply: print.downloaded (YYYY-MM-DD)")
    ap.add_argument("--why", default="", help="with --apply: print.why")
    a = ap.parse_args()

    film = load_film(a.slug)
    old = find_print(a.slug, film.meta)
    if old is None:
        raise SystemExit("no old print: set print.file in film.yaml or put the file under prints/<slug>/")
    new = Path(a.new_print)
    if not new.exists():
        raise SystemExit(f"new print not found: {new}")
    old, new = Path(old).resolve(), new.resolve()
    if sha256_head(old) == sha256_head(new):
        raise SystemExit(f"{new.name} is the print the cards are already timed against; nothing to align")

    old_info, new_info = probe(old), probe(new)
    fps = float(new_info.get("fps") or 0) or 24.0
    print(f"old  {old}\n     {old_info['width']}x{old_info['height']} {old_info['fps']} fps  {fmt_tc(old_info['duration'])}")
    print(f"new  {new}\n     {new_info['width']}x{new_info['height']} {new_info['fps']} fps  {fmt_tc(new_info['duration'])}")
    timed = [c for c in film.cards if c.tc_in is not None]
    print(f"{len(timed)} of {len(film.cards)} cards timed; searching +/- {a.window:g}s\n", flush=True)

    rows: list[dict] = []
    for c in timed:
        row = {"id": c.id, "old_in": fmt_tc(c.tc_in), "old_in_s": round(c.tc_in, 3),
               "old_duration": round(c.duration, 3) if c.duration is not None else None,
               "new_in": None, "flags": ["no match"]}
        try:
            m = align_card(old, new, c.tc_in, c.tc_out, a.window, fps, new_info["duration"])
        except RuntimeError as e:
            m = None
            row["error"] = str(e)
        if m:
            row.update(m)
            row["new_in_tc"] = fmt_tc(m["new_in"])
            row["new_out_tc"] = fmt_tc(m["new_out"])
            row["new_in"] = round(m["new_in"], 3)
            row["new_out"] = round(m["new_out"], 3)
            row["new_duration"] = round(m["new_out"] - m["new_in"], 3)
            row["offset"] = round(m["new_in"] - c.tc_in, 3)
        rows.append(row)
        print(f"  {c.id}  {row.get('offset', '-')}".ljust(40) + "\r", end="", flush=True)

    offsets = [r["offset"] for r in rows if r.get("new_in") is not None]
    if not offsets:
        raise SystemExit("no card matched; check that the two files are the same film")
    print(" " * 40 + chr(13), end="")
    median = statistics.median(offsets)
    for r in rows:
        r["flags"] = flags_for(r, median) if r.get("new_in") is not None else ["no match"]
    table(rows, median)

    flagged = [r for r in rows if r["flags"]]
    drift = rows[-1].get("offset", 0) - rows[0].get("offset", 0) if len(offsets) > 1 else 0.0
    summary = {
        "slug": a.slug, "old_print": str(old), "new_print": str(new),
        "old": old_info, "new": new_info, "window": a.window,
        "cards": len(rows), "matched": len(offsets), "flagged": len(flagged),
        "median_offset": round(median, 3), "min_offset": round(min(offsets), 3),
        "max_offset": round(max(offsets), 3),
        "offset_first_to_last": round(drift, 3),
        "duration_delta": round(new_info["duration"] - old_info["duration"], 3),
    }
    print(f"\nmedian offset {median:+.3f}s   min {min(offsets):+.3f}   max {max(offsets):+.3f}   "
          f"spread {max(offsets) - min(offsets):.3f}")
    print(f"{len(flagged)} of {len(rows)} cards flagged; first-to-last offset change {drift:+.3f}s")
    print(f"old runs {fmt_tc(old_info['duration'])}, new runs {fmt_tc(new_info['duration'])} "
          f"({summary['duration_delta']:+.3f}s)")

    if a.json_path:
        Path(a.json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json_path).write_text(json.dumps({"summary": summary, "cards": rows}, indent=2),
                                     encoding="utf-8")
        print(f"json -> {a.json_path}")

    if a.apply:
        p1 = apply_cards(a.slug, rows)
        p2 = apply_film(a.slug, new, new_info, a)
        print(f"\nwrote {p1}\nwrote {p2}")
        status = (film.meta.get("print") or {}).get("status") or "none"
        print(f"print.status is still `{status}`: set it by hand. Only you know whether this file "
              "is the copy that will be projected (`projection`) or another reference copy "
              "(`reference`).")
        if flagged:
            print(f"{len(flagged)} cards were flagged; check them in scrub.py before trusting them.")
    else:
        print("\nnothing written. Re-run with --apply to move the timecodes and the print: block.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
