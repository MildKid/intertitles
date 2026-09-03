"""Shared loaders for the intertitle toolchain.

Source of truth:
  data/films/<slug>/film.yaml   film metadata + the reference print the timecodes belong to
  data/films/<slug>/cards.yaml  the ordered list of intertitle cards (source language)
  data/locales/<lang>/<slug>.po translations, round-tripped through Crowdin

Everything else (POT files, rendered PNGs, assembled video) is derived.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]          # repo root
PIPELINE = ROOT / "pipeline"
# The data root is where films/ and locales/ live. It defaults to this repo, but the
# tools never assume it: set INTERTITLES_DATA to point at a separate data checkout
# (data/ is the delineated film-data part of the repo).
DATA = Path(os.environ.get("INTERTITLES_DATA", ROOT / "data")).resolve()
FILMS = DATA / "films"
LOCALES = DATA / "locales"
TEMPLATES = PIPELINE / "templates"
OUT = Path(os.environ.get("INTERTITLES_OUT", ROOT / "out")).resolve()

CARD_TYPES = ("title", "narrative", "dialogue", "insert", "credit")

_TC = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$")


def parse_tc(value: str | float | int) -> float:
    """'HH:MM:SS.mmm' or 'MM:SS.mmm' (or a bare number of seconds) -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _TC.match(str(value).strip())
    if not m:
        raise ValueError(f"bad timecode {value!r} (want HH:MM:SS.mmm)")
    h, mi, s, ms = m.groups()
    ms = (ms or "0").ljust(3, "0")
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000


def fmt_tc(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    mi, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(mi):02d}:{s:06.3f}"


@dataclass
class Card:
    id: str
    tc_in: float
    tc_out: float
    text: str
    type: str = "dialogue"
    speaker: str = ""
    context: str = ""
    notes: str = ""
    index: int = 0

    @property
    def duration(self) -> float:
        return self.tc_out - self.tc_in

    @property
    def key(self) -> str:
        """Stable identity used as msgctxt and as the rendered filename."""
        return self.id


@dataclass
class Film:
    slug: str
    meta: dict
    cards: list[Card] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return FILMS / self.slug

    @property
    def title(self) -> str:
        return self.meta.get("title", self.slug)

    @property
    def source_lang(self) -> str:
        return self.meta.get("languages", {}).get("source", "en")

    @property
    def target_langs(self) -> list[str]:
        return list(self.meta.get("languages", {}).get("targets", []))

    @property
    def frame(self) -> tuple[int, int]:
        f = self.meta.get("render", {}).get("frame", "1920x1080")
        w, h = f.lower().split("x")
        return int(w), int(h)


def list_films(include_example: bool = False) -> list[str]:
    slugs = sorted(p.name for p in FILMS.iterdir() if (p / "film.yaml").exists())
    if not include_example:
        slugs = [s for s in slugs if not s.startswith("_")]
    return slugs


def load_film(slug: str) -> Film:
    d = FILMS / slug
    meta = yaml.safe_load((d / "film.yaml").read_text(encoding="utf-8")) or {}
    cards_doc = yaml.safe_load((d / "cards.yaml").read_text(encoding="utf-8")) or {}
    cards: list[Card] = []
    for i, raw in enumerate(cards_doc.get("cards") or [], start=1):
        cards.append(
            Card(
                id=str(raw["id"]),
                tc_in=parse_tc(raw["in"]),
                tc_out=parse_tc(raw["out"]),
                text=str(raw.get("text", "")).rstrip("\n"),
                type=raw.get("type", "dialogue"),
                speaker=raw.get("speaker", "") or "",
                context=(raw.get("context", "") or "").strip(),
                notes=(raw.get("notes", "") or "").strip(),
                index=i,
            )
        )
    return Film(slug=slug, meta=meta, cards=cards)


def po_path(slug: str, lang: str) -> Path:
    return LOCALES / lang / f"{slug}.po"


def pot_path(slug: str) -> Path:
    return LOCALES / "templates" / f"{slug}.pot"


def load_translations(slug: str, lang: str) -> dict[str, str]:
    """msgctxt (card id) -> translated text. Missing file -> {}. Fuzzy entries are skipped."""
    import polib

    p = po_path(slug, lang)
    if not p.exists():
        return {}
    po = polib.pofile(str(p), encoding="utf-8")
    out: dict[str, str] = {}
    for e in po:
        if e.obsolete or "fuzzy" in e.flags or not e.msgstr:
            continue
        if e.msgctxt:
            out[e.msgctxt] = e.msgstr
    return out


def designer_card(slug: str, lang: str, card_id: str) -> Path | None:
    """A hand-made card, if a designer has delivered one. Filename == card id."""
    for ext in ("png", "webp", "jpg"):
        p = FILMS / slug / "cards" / lang / f"{card_id}.{ext}"
        if p.exists():
            return p
    return None
