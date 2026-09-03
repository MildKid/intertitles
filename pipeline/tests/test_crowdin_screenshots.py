"""Offline checks for pipeline/tools/crowdin_screenshots.py.

Only the card id -> Crowdin string id matcher is exercised; it is the part that decides
whether a screenshot lands on the right string, and the part that has to cope with
however Crowdin reports gettext identifiers. No network, no token needed.

    python pipeline/tests/test_crowdin_screenshots.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import crowdin_screenshots as cs  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}\n  got  {got!r}\n  want {want!r}")
        FAILURES.append(name)


def main() -> int:
    # 1. identifier is the bare card id.
    rows = [{"id": 11, "identifier": "001", "context": "", "text": "A"},
            {"id": 12, "identifier": "002", "context": "", "text": "B"}]
    m, unmatched = cs.match_strings(["001", "002"], rows)
    check("bare identifier", (m, unmatched), ({"001": 11, "002": 12}, []))

    # 2. gettext identifier reported as msgctxt||msgid.
    rows = [{"id": 21, "identifier": "042||\"You stole my watch.\"", "context": "", "text": "x"},
            {"id": 22, "identifier": "042a||Later that day.", "context": "", "text": "y"}]
    m, unmatched = cs.match_strings(["042", "042a"], rows)
    check("msgctxt||msgid identifier", (m, unmatched), ({"042": 21, "042a": 22}, []))

    # 3. id only in the context field, matched as a whole token.
    rows = [{"id": 31, "identifier": "abc123", "context": "Card 7 of 90\nmsgctxt: 007", "text": "x"}]
    m, unmatched = cs.match_strings(["007"], rows)
    check("id in context", (m, unmatched), ({"007": 31}, []))

    # 4. "042" must not claim the string belonging to "042a".
    rows = [{"id": 41, "identifier": "zzz", "context": "card 042a", "text": "x"}]
    m, unmatched = cs.match_strings(["042"], rows)
    check("no partial-token match", (m, unmatched), ({}, ["042"]))

    # 5. Unmatched ids are reported, matched ones still map.
    rows = [{"id": 51, "identifier": "001", "context": "", "text": "A"}]
    m, unmatched = cs.match_strings(["001", "002", "003"], rows)
    check("unmatched reported", (m, unmatched), ({"001": 51}, ["002", "003"]))

    # 6. The exact identifier wins over a prefix match and over the context.
    rows = [{"id": 61, "identifier": "010||first", "context": "010", "text": "A"},
            {"id": 62, "identifier": "010", "context": "", "text": "B"}]
    m, _ = cs.match_strings(["010"], rows)
    check("exact identifier wins", m, {"010": 62})

    # 7. Rows without an id are ignored rather than crashing the run.
    rows = [{"identifier": "001", "context": ""}, {"id": 71, "identifier": "001", "context": ""}]
    m, _ = cs.match_strings(["001"], rows)
    check("rows without an id ignored", m, {"001": 71})

    print(f"\n{len(FAILURES)} failed" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
