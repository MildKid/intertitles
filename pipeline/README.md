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
| `tools/ocr.py` | candidates + the frame grabs | `out/<slug>/extract/ocr.yaml`: the cards a local OCR pass settles, so the vision pass reads the rest |
| `tools/transcribe.py` | candidates + the print | `out/<slug>/extract/grabs/`, reader prompts in `batches/`, `transcribed.yaml`; `--commit` writes `data/films/<slug>/cards.yaml` |
| `tools/scrub.py` | a print, cards, the frame grabs under `out/<slug>/extract/` | `data/films/<slug>/cards.yaml`, edited card by card in the browser |
| `tools/align.py` | cards + the print they were timed against + another copy | the offset per card; `--apply` moves `in`/`out` and the `print:` block |
| `tools/export_po.py` | `data/films/<slug>/cards.yaml` | `data/locales/templates/<slug>.pot`, merges into `data/locales/<lang>/<slug>.po` |
| `tools/crowdin_screenshots.py` | cards, frame grabs under `out/<slug>/extract/` | screenshots in the Crowdin project, tagged to their strings (needs `CROWDIN_TOKEN`) |
| `tools/lint.py` | cards + translations | report; exit 1 on errors |
| `tools/render.py` | cards, translations, `pipeline/templates/card.html`, the film's `style.css` | `out/<slug>/<lang>/<layout>/<id>.png` |
| `tools/assemble.py` | a print, designer cards, rendered cards | `out/<slug>/<slug>.<lang>.<layout>.mp4` |
| `tools/status.py` | cards, translations, designer cards | `site/status.json` (the pipeline-to-site contract) |

Requires Python 3.11+, ffmpeg/ffprobe on PATH, Google Chrome (set `CHROME` if not in
the default location). See `docs/design.md` for why each stage is shaped the way it is.

## Verifying cards

`scrub.py` is where a person checks each card against the print and fixes what the
transcription pass got wrong:

```
python pipeline/tools/scrub.py <slug>                    # or --print path/to/print.mp4
```

It starts a local server on a free port and opens one page: the print in a video element,
the cards down the left, and the selected card's text, style, and timecodes on the right.
Every edit goes straight to `data/films/<slug>/cards.yaml`, which keeps its key order, its
`|-` text blocks, and any keys the tools do not know about. Add `--port N` to pin the port
and `--no-browser` to print the URL instead of opening it.

Keys, with the shortcuts inactive while a text field has focus (Escape leaves the field):

| Key | Does |
|---|---|
| `j` `k`, arrow keys | next / previous card |
| `v` | verify the card and advance |
| space | play / pause |
| `i` `o` | set in / out from the video position |
| `[` `]` | nudge in by one frame |
| `{` `}` | nudge out by one frame |

`verified: true` means a person read that card against the frame in the print. Nothing else
sets it, and a verified card has to be un-verified before it can be dropped. Cards you add
take the documented insertion id (`042a` after `042`, the next number after the last card);
ids are never renumbered.

The page reads `film.yaml` for which file the timecodes belong to, so it never assumes the
copy it is playing is that file. The header shows `print.status` (none, reference, or
projection) and compares `common.sha256_head` of the file being played with `print.sha256`:
"matches film.yaml", "differs from film.yaml (timecodes belong to another file)", or
"film.yaml records no print". Retiming against a new print is the same page: play the new
file, nudge each in and out, verify, then update the `print:` block.

## Transcribing cards

The reading is a vision pass by sub-agents (or a person); `ocr.py` only narrows it down,
and no card reaches `verified: true` without a person. `transcribe.py` makes the pass
repeatable:

```
python pipeline/tools/ocr.py <slug>                         # local OCR pre-read: ocr.yaml, so --prepare batches only what OCR could not settle (--all ignores it)
python pipeline/tools/transcribe.py <slug> --prepare        # full-res grabs at each card's midpoint, id-stamped copies for readers, batches/NN.md prompts
#   give each batches/NN.md to one reader; it writes batches/NN.response.json
python pipeline/tools/transcribe.py <slug> --merge          # all responses -> transcribed.yaml (counts by confidence, rejects, unread)
python pipeline/tools/transcribe.py <slug> --second-pass    # p2-NN.md prompts for the low-confidence cards, different grouping
python pipeline/tools/transcribe.py <slug> --adjudicate     # adjudicate.md for pass-1/pass-2 disagreements, for one stronger reader
python pipeline/tools/transcribe.py <slug> --commit         # transcribed.yaml -> cards.yaml; never overwrites a verified card
```

Each prompt is self-contained and names the response file, so a single batch can be rerun.
The reader's copy of every grab carries the card id in its corner; a reader that skips an
image cannot shift the readings of the ones after it. Candidates the readers mark as not an
intertitle (a still shot, a fade) stay out of `cards.yaml`; their ids are simply unused.
