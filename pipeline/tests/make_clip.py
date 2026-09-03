"""Generate the synthetic 10-second print for data/films/_example, then run the whole pipeline on it.

    python pipeline/tests/make_clip.py          # builds out/_example/print.mp4 and runs export/lint/render/assemble
    python pipeline/tests/make_clip.py --clip   # only the clip

The clip is an ffmpeg test pattern with three black "cards" at the fixture's timecodes,
so a rendered card that lands anywhere else is visible as a bug.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "_example"
CLIP = OUT / "print.mp4"
PY = sys.executable


def font_arg() -> str:
    """drawtext needs a font file where fontconfig is absent (Windows ffmpeg builds)."""
    for f in (r"C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(f).exists():
            return "fontfile=" + f.replace(":", "\\\\:") + ":"   # a drive colon needs two escape levels in a filtergraph
    return ""


def make_clip() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    # test pattern, with the card windows blacked out and labelled so a miss is obvious
    # Each card window carries its own words, so a tool that reads the frames (ocr.py) sees
    # three different cards rather than three copies of one.
    windows = ((1, 3.5, "THE EXAMPLE REEL - original card should be covered"),
               (4, 7, "MORNING IN THE BOOTH - original card should be covered"),
               (8, 9.5, "WAKE UP THE PICTURE IS ON - original card should be covered"))
    vf = ",".join(
        [f"drawbox=enable='between(t,{t0},{t1})':x=0:y=0:w=iw:h=ih:color=black:t=fill"
         for t0, t1, _ in windows]
        + [f"drawtext=enable='between(t,{t0},{t1})':" + font_arg() +
           f"text='{text}':fontcolor=white:fontsize=36:x=(w-tw)/2:y=h-80"
           for t0, t1, text in windows]
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=duration=10:size=1280x720:rate=24",
         "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(CLIP)],
        check=True,
    )
    print(f"clip -> {CLIP}")
    return CLIP


def run(*args: str) -> None:
    print("$", " ".join(args))
    subprocess.run([PY, *args], check=True, cwd=ROOT)


def main() -> int:
    make_clip()
    if "--clip" in sys.argv:
        return 0
    run("pipeline/tools/export_po.py", "_example")
    run("pipeline/tools/lint.py", "_example")
    run("pipeline/tools/render.py", "_example", "--lang", "es-MX")
    run("pipeline/tools/assemble.py", "_example", "--lang", "es-MX", "--print", str(CLIP))
    # frame grabs at the middle of each card for eyeballing
    for name, t in (("001", 2.2), ("002", 5.5), ("003", 8.7), ("gap", 3.75)):
        png = OUT / f"check-{name}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i",
                        str(OUT / "_example.es-MX.stacked.mp4"), "-frames:v", "1", str(png)], check=True)
        print(f"check -> {png}")
    run("pipeline/tests/test_scrub.py")      # the card-verification server, on a temp copy of the data
    run("pipeline/tests/test_align.py")      # re-timing onto a deliberately shifted copy
    run("pipeline/tests/test_ocr.py")        # the OCR pre-read, on a temp output root
    return 0


if __name__ == "__main__":
    sys.exit(main())
