# Designing cards

For the typographers and designers who turn a translation into a finished intertitle.

## What you get

Per film, from the maintainer:

- `data/films/<slug>/film.yaml`: frame size (`render.frame`, usually 1920x1080) and notes on
  the original card style.
- `data/films/<slug>/cards.yaml`: every card with id, timecodes, type, speaker, English text.
- `data/locales/<lang>/<slug>.po`: the translations, keyed by card id.
- Reference frames of the original cards from the print (`out/<slug>/extract/<id>.jpg`,
  or a folder the maintainer sends).
- The automatic renders (`out/<slug>/<lang>/stacked/<id>.png`) as a starting point.

## What you hand back

PNG files, one per card, at the frame size, named by card id:

```
data/films/<slug>/cards/<lang>/042.png
```

That is the whole contract. The assembler uses your file for card 042 and the automatic
render for every card you did not do. You can deliver ten cards or all of them.

If you would rather work in CSS than in an image editor, edit `data/films/<slug>/style.css`
and rerun `python pipeline/tools/render.py <slug> --lang es-MX`. Fonts: put the file in `data/fonts/`
and reference it with `@font-face`; free (OFL) fonts only, so the repo can stay public.

## Constraints

- **Duration is fixed.** The card is on screen for the seconds listed. The lint tool has
  already checked reading speed for each language; your job is to make both languages
  findable at a glance so a reader loses no time hunting for theirs.
- **Both languages on one card**, unless the film's maintainer says the version is
  translation-only.
- **Safe area.** Keep text inside the middle 76% of the frame in both directions. The
  projector at the Brook may crop; the original cards do too.
- **Weight one language.** The film's original language is the primary; the translation
  is secondary in size or color but never below about 75% of the primary size. When the
  bilingual card is dense (lint says so), the secondary can go smaller, never the primary.
- **Match the film's card idiom.** Sherlock, Jr. cards, Lloyd art titles, and Chaplin's
  Mutual cards look different. Reproduce the original's typeface family, frame, and
  quotation style where you can; the reference frames are there for this. A house style
  across all three films is also acceptable if the maintainer chooses it.
- **Black background, light text.** The cards are cut into a black-and-white film. No
  color unless the original card had it (tinting).
- **Line breaks are the translator's.** Reflow only if the line does not fit; then break
  at a pause, and tell the translator in the string's comment thread.

## Versions

Keep your working files (Affinity, Illustrator, Photoshop, whatever) out of the repo or
in `data/films/<slug>/cards/<lang>/src/`; only the PNGs are used. If the translation changes
after you have made the card, the maintainer will tell you which ids; the PO file marks
changed strings as fuzzy.
