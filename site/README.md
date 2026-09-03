# Website

The public face of the project. Hand-written HTML in the style of an early-2000s
hobbyist site: table layout, `<font>`, `bgcolor`, a "last updated" line. No build step,
no framework. Edit `index.html` and publish this folder as-is.

The page carries one small inline script, at the bottom of the progress board section.
It fetches `status.json` and fills in the board's cells; the page reads correctly
without it, since every cell already holds a static placeholder (an em dash, or "not
timed"). Opening the file from disk, or in a browser that fails the fetch, leaves the
static page intact.

Graphics are CC0 clip art from openclipart.org; `img/SOURCES.md` lists each file's origin.

Nothing here is maintained by hand from `data/`. The one link to the rest of the repo is
`status.json`, written by `pipeline/tools/status.py` and committed alongside `index.html`
so the page has something to read on GitHub Pages without a build step.
