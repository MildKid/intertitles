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

import polib
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
PRINTS = Path(os.environ.get("INTERTITLES_PRINTS", ROOT / "prints")).resolve()   # never committed
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".ogv", ".m4v")

CARD_TYPES = ("title", "narrative", "dialogue", "insert", "credit")
FRAME_STYLES = ("none", "rule", "ornate", "illustrated")
ALIGNS = ("center", "left")
CONFIDENCES = ("high", "medium", "low")
STYLE_KEYS = ("frame", "align", "quote_style", "emphasis", "all_caps")
# Key order used when a tool writes cards.yaml. Unknown keys follow, in the order found.
CARD_KEY_ORDER = ("id", "in", "out", "type", "speaker", "text", "context", "notes",
                  "verified", "style", "confidence", "doubt")

_TC = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$")


def parse_tc(value: str | float | int | None) -> float | None:
    """'HH:MM:SS.mmm' or 'MM:SS.mmm' (or a bare number of seconds) -> seconds. None/'' -> None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
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
    tc_in: float | None      # None = not yet timed against the print
    tc_out: float | None
    text: str
    type: str = "dialogue"
    speaker: str = ""
    context: str = ""
    notes: str = ""
    index: int = 0
    verified: bool = False           # True only after a person checked the card against the frame
    style: dict = field(default_factory=dict)   # frame, align, quote_style, emphasis, all_caps; blank = unknown
    confidence: str = ""             # high | medium | low, from the transcription pass; blank = untranscribed
    doubt: str = ""                  # one line on what the reading is unsure about

    @property
    def timed(self) -> bool:
        return self.tc_in is not None and self.tc_out is not None

    @property
    def duration(self) -> float | None:
        return (self.tc_out - self.tc_in) if self.timed else None

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
    def timing_status(self) -> str:
        """'projection' once cards are timed against the file that will be projected;
        'reference' when timed against some other copy; 'none' when untimed."""
        return (self.meta.get("print") or {}).get("status") or "none"

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
    cp = d / "cards.yaml"          # absent until the film is transcribed; the film still lists
    cards_doc = (yaml.safe_load(cp.read_text(encoding="utf-8")) or {}) if cp.exists() else {}
    cards: list[Card] = []
    for i, raw in enumerate(cards_doc.get("cards") or [], start=1):
        cards.append(
            Card(
                id=str(raw["id"]),
                tc_in=parse_tc(raw.get("in")),
                tc_out=parse_tc(raw.get("out")),
                text=str(raw.get("text", "")).rstrip("\n"),
                type=raw.get("type", "dialogue"),
                speaker=raw.get("speaker", "") or "",
                context=(raw.get("context", "") or "").strip(),
                notes=(raw.get("notes", "") or "").strip(),
                index=i,
                verified=bool(raw.get("verified", False)),
                style=dict(raw.get("style") or {}),
                confidence=str(raw.get("confidence") or ""),
                doubt=str(raw.get("doubt") or "").strip(),
            )
        )
    return Film(slug=slug, meta=meta, cards=cards)


def load_cards_doc(slug: str) -> dict:
    """The raw cards.yaml document, for tools that edit it and write it back."""
    p = FILMS / slug / "cards.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    doc = doc or {}
    doc.setdefault("film", slug)
    doc["cards"] = list(doc.get("cards") or [])
    return doc


def pot_path(slug: str) -> Path:
    return LOCALES / "templates" / f"{slug}.pot"


def po_path(slug: str, lang: str) -> Path:
    return LOCALES / lang / f"{slug}.po"


def load_translations(slug: str, lang: str) -> dict[str, str]:
    """Card id -> translated text for one target language: non-fuzzy, non-empty entries
    only, keyed by msgctxt (the card id). {} when there is no .po file yet."""
    p = po_path(slug, lang)
    if not p.exists():
        return {}
    po = polib.pofile(str(p), encoding="utf-8")
    out: dict[str, str] = {}
    for e in po:
        if e.obsolete or "fuzzy" in e.flags or not e.msgstr or not e.msgctxt:
            continue
        out[e.msgctxt] = e.msgstr
    return out


DESIGNER_EXTS = (".png", ".webp", ".jpg")


def designer_card(slug: str, lang: str, card_id: str) -> Path | None:
    """A designer's hand-made card for this id, if one has been dropped in
    data/films/<slug>/cards/<lang>/. None if only the automatic render exists."""
    d = FILMS / slug / "cards" / lang
    for ext in DESIGNER_EXTS:
        p = d / f"{card_id}{ext}"
        if p.exists():
            return p
    return None


def rel(path: Path | str) -> str:
    """A path for a printed message: relative to the repo root when it sits inside it.
    Out-of-tree roots (INTERTITLES_OUT, a temp directory in the tests) print in full."""
    p = Path(path)
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def id_sort_key(card_id: str) -> tuple[int, str]:
    """'042' < '042a' < '043'. Non-numeric ids sort after numeric ones, alphabetically."""
    m = re.match(r"^(\d+)(.*)$", str(card_id))
    if not m:
        return (10**9, str(card_id))
    return (int(m.group(1)), m.group(2))


def _scalar(value) -> str:
    """One YAML scalar on one line, quoted only when YAML needs it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if s == "":
        return '""'
    dumped = yaml.safe_dump(s, allow_unicode=True, width=10**6, default_style=None).rstrip("\n")
    if dumped.endswith("\n..."):
        dumped = dumped[: -len("\n...")]
    return dumped


def _quoted(value) -> str:
    """Always double-quoted: ids and timecodes read as strings no matter what they look like."""
    if value is None or value == "":
        return '""'
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _emit_card(raw: dict, out: list[str]) -> None:
    ind = "    "
    first = True
    keys = [k for k in CARD_KEY_ORDER if k in raw] + [k for k in raw if k not in CARD_KEY_ORDER]
    for k in keys:
        v = raw[k]
        lead = "  - " if first else ind
        if k in ("in", "out") and (v is None or v == ""):
            continue                          # untimed card: omit in/out entirely
        if k in ("speaker", "doubt", "notes") and (v is None or v == ""):
            continue                          # blank optional line adds nothing
        first = False
        if k in ("id", "in", "out"):
            out.append(f"{lead}{k}: {_quoted(v)}")
        elif k == "text":
            text = "" if v is None else str(v).rstrip("\n")
            if text == "":
                out.append(f'{lead}text: ""')
            else:
                out.append(f"{lead}text: |2-" if text[0] == " " else f"{lead}text: |-")  # leading space needs an indent indicator
                for line in text.split("\n"):
                    out.append(f"{ind}  {line}" if line.strip() else "")
        elif k == "style":
            st = dict(v or {})
            out.append(f"{lead}style:")
            for sk in list(STYLE_KEYS) + [x for x in st if x not in STYLE_KEYS]:
                out.append(f"{ind}  {sk}: {_scalar(st.get(sk))}")
        elif isinstance(v, (dict, list)):
            block = yaml.safe_dump({k: v}, allow_unicode=True, sort_keys=False, width=10**6).rstrip("\n")
            for j, line in enumerate(block.split("\n")):
                out.append((lead if j == 0 else ind) + line)
        else:
            out.append(f"{lead}{k}: {_scalar(v)}")


def dump_cards(doc: dict, header: str | None = None) -> str:
    """cards.yaml text in the house layout: fixed key order, `|-` text blocks, one blank line
    between cards. Round-trips through yaml.safe_load."""
    out: list[str] = []
    if header:
        out.extend(f"# {line}".rstrip() for line in header.split("\n"))
    out.append(f"film: {_scalar(doc.get('film'))}")
    cards = list(doc.get("cards") or [])
    for k, v in doc.items():
        if k in ("film", "cards"):
            continue
        out.append(yaml.safe_dump({k: v}, allow_unicode=True, sort_keys=False, width=10**6).rstrip("\n"))
    if not cards:
        out.append("cards: []")
        return "\n".join(out) + "\n"
    out.append("cards:")
    for i, raw in enumerate(cards):
        if i:
            out.append("")
        _emit_card(raw, out)
    return "\n".join(out) + "\n"


CARDS_HEADER = ("Intertitle cards, in film order. Schema: data/README.md and films/_example/cards.yaml.\n"
                "Written by the pipeline (transcribe.py, scrub.py); hand edits are fine, key order is kept.")


def write_cards(slug: str, doc: dict, header: str | None = CARDS_HEADER) -> Path:
    text = dump_cards(doc, header)
    yaml.safe_load(text)                      # refuse to write something that does not parse
    p = FILMS / slug / "cards.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(p)
    return p


def find_print(slug: str, meta: dict, override: str | None = None) -> Path | None:
    """The local print to read: an explicit path, else film.yaml's print.file, else the first
    video under prints/<slug>.* or prints/<slug>/. None when nothing is there."""
    if override:
        p = Path(override)
        return p if p.exists() else None
    rec = (meta.get("print") or {}).get("file") or ""
    if rec:
        p = Path(rec)
        if not p.is_absolute():
            p = ROOT / p
        if p.exists():
            return p
    for ext in VIDEO_EXTS:
        p = PRINTS / f"{slug}{ext}"
        if p.exists():
            return p
    d = PRINTS / slug
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if p.suffix.lower() in VIDEO_EXTS:
                return p
    return None


def sha256_head(path: Path, limit_mb: int = 64) -> str:
    """Hash of the first N MB: identifies a print without reading a multi-GB file.
    Same figure assemble.py checks against film.yaml."""
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        h.update(f.read(limit_mb * 1024 * 1024))
    return h.hexdigest()


def probe(path: Path) -> dict:
    """ffprobe the first video stream: width, height, fps (float), duration (seconds)."""
    import json
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,avg_frame_rate:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    j = json.loads(out)
    st = (j.get("streams") or [{}])[0]
    rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/1"
    num, _, den = rate.partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"width": st.get("width"), "height": st.get("height"), "fps": round(fps, 3),
            "duration": float((j.get("format") or {}).get("duration") or 0)}


# ---------------------------------------------------------------- film.yaml

_FILM_KEY = re.compile(r"^(\s+)([A-Za-z_][\w.-]*)\s*:(.*)$")
# A trailing comment on a plain scalar line: keep it when the value is rewritten.
_TRAILING_COMMENT = re.compile(r"^([^'\"#]*?)(\s+#.*)$")
# Order used for print: keys the tool writes; keys already in the file keep their place.
PRINT_KEY_ORDER = ("status", "source", "source_file", "file", "sha256", "fps", "width",
                   "height", "duration", "size_bytes", "downloaded", "why")


def _film_value(key: str, value) -> str:
    """The scalar text for one print: key. Timecodes and ids stay quoted strings."""
    if key in ("duration", "file", "sha256", "downloaded"):
        return _quoted(value)
    return _scalar(value)


def write_film_print(slug: str, values: dict) -> Path:
    """Set keys inside film.yaml's `print:` block, leaving the rest of the file alone.

    The repo reads YAML with PyYAML, which drops comments and block scalars on a dump, and
    film.yaml is a hand-written file full of both. So this edits the block line by line:
    known keys are rewritten in place (keeping any trailing `# ...` comment), keys that are
    not in the file yet are appended to the block, and every other line -- including keys no
    tool knows about -- is untouched. Only `print:` is ever written.
    """
    p = FILMS / slug / "film.yaml"
    lines = p.read_text(encoding="utf-8").split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(r"^print\s*:", ln)), None)
    if start is None:
        raise ValueError(f"{p}: no `print:` block")
    end = len(lines)                                  # first line back at column 0
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i][:1].isspace():
            end = i
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1                                      # trailing blank lines belong to the gap
    block = lines[start + 1:end]

    spans: dict[str, tuple[int, int]] = {}            # key -> [first line, one past last)
    indent = None
    order: list[str] = []
    key_at = None
    for i, ln in enumerate(block):
        m = _FILM_KEY.match(ln)
        if m and (indent is None or len(m.group(1)) <= len(indent)):
            if key_at is not None:
                spans[order[-1]] = (key_at, i)
            indent, key_at = m.group(1), i
            order.append(m.group(2))
    if key_at is not None:
        spans[order[-1]] = (key_at, len(block))
    indent = indent if indent is not None else "  "

    out: list[str] = []
    i = 0
    while i < len(block):
        key = next((k for k, (a, _) in spans.items() if a == i), None)
        if key is None or key not in values:
            out.append(block[i])
            i += 1
            continue
        a, b = spans[key]
        comment = ""
        m = _FILM_KEY.match(block[a])
        cm = _TRAILING_COMMENT.match(m.group(3)) if m else None
        if cm and b - a == 1:
            comment = cm.group(2)
        out.append(f"{indent}{key}: {_film_value(key, values[key])}{comment}".rstrip())
        i = b
    for key in [k for k in PRINT_KEY_ORDER if k in values] + [k for k in values if k not in PRINT_KEY_ORDER]:
        if key not in spans:
            out.append(f"{indent}{key}: {_film_value(key, values[key])}")

    text = "\n".join(lines[:start + 1] + out + lines[end:])
    yaml.safe_load(text)                              # refuse to write something that does not parse
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(p)
    return p
