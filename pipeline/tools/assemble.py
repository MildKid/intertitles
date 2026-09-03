"""Burn rendered (or designer-made) cards into a copy of the reference print with ffmpeg.

    python pipeline/tools/assemble.py <slug> --lang es --print "D:/prints/sherlock-jr.mp4"
    python pipeline/tools/assemble.py <slug> --lang es --print ... --preview 00:03:00 00:04:30
    python pipeline/tools/assemble.py <slug> --lang es --print ... --layout translation-only

For each card, the original frames between `in` and `out` are covered by the card image
(full-frame overlay). Runtime is unchanged, which matters when the film is accompanied live.

Card image priority per card id:
  1. data/films/<slug>/cards/<lang>/<id>.png      designer's hand-made card
  2. out/<slug>/<lang>/<layout>/<id>.png      automatic render (run render.py first)
Cards with neither are left as they are in the print.

The print is never committed. The `print:` block in film.yaml records which file the
timecodes were made against; assemble refuses to run if the sha256 there is set and does
not match the file you pass in.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

from common import OUT, Film, designer_card, fmt_tc, load_film, parse_tc


def sha256(path: Path, limit_mb: int = 64) -> str:
    """Hash the first N MB: enough to identify a print without reading a multi-GB file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        h.update(f.read(limit_mb * 1024 * 1024))
    return h.hexdigest()


def probe_size(print_path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(print_path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def collect_cards(film: Film, lang: str, layout: str) -> list[tuple[object, Path, str]]:
    auto_dir = OUT / film.slug / lang / layout
    chosen = []
    for c in film.cards:
        if not c.timed:
            print(f"  {c.id}  untimed, skipped")
            continue
        d = designer_card(film.slug, lang, c.id)
        if d:
            chosen.append((c, d, "designer"))
            continue
        a = auto_dir / f"{c.id}.png"
        if a.exists():
            chosen.append((c, a, "auto"))
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--lang", required=True)
    ap.add_argument("--layout", default="stacked")
    ap.add_argument("--print", dest="print_path", required=True, help="reference print video file")
    ap.add_argument("--preview", nargs=2, metavar=("IN", "OUT"), help="only encode this range")
    ap.add_argument("--out", help="output file (default out/<slug>/<slug>.<lang>.<layout>.mp4)")
    ap.add_argument("--crf", default="18")
    ap.add_argument("--skip-hash-check", action="store_true")
    a = ap.parse_args()

    film = load_film(a.slug)
    print_path = Path(a.print_path)
    if not print_path.exists():
        raise SystemExit(f"print not found: {print_path}")

    expected = (film.meta.get("print") or {}).get("sha256") or ""
    if expected and not a.skip_hash_check:
        actual = sha256(print_path)
        if actual != expected:
            raise SystemExit(
                f"print hash mismatch: film.yaml says {expected[:12]}..., this file is {actual[:12]}...\n"
                "Timecodes belong to a specific print. Use that print, re-time the cards, or pass --skip-hash-check."
            )

    cards = collect_cards(film, a.lang, a.layout)
    if not cards:
        raise SystemExit("no card images found: run render.py first, or add designer cards")

    pw, ph = probe_size(print_path)
    fw, fh = film.frame
    if (pw, ph) != (fw, fh):
        print(f"note: print is {pw}x{ph}, cards are {fw}x{fh}; cards will be scaled to the print")

    offset = 0.0
    seek_args: list[str] = []
    if a.preview:
        t0, t1 = parse_tc(a.preview[0]), parse_tc(a.preview[1])
        offset = t0
        seek_args = ["-ss", fmt_tc(t0), "-to", fmt_tc(t1)]
        cards = [(c, p, k) for (c, p, k) in cards if c.tc_out > t0 and c.tc_in < t1]

    suffix = ".preview" if a.preview else ""
    out = Path(a.out) if a.out else OUT / film.slug / f"{film.slug}.{a.lang}.{a.layout}{suffix}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    inputs: list[str] = []
    chain: list[str] = []
    prev = "[0:v]"
    for i, (c, img, kind) in enumerate(cards, start=1):
        inputs += ["-i", str(img)]
        scaled = f"[c{i}]"
        chain.append(f"[{i}:v]scale={pw}:{ph}{scaled}")
        label = f"[v{i}]"
        t_in, t_out = max(c.tc_in - offset, 0), c.tc_out - offset
        chain.append(f"{prev}{scaled}overlay=0:0:enable='between(t,{t_in:.3f},{t_out:.3f})'{label}")
        prev = label
        print(f"  {c.id}  {fmt_tc(c.tc_in)} -> {fmt_tc(c.tc_out)}  {kind}")

    script = out.with_suffix(".filter")
    script.write_text(";\n".join(chain), encoding="utf-8")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats",
           *seek_args, "-i", str(print_path), *inputs,
           "-filter_complex_script", str(script),
           "-map", prev, "-map", "0:a?",
           "-c:v", "libx264", "-crf", a.crf, "-preset", "medium", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-movflags", "+faststart", str(out)]
    print(f"encoding {len(cards)} cards -> {out}")
    subprocess.run(cmd, check=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
