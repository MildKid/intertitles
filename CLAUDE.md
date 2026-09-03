# Intertitles

Bilingual and translated intertitles for public-domain silent films. Read `docs/design.md`
before changing anything; it holds the decisions and why. `docs/kickoff.md` is the
first-session prompt.

## Layout

Three delineated parts. Nothing crosses a boundary except through a documented file
contract (`docs/design.md` § Parts).

- `pipeline/` generic toolchain: tools, card template, tests. Knows no specific film.
- `data/` film data: `films/<slug>/{film.yaml,cards.yaml,style.css,cards/<lang>/}`,
  `locales/`, `fonts/`. What volunteers and designers touch.
- `site/` hand-written early-2000s HTML, no build step.
- `prints/` and `out/` are ignored by git. Prints are never committed.

## Rules that do not bend

- Card ids are stable forever. Insert as `042a`; never renumber.
- `in`/`out` are optional until a film is timed; text and order are enough for
  translation. `print.status` says what the timecodes mean: none | reference | projection.
- Runtime never changes: bilingual text fits the original card's frame and duration.
- Target language codes are Crowdin locales (`es-MX`, Mexican Spanish, never Castilian).
- Blank means unknown. Record actuals, never plans, in `film.yaml` and `cards.yaml`.
- A card is `verified: true` only after a person checked it against the frame.
- Free/OSS only: fonts OFL, clip art CC0, no paid services beyond Crowdin's free tier.

## Running things

From the repo root, `python pipeline/tools/<stage>.py`. `python pipeline/tests/make_clip.py`
runs the whole pipeline on the fixture film `_example`. Set `PYTHONIOENCODING=utf-8` on
Windows. Chrome renders cards; ffmpeg assembles.

## Agents and token economy

The session model is for judgment. Mechanical work goes to sub-agents via the Agent
tool's `model` parameter; bulk data (frames, logs, transcripts) stays out of the session.

| Tier | Use for |
|---|---|
| session | judgment, design, anything user-facing |
| `opus` | hard, self-contained, delegable work: a tool build from a spec, a disagreement to resolve |
| `sonnet` | multi-step execution: vision transcription batches, HTML generation, structured extraction |
| `haiku` | mechanical: run a script and report, fixed-format checks, bulk reads returning a summary |

- Cheap-tier prompts are blunt and fully specified; `opus` prompts state goal,
  constraints, output contract.
- Verification is a fresh-context `haiku` job on mechanical facts, an `opus` job on
  judgment. Never tell a worker to double-check itself.
- Escalate one tier for the failed piece; never re-delegate the whole job.
- Before a fan-out of six or more agents, and between batches:
  `ccusage blocks --active --json`. Read `costUSD` against the plan's 5-hour window
  ceiling; keep a batch under a third of what is left.
- Launch independent agents in one message so they run in parallel.

## Style

Prose to the maintainer follows Google developer-documentation style: second person,
active voice, sentence-case headings, no "please" or "simply". No negation-as-setup
("this isn't X, it's Y"), no litotes, no irony. State the positive.
