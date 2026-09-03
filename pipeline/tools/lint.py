"""Sanity checks on cards and translations. Exit 1 on errors; warnings are advisory.

    python pipeline/tools/lint.py                    # every film, every target language
    python pipeline/tools/lint.py sherlock-jr-1924

Reading speed uses characters per second (cps), the same measure subtitlers use.
Each viewer reads only their own language, so cps is checked per language, plus a
softer advisory on the combined text density of the bilingual card.
"""
from __future__ import annotations

import sys

from common import (ALIGNS, CARD_TYPES, CONFIDENCES, FRAME_STYLES, STYLE_KEYS, Film, list_films,
                    load_film, load_translations, po_path)

CPS_WARN = 17.0      # comfortable adult reading speed ceiling
CPS_ERROR = 25.0     # nobody finishes the card
MAX_LINES = 4        # per language on a stacked bilingual card
MAX_LINE_CHARS = 42


def cps(text: str, seconds: float) -> float:
    n = len(text.replace("\n", " ").strip())
    return n / seconds if seconds > 0 else float("inf")


def lint_film(film: Film) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    seen: set[str] = set()
    prev_out = -1.0
    untimed = 0
    for c in film.cards:
        tag = f"{film.slug}#{c.id}"
        if c.id in seen:
            errors.append(f"{tag}: duplicate id")
        seen.add(c.id)
        if c.type not in CARD_TYPES:
            errors.append(f"{tag}: type {c.type!r} not in {CARD_TYPES}")
        check_fields(tag, c, errors, warns)
        if c.timed:
            if c.tc_out <= c.tc_in:
                errors.append(f"{tag}: out ({c.tc_out}) <= in ({c.tc_in})")
            if c.tc_in < prev_out:
                errors.append(f"{tag}: overlaps previous card (starts {c.tc_in:.2f} < prev out {prev_out:.2f})")
            prev_out = max(prev_out, c.tc_out)
        elif (c.tc_in is None) != (c.tc_out is None):
            errors.append(f"{tag}: has one of in/out but not both")
        else:
            untimed += 1
        if not c.text.strip():
            warns.append(f"{tag}: empty text (not exported)")
            continue
        # Reading speed is a hard limit only where a viewer must read the whole card: dialogue
        # and narrative, once a person has verified the timecodes. Inserts, credits, and the
        # title are read partially or not at all; unverified timecodes are provisional.
        strict = c.type in ("dialogue", "narrative") and c.verified
        check_text(tag, film.source_lang, c.text, c.duration, errors, warns, strict=strict)

    if untimed:
        warns.append(f"{film.slug}: {untimed} untimed cards (text-only; reading speed unchecked until timed)")
    unverified = sum(1 for c in film.cards if not c.verified)
    if film.cards and unverified:
        warns.append(f"{film.slug}: {unverified} of {len(film.cards)} cards not yet verified against the frame")
    lows = [c.id for c in film.cards if c.confidence == "low" and not c.verified]
    if lows:
        warns.append(f"{film.slug}: {len(lows)} low-confidence readings ({', '.join(lows[:8])}{'…' if len(lows) > 8 else ''})")
    if film.cards and film.timing_status == "reference":
        warns.append(f"{film.slug}: timecodes are against a reference copy, not the projection print")

    for lang in film.target_langs:
        tr = load_translations(film.slug, lang)
        if not po_path(film.slug, lang).exists():
            warns.append(f"{film.slug}: no {lang} .po yet (run export_po.py)")
            continue
        missing = [c.id for c in film.cards if c.text.strip() and c.id not in tr]
        if missing:
            warns.append(f"{film.slug} [{lang}]: {len(missing)} untranslated ({', '.join(missing[:8])}{'…' if len(missing) > 8 else ''})")
        for c in film.cards:
            t = tr.get(c.id)
            if not t:
                continue
            tag = f"{film.slug}#{c.id} [{lang}]"
            check_text(tag, lang, t, c.duration, errors, warns, strict=c.type in ("dialogue", "narrative") and c.verified)
            both = len(c.text.replace("\n", " ")) + len(t.replace("\n", " "))
            if c.duration and both / c.duration > CPS_WARN * 1.6:
                warns.append(f"{tag}: dense bilingual card ({both} chars in {c.duration:.1f}s) — designer should weight one language")
    return errors, warns


def check_fields(tag: str, c, errors: list[str], warns: list[str]) -> None:
    """The transcription-pass fields: blank means unknown and is fine; a wrong value is not."""
    if not isinstance(c.verified, bool):
        errors.append(f"{tag}: verified must be true or false")
    if c.confidence and c.confidence not in CONFIDENCES:
        errors.append(f"{tag}: confidence {c.confidence!r} not in {CONFIDENCES}")
    if c.verified and not c.text.strip():
        errors.append(f"{tag}: verified but has no text")
    st = c.style or {}
    for k in st:
        if k not in STYLE_KEYS:
            warns.append(f"{tag}: unknown style key {k!r}")
    if st.get("frame") not in (None, "") and st.get("frame") not in FRAME_STYLES:
        errors.append(f"{tag}: style.frame {st.get('frame')!r} not in {FRAME_STYLES}")
    if st.get("align") not in (None, "") and st.get("align") not in ALIGNS:
        errors.append(f"{tag}: style.align {st.get('align')!r} not in {ALIGNS}")
    if st.get("all_caps") not in (None, "", True, False):
        errors.append(f"{tag}: style.all_caps must be true, false, or blank")


def check_text(tag: str, lang: str, text: str, seconds: float | None, errors: list[str], warns: list[str],
               strict: bool = True) -> None:
    if seconds is not None:
        rate = cps(text, seconds)
        if rate > CPS_ERROR:
            msg = f"{tag}: {rate:.0f} cps in {lang} (limit {CPS_ERROR:.0f}) — shorten or check timecodes"
            (errors if strict else warns).append(msg if strict else msg + " (advisory: insert/credit/title or unverified timing)")
        elif rate > CPS_WARN:
            warns.append(f"{tag}: {rate:.0f} cps in {lang} (comfortable ≤ {CPS_WARN:.0f})")
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        warns.append(f"{tag}: {len(lines)} lines in {lang} (stacked layout fits {MAX_LINES})")
    longest = max(len(l) for l in lines)
    if longest > MAX_LINE_CHARS:
        warns.append(f"{tag}: line of {longest} chars in {lang} (soft max {MAX_LINE_CHARS})")


def main(argv: list[str]) -> int:
    slugs = argv or list_films(include_example=True)
    total_err = 0
    for slug in slugs:
        film = load_film(slug)
        errors, warns = lint_film(film)
        status = "ERRORS" if errors else ("warnings" if warns else "ok")
        print(f"== {slug}: {len(film.cards)} cards, {status}")
        for w in warns:
            print(f"  warn  {w}")
        for e in errors:
            print(f"  ERROR {e}")
        total_err += len(errors)
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
