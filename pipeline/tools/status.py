"""Write site/status.json: the one contract between the pipeline and the website.

    python pipeline/tools/status.py

The site reads nothing else from data/ or out/ (docs/design.md, "Website"). Set
INTERTITLES_SITE to write somewhere other than <repo root>/site.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import ROOT, designer_card, list_films, load_film, load_translations

SITE = Path(os.environ.get("INTERTITLES_SITE", ROOT / "site")).resolve()


def film_status(slug: str) -> dict:
    film = load_film(slug)
    cards = film.cards
    transcribed = sum(1 for c in cards if c.text.strip())
    verified = sum(1 for c in cards if c.verified)

    translated: dict[str, int] = {}
    designed: dict[str, int] = {}
    for lang in film.target_langs:
        tr = load_translations(slug, lang)
        translated[lang] = sum(1 for c in cards if c.text.strip() and c.id in tr)
        designed[lang] = sum(1 for c in cards if designer_card(slug, lang, c.id))

    return {
        "slug": slug,
        "title": film.title,
        "year": film.meta.get("year"),
        "cards": len(cards),
        "transcribed": transcribed,
        "verified": verified,
        "translated": translated,
        "designed": designed,
        "timing": film.timing_status,
        "extraction": film.meta.get("extraction", {}).get("status", ""),
    }


def main(argv: list[str]) -> int:
    slugs = argv or list_films()
    films = [film_status(s) for s in slugs]
    doc = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "films": films,
    }
    SITE.mkdir(parents=True, exist_ok=True)
    out = SITE / "status.json"
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    out.write_text(text, encoding="utf-8", newline="\n")
    for f in films:
        print(f"{f['slug']}: {f['cards']} cards, {f['transcribed']} transcribed, "
              f"{f['verified']} verified, timing={f['timing']}, extraction={f['extraction']!r}")
    print(f"-> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
