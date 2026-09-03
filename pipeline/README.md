# Pipeline

Generic toolchain. Nothing in here knows about a specific film; everything comes from
the data root (`INTERTITLES_DATA`, default `../data`).

```
python -m pip install -r pipeline/requirements.txt
python pipeline/tests/make_clip.py       # end-to-end smoke test on the fixture film
```

Stages, each a script with a plain-file contract on both sides (run from the repo root):

| Script | Reads | Writes |
|---|---|---|
| `tools/extract.py` | a print | `out/<slug>/extract/` candidates + thumbnails |
| `tools/export_po.py` | `data/films/<slug>/cards.yaml` | `data/locales/templates/<slug>.pot`, merges into `data/locales/<lang>/<slug>.po` |
| `tools/lint.py` | cards + translations | report; exit 1 on errors |
| `tools/render.py` | cards, translations, `pipeline/templates/card.html`, the film's `style.css` | `out/<slug>/<lang>/<layout>/<id>.png` |
| `tools/assemble.py` | a print, designer cards, rendered cards | `out/<slug>/<slug>.<lang>.<layout>.mp4` |

Requires Python 3.11+, ffmpeg/ffprobe on PATH, Google Chrome (set `CHROME` if not in
the default location). See `docs/design.md` for why each stage is shaped the way it is.
