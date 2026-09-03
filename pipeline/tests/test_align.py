"""Drive pipeline/tools/align.py across a deliberately shifted copy of a synthetic print.

    python pipeline/tests/test_align.py

Builds a ten-second clip with three distinguishable black cards at the fixture film's
timecodes, then a second copy trimmed by two seconds. Aligning the fixture onto the trimmed
copy has to report a median offset of -2.0 s. The fixture's data is copied to a temp
directory (common.py reads INTERTITLES_DATA at import, so align runs as a subprocess).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CARDS = ((1.0, 3.5, "CARD ONE"), (4.0, 7.0, "SECOND CARD HERE"), (8.0, 9.5, "THIRD"))


def font() -> str:
    for f in (r"C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(f).exists():
            return "fontfile=" + f.replace(":", "\\\\:") + ":"
    return ""


def build(path: Path) -> None:
    vf = ",".join(
        [f"drawbox=enable='between(t,{a},{b})':x=0:y=0:w=iw:h=ih:color=black:t=fill" for a, b, _ in CARDS] +
        [f"drawtext=enable='between(t,{a},{b})':{font()}text='{txt}':fontcolor=white:"
         f"fontsize=48:x=(w-tw)/2:y=(h-th)/2" for a, b, txt in CARDS])
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=duration=10:size=1280x720:rate=24", "-vf", vf,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(path)], check=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="align-test-"))
    try:
        old, new = tmp / "old.mp4", tmp / "new.mp4"
        build(old)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "2", "-i", str(old),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(new)], check=True)
        data = tmp / "data"
        shutil.copytree(ROOT / "data", data)
        film = data / "films" / "_example" / "film.yaml"
        film.write_text(film.read_text(encoding="utf-8").replace(
            'file: ""', f'file: "{old.as_posix()}"'), encoding="utf-8")
        report = tmp / "report.json"
        env = {**os.environ, "INTERTITLES_DATA": str(data), "PYTHONIOENCODING": "utf-8"}
        subprocess.run([sys.executable, str(ROOT / "pipeline" / "tools" / "align.py"), "_example",
                        "--print", str(new), "--window", "6", "--json", str(report)],
                       check=True, cwd=ROOT, env=env)
        median = json.loads(report.read_text(encoding="utf-8"))["summary"]["median_offset"]
        ok = abs(median + 2.0) <= 0.1
        print(("  ok   " if ok else "  FAIL ") + f"median offset {median:+.3f} (want -2.000)")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
