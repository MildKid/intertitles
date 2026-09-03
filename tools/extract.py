"""First pass at finding the intertitle cards in a print.

    python tools/extract.py <slug> --print "D:/prints/sherlock-jr.mp4"

Writes out/<slug>/extract/candidates.yaml (a cards.yaml-shaped list with empty text)
and one thumbnail per candidate (out/<slug>/extract/<id>.jpg) for transcription.

Method: sample the print at a few frames per second as tiny grayscale images and flag
runs where the frame is mostly black with a little bright material (text on a card).
Tinted or bordered cards (Sherlock Jr. has ornamented cards) may need the thresholds
loosened with --dark and --bright; check the thumbnails and rerun.

What this does not do: read the text. Transcribe from the thumbnails (a human, or a
vision model pass), paste into films/<slug>/cards.yaml, then run lint.py. Timecodes here
are +/- one sample interval; tighten them against the print before locking the film.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

from common import OUT, fmt_tc, load_film

W, H = 160, 90


def sample_frames(print_path: Path, fps: float):
    """Yield (t_seconds, bytes) grayscale frames of W x H."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(print_path), "-vf", f"fps={fps},scale={W}:{H}",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = W * H
    i = 0
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(n)
        if len(buf) < n:
            break
        yield i / fps, buf
        i += 1
    proc.wait()


def classify(frame: bytes, dark_max: int, bright_min: int) -> tuple[float, float]:
    table = bytes([0 if v <= dark_max else (2 if v >= bright_min else 1) for v in range(256)])
    t = frame.translate(table)
    n = len(frame)
    return t.count(b"\x00") / n, t.count(b"\x02") / n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--print", dest="print_path", required=True)
    ap.add_argument("--fps", type=float, default=4.0, help="sampling rate")
    ap.add_argument("--dark", type=int, default=48, help="luma at or below this counts as black")
    ap.add_argument("--bright", type=int, default=170, help="luma at or above this counts as text")
    ap.add_argument("--min-dark", type=float, default=0.80, help="fraction of black pixels required")
    ap.add_argument("--min-bright", type=float, default=0.004)
    ap.add_argument("--max-bright", type=float, default=0.20)
    ap.add_argument("--min-seconds", type=float, default=0.75, help="shortest run to keep")
    a = ap.parse_args()

    film = load_film(a.slug)
    print_path = Path(a.print_path)
    if not print_path.exists():
        raise SystemExit(f"print not found: {print_path}")
    out_dir = OUT / film.slug / "extract"
    out_dir.mkdir(parents=True, exist_ok=True)

    runs: list[list[float]] = []
    current: list[float] | None = None
    step = 1 / a.fps
    for t, frame in sample_frames(print_path, a.fps):
        dark, bright = classify(frame, a.dark, a.bright)
        is_card = dark >= a.min_dark and a.min_bright <= bright <= a.max_bright
        if is_card:
            if current is None:
                current = [t, t + step]
            else:
                current[1] = t + step
        elif current is not None:
            runs.append(current)
            current = None
    if current is not None:
        runs.append(current)

    runs = [r for r in runs if r[1] - r[0] >= a.min_seconds]
    cards = []
    for i, (t0, t1) in enumerate(runs, start=1):
        cid = f"{i:03d}"
        mid = (t0 + t1) / 2
        thumb = out_dir / f"{cid}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{mid:.3f}", "-i", str(print_path),
                        "-frames:v", "1", "-q:v", "3", str(thumb)], check=True)
        cards.append({"id": cid, "in": fmt_tc(t0), "out": fmt_tc(t1), "type": "dialogue",
                      "text": "", "context": "", "thumb": thumb.name})
        print(f"  {cid}  {fmt_tc(t0)} -> {fmt_tc(t1)}  ({t1 - t0:.1f}s)")

    doc = {"film": film.slug, "print": str(print_path), "sampled_fps": a.fps,
           "note": "Candidate cards. Transcribe from the thumbnails, drop false positives, "
                   "tighten timecodes, then move into films/<slug>/cards.yaml.",
           "cards": cards}
    (out_dir / "candidates.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"{len(cards)} candidates -> {out_dir / 'candidates.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
