# Design

Bilingual and translated intertitles for public-domain silent films, with crowdsourced
translation and a path from translation to finished card to re-edited film.

The output is a file any theatre can project, with live accompaniment or without. First
films: *The Adventurer* (1917), *Number, Please?* (1920), *Sherlock, Jr.* (1924). First
language pair: English to Mexican Spanish (`es-MX`).

## Parts

The project has three parts with different contributors and different lifecycles. They
share one repo and are kept delineated at the top level; nothing crosses a boundary
except through a documented file contract.

| Part | Directory | What | Who touches it |
|---|---|---|---|
| Pipeline | `pipeline/` | generic code: works for any silent film | maintainer |
| Film data | `data/` | transcriptions, timecodes, translations, designer cards, fonts | volunteers via Crowdin, designers, transcribers |
| Website | `site/` | the public page | maintainer, later a designer |

Contracts across the boundaries:
- Pipeline reads data only through the data root in `pipeline/tools/common.py`
  (`INTERTITLES_DATA`, default `data/`), so the data could be a separate checkout.
- Pipeline writes only to `out/` (ignored) and to `data/locales/templates/` (the POTs).
- Site reads nothing from `data/` by hand. The planned link is a status file per film
  that the pipeline writes and the site includes.

## The one constraint that shapes everything

The film is accompanied live. A card that stays on screen for 3.2 seconds in the print
stays on screen for 3.2 seconds in the bilingual version. That rules out the easiest
bilingual approach (show the English card, then a Spanish card) because it changes the
running time and breaks the organist's cues. So:

- Every bilingual card fits inside the original card's frame and duration.
- Reading speed is the binding limit. `pipeline/tools/lint.py` measures characters per second per
  language and flags cards over the comfortable ceiling. A viewer reads only one of the
  two languages, so the per-language figure is the real one; the combined figure is an
  advisory for the designer to weight one language visually.
- Translations should be as short as the English or shorter. This is the opposite of the
  usual translation instinct and is the first thing volunteers need to hear.

A streaming or DVD version could relax this (alternating cards, longer holds). The data
model supports that later; nothing here depends on it.

## Data model

Source of truth lives in the repo, in plain text, diffable:

| File | Holds | Edited by |
|---|---|---|
| `data/films/<slug>/film.yaml` | metadata, rights note, the reference print, render frame size | maintainer |
| `data/films/<slug>/cards.yaml` | ordered cards: id, in, out, type, speaker, text, translator context | maintainer, after extraction |
| `data/locales/<lang>/<slug>.po` | translations keyed by card id | Crowdin (volunteers), synced back |
| `data/films/<slug>/style.css` | the automatic card's look for that film | designers |
| `data/films/<slug>/cards/<lang>/<id>.png` | hand-made cards, when a designer does one | designers |

Derived, never hand-edited: `data/locales/templates/<slug>.pot` (what Crowdin reads),
everything under `out/`.

A card:

```yaml
- id: "042"                 # stable forever; insert later cards as "042a", never renumber
  in: "00:07:12.500"        # timecode in the reference print; omit both until timed
  out: "00:07:16.250"
  type: dialogue            # title | narrative | dialogue | insert | credit
  speaker: The Girl's Father
  text: |-
    "You stole my watch."    # line breaks are the card's line breaks
  context: He is wrong, and the audience knows it.   # shown to translators in Crowdin
```

Card types matter downstream. `insert` cards (a letter, a newspaper, a sign in the shot)
are not intertitles and usually get a subtitle rather than a replacement card; they are
kept in the list so translators see them, and the renderer skips them by default once
that path exists. `credit` cards (studio, cast) are usually left alone.

### Why the card id is the key

The id is the msgctxt in the PO file, the filename of the rendered PNG, the filename a
designer hands back, and the thing the assembler matches on. One key, no lookup tables.
Two cards with identical text ("Come in.") stay separate strings in Crowdin because the
context differs; translators can copy between them.

### Text first, timing second

Translation needs the text and the card order, and nothing else. So a film's cards can
be transcribed from any public-domain copy and sent to Crowdin before anyone has the
print that will be projected. `in` and `out` are optional until then; `lint.py` counts
untimed cards, `export_po.py` tells translators the duration is not yet known, and
`assemble.py` skips them. Timing against the projection print is a separate, later pass
that touches only the timecodes and the `print:` block. Reading-speed checks switch on
as cards get timed, and translations that turn out too long get flagged then rather
than guessed at now.

`print.status` records what the timecodes mean: `none` (untimed), `reference` (timed
against some other copy; durations are close but the file differs), `projection` (timed
against the file that will be shown).

### Why timecodes are tied to a specific print

Public-domain films exist in many transfers with different frame rates, trims, and
leaders. A timecode without a print is meaningless. `film.yaml` records the print's
source and a hash; `assemble.py` refuses a different file unless told otherwise. When a
better print appears, the cards get re-timed against it and the hash updated. Text and
translations survive re-timing untouched, which is the point of separating them.

`pipeline/tools/align.py` does the measuring. It matches each card's own frames from the old
print against the new copy, reports the offset per card, and flags the ones that disagree with
the median, so a different leader (one constant offset) reads differently from a different cut
(a drift). `--apply` writes the new timecodes and the `print:` block; `print.status` stays as
it is, because only a person knows whether the new file is the one that gets projected.

## Pipeline

```
print ──► extract.py ──► candidates + thumbnails ──► transcribe (human / vision pass) ──► cards.yaml
                                                                                            │
                                                                            export_po.py ◄──┘
                                                                                 │
                                        data/locales/templates/<slug>.pot ────► Crowdin ────► data/locales/<lang>/<slug>.po
                                                                                                    │
                                                        ┌───────────────────────────────────────────┘
                                                        ▼
                                              lint.py (reading speed, lines, missing)
                                                        │
                       ┌────────────────────────────────┴──────────────────────────┐
                       ▼                                                           ▼
             render.py (automatic cards)                          designer package ──► designer
             out/<slug>/<lang>/<layout>/<id>.png                  data/films/<slug>/cards/<lang>/<id>.png
                       └────────────────────────────┬──────────────────────────────┘
                                                    ▼
                                    assemble.py (ffmpeg overlay on the print, runtime unchanged)
                                                    ▼
                                         out/<slug>/<slug>.<lang>.<layout>.mp4
```

Every stage is a separate script with a plain-file contract on each side. A stage can be
done by hand today and automated later without touching its neighbors: transcription is
by hand now; card design is by hand for the first films and automatic for anything
that comes after; the final edit could go through a real NLE instead of `assemble.py`
with the same PNGs and timecodes.

### Extraction

`pipeline/tools/extract.py` samples the print and flags runs of mostly-black frames with a little
bright material. It writes candidate cards with timecodes and a thumbnail each. Someone
then transcribes the thumbnails into `cards.yaml`, drops the false positives, and
tightens the timecodes. A vision-model pass over the thumbnails can draft the
transcription; a person still checks every card against the print before the film is
marked `locked`. Existing fan transcriptions online can speed this up but are not a
source of truth; the print is.

### Crowdsourcing

Crowdin, because Billy named it and it has a free plan for open-source projects, a GitHub
integration that opens PRs with translations, and first-class support for PO files with
per-string translator context. Weblate is the open-source alternative with the same
file format if Crowdin's terms change; nothing in the repo is Crowdin-specific except
`crowdin.yml`.

PO was chosen over JSON or CSV because every translation tool reads it, it carries
translator comments and context natively, it diffs well, and `msgmerge`-style merging
(kept, fuzzy, new) is a solved problem. The `.pot` per film is the source; one `.po` per
film per language is the translation.

What a volunteer sees per string, from the POT: card number and count, type, speaker,
seconds on screen, the context line, and a reminder to keep line breaks.

Setup steps, once the repo is public:
1. Create the Crowdin project (source language English, target Mexican Spanish).
2. Connect the GitHub repo; Crowdin reads `crowdin.yml`.
3. Add `docs/translating.md` as the project's guidelines.
4. Merge translation PRs into `main`, run `pipeline/tools/lint.py`.

### Rendering

`pipeline/tools/render.py` fills `pipeline/templates/card.html` with a card and its translation, appends
the film's `style.css`, and screenshots it with headless Chrome at the film's frame size.
HTML/CSS rather than a drawing library because designers already know CSS, web fonts
are free, and a film's original card frame can be dropped in as a background image.

Layouts: `stacked` (English over Spanish, a rule between; the default), `side-by-side`,
`translation-only` (a purely Spanish version), `source-only` (a clean reproduction of the
original cards, useful for comparison and for prints with damaged titles).

### Designer hand-off

A designer gets, per film: the reference frames of the original cards, `cards.yaml`, the
`.po`, the automatic renders as a starting point, and the frame size. They hand back
PNGs named by card id into `data/films/<slug>/cards/<lang>/`. The assembler prefers those over
automatic renders card by card, so a film can ship with ten hand-made cards and eighty
automatic ones.

### Assembly

`pipeline/tools/assemble.py` overlays each card image on the print for the card's `in`..`out`
window and re-encodes. Runtime, frame rate, and audio (if any) are untouched. `--preview`
encodes a short range for checking. The output file is what a theatre projects.

## Website

`site/`. Hand-written HTML in the look of an early-2000s hobbyist page (table layout,
`<font>`, `bgcolor`, a "last updated" line), CC0 clip art from openclipart.org (founded
2004, so period-correct), no build step. The one planned link to the rest of the repo is
a progress board: the pipeline will write a small status file (per film: extracted,
translated, designed, assembled) that the site includes. Nothing on the site is
maintained by hand from data that lives in `data/`.

## Licensing

Decided 2026-09-03; the terms live in `data/README.md` § Licenses and `LICENSE`.

- Films: public domain in the US. Say which print and confirm its status before publishing.
- Card text (transcriptions): public domain, as they reproduce the films.
- Translations and card designs: CC BY 4.0, so other theatres can use them with credit.
- Code: MIT.

Contributors on Crowdin agree to the project's license by contributing; the project
description says so.

## Open decisions

- Which print of each film a theatre will project, and whether the file is one this
  project can burn cards into. Timecodes wait on this; a film can carry timecodes for
  one print at a time.
- Whether a first screening uses bilingual cards for all three films or starts with
  the shortest (*The Adventurer*, ~24 minutes, plain cards) as the proof.
- Card typography per film: reproduce the original card style, or a house style across
  all three.
- German-language films (*Faust*, *Metropolis*): whether the bilingual card pairs the
  original German with Spanish, or an English translation with Spanish, for a US audience.
  Their `film.yaml` records `source: de` until this is decided.
