"""Render intertitle cards to PNG from an HTML template + the film's style.css.

    python pipeline/tools/render.py <slug> --lang es                 # bilingual, stacked (default)
    python pipeline/tools/render.py <slug> --lang es --layout translation-only
    python pipeline/tools/render.py <slug> --layout source-only      # clean reproduction of the originals
    python pipeline/tools/render.py <slug> --lang es --only 003 007  # a few cards

Output: out/<slug>/<lang>/<layout>/<card id>.png at the film's render frame size.

This is the "automatic" path. A designer's hand-made card in data/films/<slug>/cards/<lang>/<id>.png
always wins over this output when assembling (see assemble.py). The template and the
per-film style.css are the surface designers can edit without touching Python.

Engine: headless Chrome. Set CHROME=<path> if it is not in the default location.
"""
from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from string import Template

from common import OUT, TEMPLATES, Film, load_film, load_translations

LAYOUTS = ("stacked", "side-by-side", "translation-only", "source-only")

CHROME_CANDIDATES = [
    os.environ.get("CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    found = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    raise SystemExit("Chrome not found; set CHROME=<path to chrome executable>")


def to_html(text: str) -> str:
    return "<br>".join(html.escape(line) for line in text.split("\n"))


def build_page(film: Film, card, translation: str | None, lang: str, layout: str) -> str:
    tpl = Template((TEMPLATES / "card.html").read_text(encoding="utf-8"))
    style_file = film.dir / "style.css"
    style = style_file.read_text(encoding="utf-8") if style_file.exists() else ""
    w, h = film.frame
    return tpl.substitute(
        width=w,
        height=h,
        film_style=style,
        layout=layout,
        card_type=card.type,
        card_id=card.id,
        source_lang=film.source_lang,
        target_lang=lang or "",
        source_html=to_html(card.text),
        translation_html=to_html(translation) if translation else "",
        has_translation="has-translation" if translation else "no-translation",
    )


def screenshot(chrome: str, page: Path, png: Path, size: tuple[int, int], profile: Path) -> None:
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--disable-extensions",
        f"--user-data-dir={profile}",
        f"--window-size={size[0]},{size[1]}",
        f"--screenshot={png}",
        page.resolve().as_uri(),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def render(film: Film, lang: str | None, layout: str, only: set[str] | None) -> list[Path]:
    if layout not in LAYOUTS:
        raise SystemExit(f"layout must be one of {LAYOUTS}")
    if layout != "source-only" and not lang:
        raise SystemExit("--lang is required unless --layout source-only")
    translations = load_translations(film.slug, lang) if lang else {}
    out_dir = OUT / film.slug / (lang or film.source_lang) / layout
    out_dir.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="intertitles-") as tmp:
        tmp = Path(tmp)
        profile = tmp / "profile"
        for c in film.cards:
            if only and c.id not in only:
                continue
            if not c.text.strip():
                continue
            tr = translations.get(c.id)
            if layout == "translation-only" and not tr:
                print(f"  skip {c.id}: no {lang} translation")
                continue
            page = tmp / f"{c.id}.html"
            page.write_text(
                build_page(film, c, tr if layout != "source-only" else None, lang or "", layout),
                encoding="utf-8",
            )
            png = out_dir / f"{c.id}.png"
            screenshot(chrome, page, png, film.frame, profile)
            written.append(png)
            note = "" if (tr or layout == "source-only") else "  (untranslated: source only)"
            print(f"  {png.relative_to(OUT)}{note}")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--lang", help="target language code, e.g. es")
    ap.add_argument("--layout", default="stacked", choices=LAYOUTS)
    ap.add_argument("--only", nargs="*", help="card ids to render")
    a = ap.parse_args()
    film = load_film(a.slug)
    files = render(film, a.lang, a.layout, set(a.only) if a.only else None)
    print(f"{len(files)} cards rendered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
