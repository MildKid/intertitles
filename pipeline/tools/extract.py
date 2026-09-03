"""First pass at finding the intertitle cards in a print.

    python pipeline/tools/extract.py <slug> --print "prints/<slug>/<slug>.mp4"

Writes out/<slug>/extract/candidates.yaml (a cards.yaml-shaped list with empty text)
and one thumbnail per candidate (out/<slug>/extract/<id>.jpg). transcribe.py takes it from there.

Method: sample the print at a few frames per second as tiny grayscale images and flag a
run of samples when either rule holds for at least --min-seconds:

  still   the frame barely changes from the previous sample. A title card is a static frame;
          live action always moves. Measured on a 2x2 box-downsampled frame so film grain
          counts less; print weave and flicker still add a little, so --still is a few units
          above zero. This rule finds art titles (text over an illustration) that the dark
          rule cannot.
  dark    the frame is mostly black with a little bright material (text on a plain card).
          Catches a plain card even when the print jitters.

A candidate that is a still live-action shot, a fade, or an insert is expected; the vision
pass in transcribe.py marks those and --commit leaves them out. Missing a card is the
expensive failure, so lean toward more candidates. Timecodes here are +/- one sample
interval; scrub.py tightens them against the print.

Tuning: --still 2.5 and the dark defaults suit the three 2026 reference copies. A very
grainy or unsteady print may need --still 3.5; a clean one, 1.5. Check the candidate count
against what the film is known to have, and look at the thumbnails.
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
    """-> (fraction of pixels at or below dark_max, fraction at or above bright_min)."""
    table = bytes([0 if v <= dark_max else (2 if v >= bright_min else 1) for v in range(256)])
    t = frame.translate(table)
    n = len(frame)
    return t.count(b"\x00") / n, t.count(b"\x02") / n


def downsample(frame: bytes) -> list[int]:
    """2x2 box average -> (W/2) x (H/2) list of ints. Grain averages out; text edges survive."""
    out = []
    for y in range(0, H - 1, 2):
        r0 = frame[y * W:(y + 1) * W]
        r1 = frame[(y + 1) * W:(y + 2) * W]
        for x in range(0, W - 1, 2):
            out.append((r0[x] + r0[x + 1] + r1[x] + r1[x + 1]) >> 2)
    return out


def motion(a: list[int], b: list[int]) -> float:
    """Mean absolute difference between two downsampled frames (0 = identical)."""
    return sum(abs(p - q) for p, q in zip(a, b)) / len(a)


def merge_runs(runs: list[list[float]], gap: float) -> list[list[float]]:
    """Join runs separated by less than `gap` seconds (a flash frame inside one card)."""
    runs = sorted(runs)
    out: list[list[float]] = []
    for r in runs:
        if out and r[0] - out[-1][1] < gap:
            out[-1][1] = max(out[-1][1], r[1])
        else:
            out.append(list(r))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--print", dest="print_path", required=True)
    ap.add_argument("--fps", type=float, default=4.0, help="sampling rate")
    ap.add_argument("--still", type=float, default=2.5,
                    help="motion at or below this counts as a still frame; 0 disables the rule")
    ap.add_argument("--dark", type=int, default=60, help="luma at or below this counts as black")
    ap.add_argument("--bright", type=int, default=130, help="luma at or above this counts as text")
    ap.add_argument("--min-dark", type=float, default=0.80, help="fraction of black pixels required")
    ap.add_argument("--min-bright", type=float, default=0.002)
    ap.add_argument("--max-bright", type=float, default=0.25)
    ap.add_argument("--min-seconds", type=float, default=0.75, help="shortest run to keep")
    ap.add_argument("--gap", type=float, default=0.3, help="join runs closer than this")
    ap.add_argument("--no-dark", action="store_true", help="use the still rule only")
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
    prev: list[int] | None = None
    reasons: dict[float, set[str]] = {}
    for t, frame in sample_frames(print_path, a.fps):
        small = downsample(frame)
        why = set()
        if not a.no_dark:
            dark, bright = classify(frame, a.dark, a.bright)
            if dark >= a.min_dark and a.min_bright <= bright <= a.max_bright:
                why.add("dark")
        if a.still > 0 and prev is not None and motion(small, prev) <= a.still:
            why.add("still")
        prev = small
        if why:
            # a still sample means the *previous* sample already showed this frame
            t0 = t - step if "still" in why else t
            if current is None:
                current = [t0, t + step]
                reasons[current[0]] = set(why)
            else:
                current[1] = t + step
                reasons[current[0]] |= why
        elif current is not None:
            runs.append(current)
            current = None
    if current is not None:
        runs.append(current)

    runs = merge_runs(runs, a.gap)
    runs = [r for r in runs if r[1] - r[0] >= a.min_seconds]
    cards = []
    for i, (t0, t1) in enumerate(runs, start=1):
        cid = f"{i:03d}"
        mid = (t0 + t1) / 2
        thumb = out_dir / f"{cid}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{mid:.3f}", "-i", str(print_path),
                        "-frames:v", "1", "-q:v", "3", str(thumb)], check=True)
        why = ",".join(sorted(reasons.get(t0, {"merged"})))
        cards.append({"id": cid, "in": fmt_tc(max(0.0, t0)), "out": fmt_tc(t1), "type": "dialogue",
                      "text": "", "context": "", "thumb": thumb.name, "detected_by": why})
        print(f"  {cid}  {fmt_tc(max(0.0, t0))} -> {fmt_tc(t1)}  ({t1 - t0:.1f}s)  {why}")

    doc = {"film": film.slug, "print": str(print_path), "sampled_fps": a.fps,
           "settings": {"still": a.still, "dark": a.dark, "bright": a.bright, "min_dark": a.min_dark,
                        "min_bright": a.min_bright, "max_bright": a.max_bright,
                        "min_seconds": a.min_seconds, "gap": a.gap},
           "note": "Candidate cards. transcribe.py --prepare makes grabs and reader batches; "
                   "--commit moves the readings into data/films/<slug>/cards.yaml.",
           "cards": cards}
    (out_dir / "candidates.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"{len(cards)} candidates -> {out_dir / 'candidates.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
