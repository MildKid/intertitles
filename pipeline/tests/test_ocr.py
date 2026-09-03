"""Run extract.py and ocr.py over the fixture clip and check the OCR pre-read.

    python pipeline/tests/test_ocr.py

The candidates and the grabs are made from scratch in a temp output root (INTERTITLES_OUT),
so the test also covers ocr.py making the grabs itself when transcribe.py --prepare has not
run yet. The fixture's three cards are rendered text, so OCR has to find words on all three:
none of them may come back "empty". The test then checks that transcribe.py --prepare
batches only the unsettled candidates and that --merge takes the OCR readings.

Exits 0 when everything passes, 1 otherwise. make_clip.py runs it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CLIP = ROOT / "out" / "_example" / "print.mp4"
SLUG = "_example"
PY = sys.executable

FAILURES: list[str] = []


def check(ok: bool, what: str) -> bool:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        FAILURES.append(what)
    return ok


def run(env: dict, *args: str) -> str:
    print("$", " ".join(args))
    p = subprocess.run([PY, *args], cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(p.stdout)
        print(p.stderr, file=sys.stderr)
        raise SystemExit(f"{args[0]} failed with {p.returncode}")
    return p.stdout


def main() -> int:
    if not CLIP.exists():
        raise SystemExit(f"{CLIP} not found; run pipeline/tests/make_clip.py first")
    tmp = Path(tempfile.mkdtemp(prefix="intertitles-ocr-"))
    env = dict(os.environ, INTERTITLES_OUT=str(tmp), PYTHONIOENCODING="utf-8")
    try:
        run(env, "pipeline/tools/extract.py", SLUG, "--print", str(CLIP))
        cands = yaml.safe_load((tmp / SLUG / "extract" / "candidates.yaml").read_text(encoding="utf-8"))
        check(len(cands["cards"]) == 3, f"extract found the fixture's 3 cards ({len(cands['cards'])})")

        out = run(env, "pipeline/tools/ocr.py", SLUG, "--print", str(CLIP))
        doc = yaml.safe_load((tmp / SLUG / "extract" / "ocr.yaml").read_text(encoding="utf-8"))
        rows = {r["id"]: r for r in doc["candidates"]}
        check(len(rows) == 3, f"ocr.yaml has one row per candidate ({len(rows)})")
        check(sum(doc["counts"].values()) == len(rows), "the counts add up to the candidates")
        for cid, r in sorted(rows.items()):
            check(r["class"] in ("settled", "unsettled"),
                  f"{cid}: class {r['class']} (rendered text must never be empty)")
            check(len(r["ocr_text"].split()) >= 2, f"{cid}: OCR read words: {r['ocr_text'][:40]!r}")
            check(r["ocr_lines"] >= 1 and 0.0 < float(r["ocr_conf"]) <= 1.0,
                  f"{cid}: confidence {r['ocr_conf']} over {r['ocr_lines']} line(s)")
        for cid, r in sorted(rows.items()):
            if r["class"] == "settled":
                check(r["reading"]["confidence"] == "medium" and r["reading"]["doubt"] == "ocr",
                      f"{cid}: a settled reading is medium confidence, doubt 'ocr'")
        check("unsettled" in out, "ocr.py prints the unsettled ids")

        left = sorted(cid for cid, r in rows.items() if r["class"] == "unsettled")
        out = run(env, "pipeline/tools/transcribe.py", SLUG, "--prepare", "--print", str(CLIP))
        bdir = tmp / SLUG / "extract" / "batches"
        batched = sorted(cid for f in bdir.glob("[0-9]*.json")
                         for cid in yaml.safe_load(f.read_text(encoding="utf-8"))["ids"])
        check(batched == left, f"--prepare batches the unsettled candidates only ({batched} vs {left})")
        if left:
            check("the rest were settled or rejected by OCR" in
                  next(bdir.glob("*.md")).read_text(encoding="utf-8"),
                  "the batch prompt says how many candidates it covers")
        shutil.rmtree(bdir, ignore_errors=True)
        run(env, "pipeline/tools/transcribe.py", SLUG, "--prepare", "--all", "--print", str(CLIP))
        batched = sorted(cid for f in bdir.glob("[0-9]*.json")
                         for cid in yaml.safe_load(f.read_text(encoding="utf-8"))["ids"])
        check(batched == sorted(rows), "--all batches every candidate again")

        out = run(env, "pipeline/tools/transcribe.py", SLUG, "--merge")
        merged = yaml.safe_load((tmp / SLUG / "extract" / "transcribed.yaml").read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in merged["cards"]}
        check(sorted(by_id) == sorted(rows), "--merge keeps every candidate")
        for cid, r in sorted(rows.items()):
            if r["class"] == "settled":
                check(by_id[cid]["text"] == r["ocr_text"], f"{cid}: the OCR reading reached transcribed.yaml")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(FAILURES)} failures" if FAILURES else "all checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
