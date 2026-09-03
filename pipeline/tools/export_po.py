"""cards.yaml -> data/locales/templates/<slug>.pot, then merge into each target .po.

Run after any edit to cards.yaml. The POT is what Crowdin reads as the source file;
the .po files are what Crowdin writes translations back into. Merging keeps existing
translations whose card id + source text are unchanged and marks changed ones fuzzy,
so translators see exactly what needs another look.

    python pipeline/tools/export_po.py                 # every film
    python pipeline/tools/export_po.py sherlock-jr-1924
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import polib

from common import Card, Film, list_films, load_film, po_path, pot_path


def lf_only(p) -> None:
    """polib writes the platform newline; the repo and Crowdin want LF."""
    b = p.read_bytes()
    if b"\r\n" in b:
        p.write_bytes(b.replace(b"\r\n", b"\n"))


def translator_comment(film: Film, c: Card) -> str:
    bits = [f"Card {c.index} of {len(film.cards)}", c.type]
    if c.speaker:
        bits.append(f"speaker: {c.speaker}")
    bits.append(f"{c.duration:.1f}s on screen" if c.timed else "duration not yet timed")
    lines = [" · ".join(bits)]
    if c.context:
        lines.append(f"Context: {c.context}")
    lines.append("Keep the line breaks: each newline is a line on the card.")
    return "\n".join(lines)


def build_pot(film: Film) -> polib.POFile:
    pot = polib.POFile(check_for_duplicates=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M%z")
    pot.metadata = {
        "Project-Id-Version": f"intertitles {film.slug}",
        "POT-Creation-Date": now,
        "Language": "",
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "X-Film-Title": film.title,
        "X-Film-Year": str(film.meta.get("year", "")),
    }
    pot.header = (
        f"{film.title} ({film.meta.get('year', '')}) — intertitles.\n"
        "Generated from data/films/<slug>/cards.yaml by pipeline/tools/export_po.py. Do not edit by hand."
    )
    for c in film.cards:
        if not c.text.strip():
            continue
        e = polib.POEntry(
            msgctxt=c.key,
            msgid=c.text,
            msgstr="",
            comment=translator_comment(film, c),
            occurrences=[(f"data/films/{film.slug}/cards.yaml", str(c.index))],
        )
        pot.append(e)
    return pot


def merge_into(film: Film, pot: polib.POFile, lang: str) -> tuple[int, int, int]:
    p = po_path(film.slug, lang)
    if p.exists():
        po = polib.pofile(str(p), encoding="utf-8")
    else:
        po = polib.POFile()
        po.metadata = dict(pot.metadata)
    po.metadata["Language"] = lang
    po.metadata["Plural-Forms"] = "nplurals=2; plural=(n != 1);"
    po.merge(pot)
    p.parent.mkdir(parents=True, exist_ok=True)
    po.save(str(p))
    lf_only(p)
    translated = sum(1 for e in po if e.msgstr and not e.obsolete and "fuzzy" not in e.flags)
    fuzzy = sum(1 for e in po if "fuzzy" in e.flags and not e.obsolete)
    total = sum(1 for e in po if not e.obsolete)
    return translated, fuzzy, total


def main(argv: list[str]) -> int:
    slugs = argv or list_films(include_example=True)
    for slug in slugs:
        film = load_film(slug)
        pot = build_pot(film)
        pot_path(slug).parent.mkdir(parents=True, exist_ok=True)
        pot.save(str(pot_path(slug)))
        lf_only(pot_path(slug))
        print(f"{slug}: {len(pot)} strings -> {pot_path(slug).relative_to(pot_path(slug).parents[2])}")
        for lang in film.target_langs:
            t, f, n = merge_into(film, pot, lang)
            print(f"  {lang}: {t}/{n} translated, {f} fuzzy")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
