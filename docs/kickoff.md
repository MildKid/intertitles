# Kickoff: one-shot for the first working session

Paste everything below the rule into a fresh Fable session opened in `C:\ccode\intertitles`.
It assumes `CLAUDE.md` in this repo is loaded and that `docs/design.md` is the source of
truth for decisions already made. Do not re-open those decisions.

---

## Goal

Get all three films' English intertitles out of public-domain copies and into a state
where translators can start, with a local tool for me to verify every card against the
frame. Timing against the Brook's projection print is a later, separate pass and is out of
scope for this session. Do not ask me questions mid-run; make the routine calls, record
assumptions in the commit messages, and stop only if something makes the work useless.

Read `docs/design.md` and `CLAUDE.md` first. Everything below builds on them.

## Definition of done

1. Two new pipeline stages exist, are documented in `pipeline/README.md`, and run on the
   fixture film: `pipeline/tools/transcribe.py` and `pipeline/tools/scrub.py` (specs below).
2. Each of the three films has a reference copy recorded in `film.yaml` (`print.status:
   reference`, source URL, sha256, fps, size, duration), and the file itself sits under
   `prints/` (ignored by git).
3. Each film has a `cards.yaml` populated by extract + transcribe, every card carrying
   `verified: false`, provisional `in`/`out` from the reference copy, and the style fields
   below. Card ids are stable from this moment on.
4. I can run `python pipeline/tools/scrub.py <slug>` and verify cards in the browser.
5. `python pipeline/tools/lint.py` exits 0 across all films; POTs are exported.
6. `pipeline/tools/status.py` writes `site/status.json` and the site shows a per-film
   progress row from it. `docs/go-public.md` holds the checklist for flipping the repo
   public, GitHub Pages for `site/`, and Crowdin setup (below).
7. Every commit message says what was assumed. A final summary lists per film: card count,
   how many transcriptions the vision pass flagged as uncertain, and anything I must decide.

## Stage specs

### transcribe.py

Input: `out/<slug>/extract/candidates.yaml` and its thumbnails, produced by `extract.py`.
Output: the same list with `text`, `style`, and `confidence` filled in, written to
`out/<slug>/extract/transcribed.yaml`, plus a `--commit` flag that writes it into
`data/films/<slug>/cards.yaml` (refusing to overwrite a card already marked `verified: true`).

The reading is a vision pass, done by sub-agents, not OCR. The script's job is to make that
pass efficient and repeatable:

- Crop and grade each thumbnail for reading (full-resolution frame grab at the card's
  midpoint, not the 160x90 detection sample; autocontrast; keep the frame edges so
  ornaments are visible).
- Build batches of 8 to 12 cards per sub-agent call, each batch a numbered contact
  sheet or a list of image paths, and write the exact prompt each batch gets to
  `out/<slug>/extract/batches/NN.md` so a batch can be rerun alone.
- The prompt asks for, per card, in JSON: `text` with line breaks exactly as on the card;
  `case` (`as-is` text keeps original capitalization; note `all-caps` as a style flag rather
  than shouting in the text); `style` with `frame` (none | rule | ornate | illustrated),
  `align` (center | left), `quote_style` (the marks used on dialogue cards), `emphasis`
  (any italics or size changes, described); `type` (title | narrative | dialogue | insert |
  credit); `confidence` (high | medium | low) and a one-line `doubt` when not high.
- Merge the JSON back by card id. Never let the model renumber or drop cards; a card it
  cannot read gets empty text and `confidence: low`.
- Run two independent passes on the low-confidence cards with a different batch grouping
  and keep the agreement; disagreements stay low with both readings in `doubt`.

Card schema additions (document in `data/README.md` and `data/films/_example/cards.yaml`):
`verified: false`, `style: {frame, align, quote_style, emphasis, all_caps}`,
`confidence`, `doubt`. Unknown stays blank, never guessed.

### scrub.py

A local verification tool: `python pipeline/tools/scrub.py <slug>` starts a small Python
HTTP server (stdlib only) on a free port, opens the browser, and serves one page. No build
step, no framework, no external requests. The page:

- Plays the reference copy from `prints/` in an HTML5 video element with a scrubber, and a
  card list down one side.
- Selecting a card seeks to its `in`, shows the full-resolution grab and the transcript in
  an editable text box (line breaks preserved), the style fields, the confidence and doubt,
  and prev/next keys. Nudge buttons adjust `in`/`out` by a frame or half a second while
  the video shows the result.
- A verify button marks the card `verified: true` and moves on. Edits save to
  `data/films/<slug>/cards.yaml` immediately via the server; the yaml stays hand-readable
  (keep key order, keep the block-scalar text style).
- A header shows verified / total and the films' timing status.

The Brook's projection print, when it arrives, goes through the same page with only the
timecodes changing; design for that now (the page must not assume the file in `prints/`
is the one the timecodes were made against; it reads `film.yaml` for that).

### status.py and the site

`status.py` writes `site/status.json`: per film, counts of cards, verified, translated per
language (from the PO files), designed (PNGs present), and the timing status. `site/index.html`
reads it with the smallest possible inline script and fills a progress table; the page must
still read correctly with the script blocked (leave the static status text in place).

## Sources

Use archive.org or Wikimedia Commons copies. Prefer the highest-resolution plain transfer
without a modern score's credits or restoration titles; a transfer with a music track is
fine. Record the exact item URL, the file chosen, and why, in `film.yaml`. Do not use a
copy whose page claims a restoration copyright. If a film's best copy is clearly a
different edit from the Brook's likely print (a shorter cut, missing cards), say so in the
summary; text still gets transcribed.

## Crowdin

Do not create the Crowdin project; I do that by hand. Write `docs/go-public.md` with the
exact steps for the free tier: project settings (source en, target es-MX, export only
approved translations, open project), connecting the GitHub repo and `crowdin.yml`,
uploading the card grabs as screenshots tagged to their strings so translators see the
card (write `pipeline/tools/crowdin_screenshots.py` against the Crowdin API v2 with a token
from the environment, and confirm in the doc whether screenshots are available on the
free tier; if they are not, say what the fallback is), and pasting `docs/translating.md`
as the guidelines. Leave the license decision in the doc as a question for me with the
proposed answer from `docs/design.md`.

## Orchestrating agents

This session's model is for judgment. Everything mechanical goes to sub-agents through the
Agent tool's `model` parameter, and bulk data never enters this session's context.

- **Vision transcription batches → `sonnet`**, one agent per batch, launched in parallel,
  all in one message. Each prompt is blunt and self-contained: the image paths, the exact
  JSON shape, "return only the JSON". Sonnet reads ornate title fonts reliably; Haiku
  does not, so do not downgrade. Second-pass disagreements on low-confidence cards go to
  one `opus` agent per film, with both readings and the grabs.
- **Downloads, hashing, ffprobe, extraction runs → `haiku`**, or better, plain scripts run
  from Bash with no model at all. Extraction is a script; do not narrate it.
- **Verification is a fresh-context job.** After transcribe writes `cards.yaml`, one
  `haiku` agent per film checks the file against its transcribed.yaml (counts, ids,
  no dropped cards, no card with text but `verified: true`). Do not tell workers to
  double-check themselves.
- **Build work on the two new tools stays in this session** or goes to a single `opus`
  agent with the spec above and a clear output contract, not enumerated steps.
- **Headroom.** Before any fan-out of six or more agents, run
  `ccusage blocks --active --json` and read `costUSD` against the working ceiling in
  `CLAUDE.md`. Three films at roughly 60 to 120 cards each means 20 to 40 transcription
  batches; run them per film, check headroom between films, and shrink batches rather
  than let a limit kill a run half-way.
- **Escalate, do not retry.** A failed or ambiguous cheap agent gets its one piece
  handled a tier up; never re-delegate the whole job and never redo it in-session.
- **Prompts for cheap tiers are exact**; prompts for `opus` state goal, constraints, and
  the output contract.

## Order of work

Build transcribe and scrub on the fixture first, so the three real films go through a
tool that already works. Then pull the copies, extract, transcribe, lint, status, docs.
Commit at each stage boundary. Stop when done and give me the summary.
