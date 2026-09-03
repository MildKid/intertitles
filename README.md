# Intertitles

Bilingual and translated intertitles for public-domain silent films. Translations are
crowdsourced (Crowdin), turned into cards (by designers, or automatically), and burned
back into the film with the running time untouched, so the result works with live
accompaniment.

Site: <https://intertitles.org/>. The films:

| Film | Year | Cards | Status |
|---|---|---|---|
| The Adventurer (Chaplin) | 1917 | `data/films/the-adventurer-1917/` | transcribed, unverified |
| Number, Please? (Lloyd) | 1920 | `data/films/number-please-1920/` | transcribed, unverified |
| Sherlock, Jr. (Keaton) | 1924 | `data/films/sherlock-jr-1924/` | transcribed, unverified; projection print in hand |
| The General (Keaton) | 1926 | `data/films/the-general-1926/` | print not chosen |
| Don Juan (Barrymore) | 1926 | `data/films/don-juan-1926/` | print not chosen |
| Faust (Murnau) | 1926 | `data/films/faust-1926/` | print not chosen |
| The Kid Brother (Lloyd) | 1927 | `data/films/the-kid-brother-1927/` | print not chosen |
| It (Bow) | 1927 | `data/films/it-1927/` | print not chosen |
| 7th Heaven (Borzage) | 1927 | `data/films/seventh-heaven-1927/` | print not chosen |
| Metropolis (Lang) | 1927 | `data/films/metropolis-1927/` | print not chosen |

First language pair: English to Mexican Spanish (`es-MX`).

## Layout

Three parts, each self-contained, each with its own README:

```
pipeline/   the toolchain: generic code that works for any silent film
  tools/      extract -> transcribe -> scrub -> export_po -> lint -> render -> assemble; status
  templates/  the automatic card template (card.html)
  tests/      make_clip.py builds a synthetic print and runs the whole pipeline on it
data/       the film data: what volunteers, designers, and transcribers touch
  films/<slug>/film.yaml        metadata + the reference print the timecodes belong to
  films/<slug>/cards.yaml       the cards: id, in, out, type, text, translator context (source of truth)
  films/<slug>/style.css        look of the automatic card for this film (designers edit this)
  films/<slug>/cards/<lang>/    hand-made cards, one PNG per card id (designers deliver here)
  locales/templates/<slug>.pot  generated: what Crowdin reads
  locales/<lang>/<slug>.po      translations, written back by Crowdin
  fonts/                        OFL fonts referenced from style.css
site/       the public website: hand-written HTML, no build step
docs/       design.md (how and why), translating.md (volunteers), designing.md (designers)
crowdin.yml at the root, because Crowdin looks for it there; it points into data/.
out/        generated, ignored
```

The pipeline reads film data only through the data root in `pipeline/tools/common.py`
(`INTERTITLES_DATA`, default `data/`). The site includes nothing from `data/` by hand; a
status file the pipeline writes is the planned link.

## Setup

Python 3.11+, ffmpeg and ffprobe on PATH, Google Chrome installed (for rendering).

```
python -m pip install -r pipeline/requirements.txt
python pipeline/tests/make_clip.py        # end-to-end smoke test on the fixture film
```

## Working a film

```
python pipeline/tools/extract.py <slug> --print path/to/print.mp4   # candidate cards + thumbnails
python pipeline/tools/transcribe.py <slug> --prepare                 # grabs + reader prompts; then --merge, --commit
#   ... fill the film.yaml print: block (source, sha256, fps) ...
python pipeline/tools/scrub.py <slug>                                # check each card against the print, in the browser
python pipeline/tools/align.py <slug> --print path/to/better-print.mp4   # re-time onto another copy; --apply to write
python pipeline/tools/export_po.py <slug>                            # POT + merge into each .po
#   ... translations arrive from Crowdin into data/locales/<lang>/<slug>.po ...
python pipeline/tools/lint.py <slug>                                 # reading speed, lines, missing
python pipeline/tools/render.py <slug> --lang es-MX                  # automatic cards -> out/
python pipeline/tools/assemble.py <slug> --lang es-MX --print path/to/print.mp4 --preview 00:02:00 00:03:00
python pipeline/tools/status.py                                     # site/status.json for the progress board
```

Prints are never committed. `docs/design.md` explains the data model, the pipeline, and
the decisions behind them.
