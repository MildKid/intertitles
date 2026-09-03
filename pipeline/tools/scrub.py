"""Check every card against the print, by eye, and fix it in place.

    python pipeline/tools/scrub.py <slug> [--print P] [--port N] [--no-browser]

Starts a small local server (stdlib only, no build step, no external requests) and opens one
page: the print in a video element, the cards down the left, and one card's transcript,
style, and timecodes on the right. Every edit is written straight to
data/films/<slug>/cards.yaml through common.write_cards, so the file keeps its key order and
its `|-` text blocks, and keys the tools do not know about survive the round trip.

The page reads film.yaml for what the timecodes mean. `print.status` says none, reference, or
projection; the hash of the file being played is compared with `print.sha256`, so a card timed
against another copy says so. Retiming against a new print is the same page: play the new
file, nudge in/out, verify.

    v   verify and advance          i / o   set in / out from the video
    j / k or arrow keys   move      [ / ]   nudge in by a frame
    space   play / pause            { / }   nudge out by a frame

`verified: true` means a person read the card against the frame. Nothing else sets it.
"""
from __future__ import annotations

import argparse
import http.server
import json
import mimetypes
import re
import socketserver
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from common import (ALIGNS, CARD_TYPES, CONFIDENCES, FRAME_STYLES, OUT, ROOT, STYLE_KEYS,
                    TEMPLATES, find_print, fmt_tc, load_cards_doc, load_film, parse_tc, probe,
                    sha256_head, write_cards)

PAGE = TEMPLATES / "scrub.html"
LETTERS = [chr(c) for c in range(ord("a"), ord("z") + 1)]
_LOCK = threading.Lock()


# ---------------------------------------------------------------- card helpers

def suffixes():
    """a, b, ... z, aa, ab, ... : the insertion suffixes, in order."""
    yield from LETTERS
    for a in LETTERS:
        for b in LETTERS:
            yield a + b


def next_card_id(after_id: str | None, ids: list[str], is_last: bool) -> str:
    """The id for a card inserted after `after_id`. Never renumbers anything.

    After the last card: the next free numeric id, zero-padded to the same width.
    Anywhere else: the next free suffix of the id in front of it ('042a', '042b', ...).
    """
    used = set(ids)
    if after_id is None:
        for n in range(1, 1000):
            cand = f"{n:03d}"
            if cand not in used:
                return cand
    m = re.match(r"^(\d+)(.*)$", str(after_id))
    if is_last and m:
        width, n = len(m.group(1)), int(m.group(1))
        while True:
            n += 1
            cand = str(n).zfill(width)
            if cand not in used:
                return cand
    for s in suffixes():
        cand = f"{after_id}{s}"
        if cand not in used:
            return cand
    raise ValueError(f"no free id after {after_id}")


def tc_value(v) -> str:
    """A number of seconds or a timecode string -> the string cards.yaml stores. '' = untimed."""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        raise ValueError("timecode must be a number or a string")
    if isinstance(v, (int, float)):
        return fmt_tc(max(0.0, float(v)))
    s = str(v).strip()
    if not s:
        return ""
    parse_tc(s)                                   # raises on anything malformed
    return s


def seconds(v) -> float | None:
    try:
        return parse_tc(v)
    except ValueError:
        return None


def card_json(raw: dict, index: int) -> dict:
    style = dict(raw.get("style") or {})
    return {
        "id": str(raw.get("id", "")),
        "index": index,
        "in": raw.get("in") or "",
        "out": raw.get("out") or "",
        "in_s": seconds(raw.get("in")),
        "out_s": seconds(raw.get("out")),
        "type": raw.get("type") or "dialogue",
        "speaker": raw.get("speaker") or "",
        "text": str(raw.get("text") or ""),
        "context": raw.get("context") or "",
        "notes": raw.get("notes") or "",
        "verified": bool(raw.get("verified")),
        "style": {k: style.get(k) for k in STYLE_KEYS},
        "confidence": str(raw.get("confidence") or ""),
        "doubt": raw.get("doubt") or "",
    }


def counts(doc: dict) -> dict:
    cards = doc.get("cards") or []
    return {"total": len(cards), "verified": sum(1 for c in cards if c.get("verified"))}


def apply_patch(raw: dict, patch: dict) -> None:
    """Set the fields the page sent. Unknown keys on the card are left alone; the id never moves."""
    for k, v in patch.items():
        if k in ("in", "out"):
            raw[k] = tc_value(v)
        elif k == "text":
            raw["text"] = str(v or "").replace("\r\n", "\n").rstrip("\n")
        elif k in ("speaker", "context", "notes", "doubt"):
            raw[k] = str(v or "").strip() if k != "context" else str(v or "")
        elif k == "type":
            if v not in CARD_TYPES:
                raise ValueError(f"type must be one of {', '.join(CARD_TYPES)}")
            raw["type"] = v
        elif k == "confidence":
            if v not in CONFIDENCES + ("",):
                raise ValueError(f"confidence must be one of {', '.join(CONFIDENCES)} or blank")
            raw["confidence"] = v or ""
        elif k == "verified":
            raw["verified"] = bool(v)
        elif k == "style":
            st = dict(raw.get("style") or {})
            for sk, sv in dict(v or {}).items():
                if sk == "frame" and sv not in FRAME_STYLES + ("",):
                    raise ValueError(f"frame must be one of {', '.join(FRAME_STYLES)} or blank")
                if sk == "align" and sv not in ALIGNS + ("",):
                    raise ValueError(f"align must be one of {', '.join(ALIGNS)} or blank")
                if sk == "all_caps":
                    sv = None if sv in (None, "") else bool(sv)
                st[sk] = sv
            raw["style"] = st
        # id and anything else the page sends is ignored: ids are stable forever


def find_card(doc: dict, cid: str) -> tuple[int, dict] | tuple[None, None]:
    for i, c in enumerate(doc.get("cards") or []):
        if str(c.get("id")) == cid:
            return i, c
    return None, None


# ---------------------------------------------------------------- context

class Context:
    def __init__(self, slug: str, print_path: Path):
        self.slug = slug
        self.print_path = print_path
        film = load_film(slug)
        self.meta = film.meta
        self.title = film.title
        pr = self.meta.get("print") or {}
        self.status = pr.get("status") or "none"
        recorded = str(pr.get("sha256") or "").strip()
        actual = sha256_head(print_path)
        if not recorded:
            self.match, self.timing_note = "none", "film.yaml records no print"
        elif recorded.lower() == actual.lower():
            self.match, self.timing_note = "match", "matches film.yaml"
        else:
            self.match = "differs"
            self.timing_note = "differs from film.yaml (timecodes belong to another file)"
        self.sha256 = actual
        self.fps = float(pr.get("fps") or 0) or self._probe_fps()

    def _probe_fps(self) -> float:
        try:
            return float(probe(self.print_path).get("fps") or 0) or 24.0
        except Exception:
            return 24.0

    def state(self) -> dict:
        doc = load_cards_doc(self.slug)
        cards = doc.get("cards") or []
        return {
            "slug": self.slug,
            "title": self.title,
            "year": self.meta.get("year", ""),
            "video": {"name": self.print_path.name, "path": str(self.print_path), "fps": self.fps},
            "timing": {"status": self.status, "match": self.match, "note": self.timing_note,
                       "sha256": self.sha256[:12]},
            "cards": [card_json(c, i) for i, c in enumerate(cards)],
            "counts": counts(doc),
        }


def grab_path(slug: str, cid: str) -> Path | None:
    d = OUT / slug / "extract"
    for p in (d / "grabs" / f"{cid}.png", d / f"{cid}.jpg", d / f"{cid}.png"):
        if p.exists():
            return p
    return None


PLACEHOLDER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#141414"/>
<text x="320" y="176" fill="#5a5a5a" font-family="sans-serif" font-size="20"
 text-anchor="middle">no frame grab for {cid}</text>
<text x="320" y="204" fill="#454545" font-family="sans-serif" font-size="14"
 text-anchor="middle">out/{slug}/extract/grabs/{cid}.png</text></svg>"""


# ---------------------------------------------------------------- server

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "scrub"
    protocol_version = "HTTP/1.1"

    # ---- plumbing

    def log_message(self, fmt, *args):        # the page is the interface; keep stdout clean
        pass

    @property
    def ctx(self) -> Context:
        return self.server.ctx                # type: ignore[attr-defined]

    def _write(self, data: bytes) -> None:
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                              # a seek in the video aborts the request in flight

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self._write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _fail(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        data = self.rfile.read(n)
        obj = json.loads(data.decode("utf-8"))
        return obj if isinstance(obj, dict) else {}

    # ---- routes

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                html = PAGE.read_text(encoding="utf-8")     # read per request: edits show on reload
            except OSError as e:
                return self._fail(500, f"{PAGE}: {e}")
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._json(self.ctx.state())
        if path == "/video":
            return self.send_video()
        if path.startswith("/grab/"):
            return self.send_grab(urllib.parse.unquote(path[len("/grab/"):]))
        if path == "/favicon.ico":
            return self._send(200, b"", "image/x-icon")
        self._fail(404, f"no route {path}")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self._body()
        except (ValueError, UnicodeDecodeError) as e:
            return self._fail(400, f"bad JSON: {e}")
        try:
            if path == "/api/add":
                return self.add_card(body)
            m = re.match(r"^/api/card/([^/]+)/drop$", path)
            if m:
                return self.drop_card(urllib.parse.unquote(m.group(1)))
            m = re.match(r"^/api/card/([^/]+)$", path)
            if m:
                return self.update_card(urllib.parse.unquote(m.group(1)), body)
        except ValueError as e:
            return self._fail(400, str(e))
        self._fail(404, f"no route {path}")

    # ---- api

    def update_card(self, cid: str, patch: dict) -> None:
        with _LOCK:
            doc = load_cards_doc(self.ctx.slug)
            i, raw = find_card(doc, cid)
            if raw is None:
                return self._fail(404, f"no card {cid}")
            apply_patch(raw, patch)
            write_cards(self.ctx.slug, doc)
            self._json({"card": card_json(raw, i), "counts": counts(doc)})

    def drop_card(self, cid: str) -> None:
        with _LOCK:
            doc = load_cards_doc(self.ctx.slug)
            i, raw = find_card(doc, cid)
            if raw is None:
                return self._fail(404, f"no card {cid}")
            if raw.get("verified"):
                return self._fail(409, f"card {cid} is verified; un-verify it before dropping")
            doc["cards"].pop(i)
            write_cards(self.ctx.slug, doc)
            self._json({"dropped": cid, "counts": counts(doc)})

    def add_card(self, body: dict) -> None:
        at = float(body.get("at") or 0.0)
        after = body.get("after")
        with _LOCK:
            doc = load_cards_doc(self.ctx.slug)
            cards = doc["cards"]
            ids = [str(c.get("id")) for c in cards]
            pos = None
            if after not in (None, ""):
                if str(after) not in ids:
                    return self._fail(404, f"no card {after}")
                pos = ids.index(str(after))
            else:
                for i, c in enumerate(cards):        # the last card that starts at or before `at`
                    s = seconds(c.get("in"))
                    if s is not None and s <= at:
                        pos = i
            after_id = ids[pos] if pos is not None else None
            new_id = next_card_id(after_id, ids, is_last=(pos is not None and pos == len(cards) - 1))
            card = {
                "id": new_id,
                "in": fmt_tc(max(0.0, at)),
                "out": fmt_tc(max(0.0, at) + 2.0),
                "type": "dialogue",
                "text": "",
                "context": "",
                "verified": False,
                "style": {k: (None if k == "all_caps" else "") for k in STYLE_KEYS},
                "confidence": "",
            }
            index = 0 if pos is None else pos + 1
            cards.insert(index, card)
            write_cards(self.ctx.slug, doc)
            self._json({"card": card_json(card, index), "counts": counts(doc)})

    # ---- media

    def send_grab(self, cid: str) -> None:
        p = grab_path(self.ctx.slug, cid)
        if p is None:
            svg = PLACEHOLDER.format(cid=cid or "?", slug=self.ctx.slug)
            return self._send(200, svg.encode("utf-8"), "image/svg+xml; charset=utf-8")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        self._send(200, p.read_bytes(), ctype)

    def send_video(self) -> None:
        path = self.ctx.print_path
        try:
            size = path.stat().st_size
        except OSError as e:
            return self._fail(404, f"{path}: {e}")
        ctype = mimetypes.guess_type(path.name)[0] or "video/mp4"
        start, end, partial = 0, size - 1, False
        rng = (self.headers.get("Range") or "").strip()
        if rng:
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng)
            if not m:
                return self._fail(400, f"bad Range {rng}")
            first, last = m.group(1), m.group(2)
            if first == "":
                if last == "":
                    return self._fail(400, f"bad Range {rng}")
                start, end = max(0, size - int(last)), size - 1
            else:
                start = int(first)
                end = int(last) if last else size - 1
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            partial = True
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with path.open("rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self._write(chunk)
                    left -= len(chunk)
        except OSError:
            pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--print", dest="print_path", help="the copy to play (default: film.yaml / prints/)")
    ap.add_argument("--port", type=int, default=0, help="0 (the default) takes a free port")
    ap.add_argument("--no-browser", action="store_true", help="print the URL and wait")
    a = ap.parse_args()

    try:
        film = load_film(a.slug)
    except FileNotFoundError as e:
        raise SystemExit(f"no film {a.slug}: {e}")
    print_path = find_print(a.slug, film.meta, a.print_path)
    if print_path is None:
        raise SystemExit("no print found: pass --print, set print.file in film.yaml, "
                         "or put the file under prints/<slug>/")
    if not PAGE.exists():
        raise SystemExit(f"page missing: {PAGE}")

    ctx = Context(a.slug, Path(print_path).resolve())
    server = Server(("127.0.0.1", a.port), Handler)
    server.ctx = ctx                                   # type: ignore[attr-defined]
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"scrub: {url}", flush=True)                 # first line: tools read the port from here
    doc = load_cards_doc(a.slug)
    c = counts(doc)
    print(f"  {film.title}: {c['verified']} of {c['total']} cards verified", flush=True)
    print(f"  playing {ctx.print_path}", flush=True)
    print(f"  timing: {ctx.status}; {ctx.timing_note}", flush=True)
    print("  ctrl-c to stop", flush=True)
    if not a.no_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
