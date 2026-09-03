# Intertitles

Bilingual and translated intertitles for public-domain silent films. Translations are
crowdsourced (Crowdin), turned into cards (by designers, or automatically), and burned
back into the film with the running time untouched, so the result works with live
accompaniment.

First program, Silent Movie Day at the Brook Arts Center, Bound Brook NJ, Sept 27, 2026:

| Film | Year | Cards | Status |
|---|---|---|---|
| The Adventurer (Chaplin) | 1917 | `films/the-adventurer-1917/` | print not chosen |
| Number, Please? (Lloyd) | 1920 | `films/number-please-1920/` | print not chosen |
| Sherlock, Jr. (Keaton) | 1924 | `films/sherlock-jr-1924/` | print not chosen |

First language pair: English to Mexican Spanish (`es-MX`).

## Layout

```
films/<slug>/film.yaml        film metadata + the reference print the timecodes belong to
films/<slug>/cards.yaml       the cards: id, in, out, type, text, translator context  (source of truth)
films/<slug>/style.css        look of the automatic card for this film (designers edit this)
films/<slug>/cards/<lang>/    hand-made cards, one PNG per card id (designers deliver here)
locales/templates/<slug>.pot  generated: what Crowdin reads
locales/<lang>/<slug>.po      translations, written back by Crowdin
templates/card.html           the automatic card template
tools/                        extract -> export_po -> lint -> render -> assemble
docs/                         design.md (how and why), translating.md, designing.md
site/                         the project website (hand-written HTML)
tests/make_clip.py            builds a synthetic print and runs the whole pipeline on it
```

## Setup

Python 3.11+, ffmpeg and ffprobe on PATH, Google Chrome installed (for rendering).

```
python -m pip install -r requirements.txt
python tests/make_clip.py        # end-to-end smoke test on the fixture film
```

## Working a film

```
python tools/extract.py <slug> --print path/to/print.mp4   # candidate cards + thumbnails
#   ... transcribe into films/<slug>/cards.yaml, fill film.yaml print: block ...
python tools/export_po.py <slug>                            # POT + merge into each .po
#   ... translations arrive from Crowdin into locales/<lang>/<slug>.po ...
python tools/lint.py <slug>                                 # reading speed, lines, missing
python tools/render.py <slug> --lang es-MX                  # automatic cards -> out/
python tools/assemble.py <slug> --lang es-MX --print path/to/print.mp4 --preview 00:02:00 00:03:00
```

Prints are never committed. `docs/design.md` explains the data model, the pipeline, and
the decisions behind them.
