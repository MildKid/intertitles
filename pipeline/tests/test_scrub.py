"""Drive pipeline/tools/scrub.py against a throwaway copy of the fixture film.

    python pipeline/tests/test_scrub.py

The film data is copied to a temp directory and the server is started as a subprocess with
INTERTITLES_DATA pointed at it (common.py reads the env at import, so a subprocess is the only
way to move the data root). The test then exercises every route the page uses and reads the
temp cards.yaml back: the file must parse, hold each change, keep its `|-` text blocks, keep
the house key order, and keep a key the tools know nothing about.

Exits 0 when everything passes, 1 otherwise. make_clip.py runs this last.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CLIP = ROOT / "out" / "_example" / "print.mp4"
SLUG = "_example"
sys.path.insert(0, str(ROOT / "pipeline" / "tools"))
import common                                        # noqa: E402  (after sys.path)

FAILURES: list[str] = []


def check(ok: bool, what: str) -> bool:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        FAILURES.append(what)
    return ok


# ---------------------------------------------------------------- http

class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def get(self, path: str, headers: dict | None = None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read()

    def get_json(self, path: str):
        _, _, body = self.get(path)
        return json.loads(body.decode("utf-8"))

    def post(self, path: str, obj: dict | None = None):
        data = json.dumps(obj or {}).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")


# ---------------------------------------------------------------- yaml reading

def key_blocks(text: str) -> list[list[str]]:
    """The keys of each card, in the order they appear in the file."""
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("  - "):
            cur = []
            blocks.append(cur)
            line = "    " + line[4:]
        if cur is None:
            continue
        m = re.match(r"^ {4}([A-Za-z_][A-Za-z0-9_-]*):", line)
        if m:
            cur.append(m.group(1))
    return blocks


def is_subsequence(keys: list[str], order: tuple[str, ...]) -> bool:
    it = iter(order)
    return all(k in it for k in keys)


# ---------------------------------------------------------------- the run

def run() -> int:
    if not CLIP.exists():
        print(f"{CLIP} missing; building it")
        subprocess.run([sys.executable, str(ROOT / "pipeline" / "tests" / "make_clip.py"), "--clip"],
                       check=True, cwd=ROOT)

    tmp = Path(tempfile.mkdtemp(prefix="scrub-test-"))
    data = tmp / "data"
    (data / "films").mkdir(parents=True)
    (data / "locales").mkdir(parents=True)
    shutil.copytree(ROOT / "data" / "films" / SLUG, data / "films" / SLUG)
    cards_path = data / "films" / SLUG / "cards.yaml"

    # a key no tool knows about: it must still be there at the end
    doc = yaml.safe_load(cards_path.read_text(encoding="utf-8"))
    doc["cards"][0]["provenance"] = "fixture-check"
    cards_path.write_text(common.dump_cards(doc, common.CARDS_HEADER), encoding="utf-8", newline="\n")

    env = dict(os.environ, INTERTITLES_DATA=str(data), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "pipeline" / "tools" / "scrub.py"), SLUG,
         "--no-browser", "--port", "0", "--print", str(CLIP)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    tail: list[str] = []
    try:
        first = proc.stdout.readline().strip()
        m = re.match(r"^scrub: (http://127\.0\.0\.1:\d+/)$", first)
        if not m:
            print(f"first stdout line was {first!r}; expected 'scrub: http://127.0.0.1:<port>/'")
            print("".join(proc.stdout.read() or ""))
            return 1
        threading.Thread(target=lambda: tail.extend(proc.stdout), daemon=True).start()
        c = Client(m.group(1))
        exercise(c)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    verify_file(cards_path)

    if FAILURES:
        print(f"\ntest_scrub: {len(FAILURES)} failure(s)")
        for f in FAILURES:
            print("  - " + f)
        print("server output:\n" + "".join(tail))
        print(f"data left in {data}")
        return 1
    shutil.rmtree(tmp, ignore_errors=True)
    print("test_scrub: ok")
    return 0


def exercise(c: Client) -> None:
    print("routes")
    st = c.get_json("/api/state")
    check(st["counts"] == {"total": 3, "verified": 0}, "GET /api/state counts 0 of 3")
    check([x["id"] for x in st["cards"]] == ["001", "002", "003"], "state lists the three fixture cards")
    check(st["timing"]["status"] == "projection", "state carries film.yaml print.status")
    check(st["timing"]["match"] == "none" and "no print" in st["timing"]["note"],
          "state says film.yaml records no print (blank sha256)")
    check(abs(st["video"]["fps"] - 24) < 0.001, "fps comes from film.yaml")

    status, headers, body = c.get("/", None)
    check(status == 200 and b"<title>scrub</title>" in body, "GET / serves the page")

    status, headers, body = c.get("/video", {"Range": "bytes=0-1023"})
    check(status == 206, "Range GET /video -> 206")
    check(len(body) == 1024, "Range GET returns exactly the bytes asked for")
    check(headers.get("Content-Range", "").startswith("bytes 0-1023/"), "Content-Range header is set")

    status, headers, grab = c.get("/grab/001")
    check(status == 200 and headers.get("Content-Type", "").startswith("image/"),
          "GET /grab/<id> serves an image")
    status, _, grab = c.get("/grab/no-such-card")
    check(status == 200 and grab.startswith(b"<svg"), "a card with no grab gets a placeholder")

    print("editing")
    new_text = 'Morning. The projectionist\nhas fallen asleep again.\n(scrubbed)'
    status, j = c.post("/api/card/002", {"text": new_text})
    check(status == 200 and j["card"]["text"] == new_text, "POST text edit")

    status, j = c.post("/api/card/002", {"in": 4.25})
    check(status == 200 and j["card"]["in"] == "00:00:04.250", "POST in nudge -> timecode string")

    status, j = c.post("/api/card/003", {"verified": True})
    check(status == 200 and j["counts"]["verified"] == 1, "POST verify updates the count")

    status, j = c.post("/api/card/003", {"style": {"frame": "rule", "all_caps": True},
                                         "confidence": "high", "speaker": "The Manager"})
    check(status == 200 and j["card"]["style"]["frame"] == "rule", "POST style fields")

    status, j = c.post("/api/card/002", {"type": "nonsense"})
    check(status == 400, "POST rejects a type outside the list")

    print("adding and dropping")
    status, j = c.post("/api/add", {"at": 5.0, "after": "002"})
    check(status == 200 and j["card"]["id"] == "002a", "POST /api/add inserts as 002a")
    check(j["card"]["in"] == "00:00:05.000" and j["card"]["out"] == "00:00:07.000",
          "new card runs from the video position for 2 s")
    check(j["card"]["type"] == "dialogue" and j["card"]["text"] == "" and not j["card"]["verified"],
          "new card is an empty unverified dialogue card")

    status, j = c.post("/api/card/003/drop")
    check(status == 409, "drop refuses a verified card")

    status, j = c.post("/api/card/002a/drop")
    check(status == 200 and j["counts"]["total"] == 3, "POST drop removes the new card")


def verify_file(cards_path: Path) -> None:
    print("cards.yaml")
    text = cards_path.read_text(encoding="utf-8")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        check(False, f"cards.yaml parses ({e})")
        return
    check(True, "cards.yaml parses with yaml.safe_load")
    cards = {str(x["id"]): x for x in doc["cards"]}
    check(list(cards) == ["001", "002", "003"], "ids and order are unchanged")
    check(cards["002"]["text"].endswith("(scrubbed)"), "the text edit is on disk")
    check(cards["002"]["in"] == "00:00:04.250", "the nudged in is on disk")
    check(cards["003"]["verified"] is True, "verified: true is on disk")
    check(cards["003"]["style"]["frame"] == "rule" and cards["003"]["confidence"] == "high",
          "style and confidence are on disk")
    check(cards["001"].get("provenance") == "fixture-check", "an unknown key survives the round trip")
    check(text.count("text: |-") == 3, "text keeps its block scalar (text: |-)")

    blocks = key_blocks(text)
    check(len(blocks) == 3, "three card blocks in the file")
    check(blocks[2][:8] == ["id", "in", "out", "type", "speaker", "text", "context", "verified"],
          "card 003 keeps the key order id, in, out, type, speaker, text, context, ...")
    check(all(is_subsequence(b, common.CARD_KEY_ORDER + ("provenance",)) for b in blocks),
          "every card follows the house key order")


if __name__ == "__main__":
    sys.exit(run())
