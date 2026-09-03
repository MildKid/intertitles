"""Upload each card's frame grab to Crowdin as a screenshot tagged to that card's string.

A translator working a string sees the title card itself in the editor's context panel,
which settles line breaks, punctuation, and who is speaking faster than a context note.

    # PowerShell
    $env:CROWDIN_TOKEN = "<personal token: Account Settings -> API>"   # or save it as ~/.crowdin_token
    $env:CROWDIN_PROJECT_ID = "123456"

    python pipeline/tools/crowdin_screenshots.py                      # every film
    python pipeline/tools/crowdin_screenshots.py sherlock-jr-1924
    python pipeline/tools/crowdin_screenshots.py sherlock-jr-1924 --dry-run
    python pipeline/tools/crowdin_screenshots.py --list-strings sherlock-jr-1924

Per film the tool finds the Crowdin file named `<slug>.pot`, lists its strings, maps each
card id to a string id, reads the card's frame grab from `out/<slug>/extract/grabs/<id>.png`
(falling back to `out/<slug>/extract/<id>.jpg`), uploads it to Crowdin storage, creates a
screenshot named `<slug>/<id>.png`, and tags it with the string. Running it again skips
screenshots whose names are already in the project; `--replace` deletes and re-uploads them.

Free-tier caveat: whether screenshots are included on Crowdin's free plan is unconfirmed.
A 403 on `POST /projects/{id}/screenshots` means the plan does not carry the feature; the
fallback is a frame URL in each string's translator comment, written up in
`docs/go-public.md` § Screenshots.

Stdlib only apart from `common.py` (which reads YAML). The token is read from the
environment and never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

API = "https://api.crowdin.com/api/v2"
PAUSE = 0.2          # seconds between calls, to stay well inside the rate limit
PAGE = 500           # Crowdin's maximum page size
RETRY_WAIT = 5.0     # fallback wait on 429 when the response carries no Retry-After

_TOKEN = ""
_PROJECT = ""


TOKEN_FILE = Path.home() / ".crowdin_token"


def resolve_token() -> str:
    """CROWDIN_TOKEN from the environment, else the one-line file ~/.crowdin_token (outside
    the repo, never committed). Empty string when neither exists."""
    tok = os.environ.get("CROWDIN_TOKEN", "").strip()
    if tok:
        return tok
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip() if TOKEN_FILE.stat().st_size else ""
    return ""


NO_TOKEN_MSG = (
    "No Crowdin token. Create a personal access token in Crowdin (Account Settings -> API),\n"
    f"then either save it as the only line of {TOKEN_FILE} or set it in the environment:\n"
    '  PowerShell: $env:CROWDIN_TOKEN = "<token>"\n'
    "  bash:       export CROWDIN_TOKEN=<token>")


class ApiError(Exception):
    """An HTTP failure, carrying enough to print without leaking the token."""

    def __init__(self, status: int, endpoint: str, body: str, retry_after: str | None = None):
        super().__init__(f"HTTP {status} {endpoint}: {body[:300]}")
        self.status = status
        self.endpoint = endpoint
        self.body = body
        self.retry_after = retry_after


# --------------------------------------------------------------------------- transport

def _send(method: str, url: str, data: bytes | None, headers: dict) -> dict:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_TOKEN}")
    for k, v in headers.items():
        req.add_header(k, v)
    endpoint = f"{method} {url.split('?', 1)[0][len(API):]}"
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        raise ApiError(e.code, endpoint, text, (e.headers or {}).get("Retry-After")) from None
    except urllib.error.URLError as e:
        raise ApiError(0, endpoint, str(e.reason)) from None
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def api(method: str, path: str, *, json_body=None, raw: bytes | None = None,
        filename: str | None = None, query: dict | None = None) -> dict:
    """One API call, with a single retry on 429."""
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers: dict[str, str] = {}
    data: bytes | None = None
    if raw is not None:
        data = raw
        headers["Content-Type"] = "application/octet-stream"
        headers["Crowdin-API-FileName"] = urllib.parse.quote(filename or "upload.bin")
    elif json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    for attempt in (0, 1):
        time.sleep(PAUSE)
        try:
            return _send(method, url, data, headers)
        except ApiError as e:
            if e.status == 429 and attempt == 0:
                try:
                    wait = float(e.retry_after)
                except (TypeError, ValueError):
                    wait = RETRY_WAIT
                print(f"  rate limited, waiting {wait:.0f}s")
                time.sleep(wait)
                continue
            raise
    return {}


def paginate(path: str, query: dict | None = None):
    """Every record of a paginated collection, unwrapped from its `data` envelope."""
    offset = 0
    while True:
        q = dict(query or {})
        q.update({"limit": PAGE, "offset": offset})
        rows = api("GET", path, query=q).get("data") or []
        for row in rows:
            yield row.get("data", row)
        if len(rows) < PAGE:
            return
        offset += len(rows)


# ----------------------------------------------------------------------------- matching

def match_strings(card_ids, strings) -> tuple[dict[str, int], list[str]]:
    """Map card id -> Crowdin string id.

    Tried in order, first hit wins:
      1. identifier equals the card id
      2. identifier starts with `<id>||` (gettext files report `msgctxt||msgid`)
      3. context mentions the card id as a whole token

    Returns the mapping and the ids that matched nothing.
    """
    import re

    rows = [r for r in strings if r.get("id") is not None]
    by_identifier: dict[str, dict] = {}
    for r in rows:
        by_identifier.setdefault(str(r.get("identifier") or ""), r)

    mapping: dict[str, int] = {}
    unmatched: list[str] = []
    for cid in card_ids:
        cid = str(cid)
        hit = by_identifier.get(cid)
        if hit is None:
            prefix = cid + "||"
            hit = next((r for r in rows if str(r.get("identifier") or "").startswith(prefix)), None)
        if hit is None:
            pat = re.compile(r"(?<![0-9A-Za-z])" + re.escape(cid) + r"(?![0-9A-Za-z])")
            hit = next((r for r in rows if pat.search(str(r.get("context") or ""))), None)
        if hit is None:
            unmatched.append(cid)
        else:
            mapping[cid] = int(hit["id"])
    return mapping, unmatched


# ------------------------------------------------------------------------------- pieces

def find_file_id(slug: str) -> int | None:
    """The Crowdin file whose name is `<slug>.pot`."""
    want = f"{slug}.pot"
    for f in paginate(f"/projects/{_PROJECT}/files"):
        name = str(f.get("name") or "")
        path = str(f.get("path") or "")
        if name == want or path.endswith("/" + want):
            return int(f["id"])
    return None


def find_image(slug: str, card_id: str) -> Path | None:
    base = common.OUT / slug / "extract"
    for p in (base / "grabs" / f"{card_id}.png", base / f"{card_id}.jpg"):
        if p.exists():
            return p
    return None


def existing_screenshots() -> dict[str, int]:
    return {str(s.get("name") or ""): int(s["id"])
            for s in paginate(f"/projects/{_PROJECT}/screenshots")}


def upload_screenshot(path: Path, name: str, string_id: int) -> None:
    storage = api("POST", "/storages", raw=path.read_bytes(), filename=path.name)
    storage_id = int(storage["data"]["id"])
    shot = api("POST", f"/projects/{_PROJECT}/screenshots",
               json_body={"storageId": storage_id, "name": name, "autoTag": False})
    shot_id = int(shot["data"]["id"])
    api("POST", f"/projects/{_PROJECT}/screenshots/{shot_id}/tags",
        json_body=[{"stringId": string_id}])


# -------------------------------------------------------------------------------- films

def list_strings(slug: str) -> int:
    """Print the first ten strings so the maintainer can confirm the id mapping."""
    file_id = find_file_id(slug)
    if file_id is None:
        print(f"{slug}: no file named {slug}.pot in project {_PROJECT}")
        return 1
    print(f"{slug}: file {file_id}")
    for i, s in enumerate(paginate(f"/projects/{_PROJECT}/strings", {"fileId": file_id})):
        if i >= 10:
            break
        text = str(s.get("text") or "").replace("\n", " / ")[:60]
        print(f"  id={s.get('id')} identifier={s.get('identifier')!r}")
        print(f"     context={str(s.get('context') or '')[:120]!r}")
        print(f"     text={text!r}")
    return 0


def process_film(slug: str, dry_run: bool, replace: bool) -> tuple[int, int]:
    """Upload one film's screenshots. Returns (uploaded, failed)."""
    film = common.load_film(slug)
    cards = [c for c in film.cards if c.text.strip()]
    if not cards:
        print(f"{slug}: no cards with text, skipped")
        return 0, 0

    file_id = find_file_id(slug)
    if file_id is None:
        print(f"{slug}: no file named {slug}.pot in the project; export the POT and sync first")
        return 0, 1

    strings = list(paginate(f"/projects/{_PROJECT}/strings", {"fileId": file_id}))
    mapping, unmatched = match_strings([c.id for c in cards], strings)
    existing = existing_screenshots()
    print(f"{slug}: {len(cards)} cards, {len(strings)} strings, "
          f"{len(mapping)} matched, {len(existing)} screenshots already in the project")

    uploaded = failed = skipped = missing = 0
    for card in cards:
        string_id = mapping.get(card.id)
        if string_id is None:
            continue
        image = find_image(slug, card.id)
        if image is None:
            print(f"  {card.id}: no frame grab under out/{slug}/extract/, skipped")
            missing += 1
            continue
        name = f"{slug}/{card.id}.png"
        if name in existing and not replace:
            skipped += 1
            continue
        if dry_run:
            action = "replace" if name in existing else "upload"
            print(f"  {card.id}: would {action} {name} <- {image} (string {string_id})")
            uploaded += 1
            continue
        try:
            if name in existing:
                api("DELETE", f"/projects/{_PROJECT}/screenshots/{existing[name]}")
            upload_screenshot(image, name, string_id)
        except ApiError as e:
            print(f"  {card.id}: {e}")
            failed += 1
            continue
        uploaded += 1
        print(f"  {card.id}: {name}")

    verb = "would upload" if dry_run else "uploaded"
    print(f"  {verb} {uploaded}, skipped {skipped} already present, "
          f"{missing} without a frame grab, {failed} failed")
    if unmatched:
        shown = ", ".join(unmatched[:20]) + (" ..." if len(unmatched) > 20 else "")
        print(f"  {len(unmatched)} card ids matched no string: {shown}")
        print(f"  run --list-strings {slug} to see how Crowdin reports the identifiers")
    return uploaded, failed


# --------------------------------------------------------------------------------- main

def main(argv: list[str]) -> int:
    global _TOKEN, _PROJECT

    ap = argparse.ArgumentParser(
        description="Upload card frame grabs to Crowdin as screenshots tagged to their strings.")
    ap.add_argument("slugs", nargs="*", help="films to upload; default every film")
    ap.add_argument("--project", default="", help="Crowdin project id (else CROWDIN_PROJECT_ID)")
    ap.add_argument("--dry-run", action="store_true", help="read only; print what would be uploaded")
    ap.add_argument("--replace", action="store_true", help="delete and re-upload screenshots already present")
    ap.add_argument("--list-strings", metavar="SLUG", default="",
                    help="print the first ten strings of a film's file and exit")
    args = ap.parse_args(argv)

    _TOKEN = os.environ.get("CROWDIN_TOKEN", "").strip()
    if not _TOKEN:
        print("CROWDIN_TOKEN is not set. Create a personal access token in Crowdin under\n"
              "Account Settings -> API, then set it in the environment:\n"
              '  PowerShell: $env:CROWDIN_TOKEN = "<token>"\n'
              "  bash:       export CROWDIN_TOKEN=<token>", file=sys.stderr)
        return 2
    _PROJECT = (args.project or os.environ.get("CROWDIN_PROJECT_ID", "")).strip()
    if not _PROJECT:
        print("No Crowdin project id. Pass --project <id> or set CROWDIN_PROJECT_ID.\n"
              "The numeric id is on the project's Tools -> API page and in the project URL.",
              file=sys.stderr)
        return 2

    try:
        if args.list_strings:
            return list_strings(args.list_strings)
        slugs = args.slugs or common.list_films()
        failed = 0
        for slug in slugs:
            _, f = process_film(slug, args.dry_run, args.replace)
            failed += f
    except ApiError as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
