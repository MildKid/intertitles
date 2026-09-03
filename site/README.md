# Website

The public face of the project. Hand-written HTML in the style of an early-2000s
hobbyist site: table layout, `<font>`, `bgcolor`, a "last updated" line. No build step,
no framework. Edit `index.html` and publish this folder as-is.

The `<head>` also carries one small `<style>` block that exists only for narrow screens:
it lets the `width="640"` tables and rules shrink to the phone's width and caps images at
their container, then under a 700-pixel media query it enlarges the `<font size="1">` and
`size="2"` lines, shrinks the title and its reel icons so the header stays on one line,
narrows the icon columns, and tightens the progress board so all six columns fit. Above
700 pixels none of it applies, so the desktop page renders exactly as it did before.

The page carries one small inline script, at the bottom of the progress board section.
It fetches `status.json` and fills in the board's cells; the page reads correctly
without it, since every cell already holds a static placeholder (an em dash, or "not
timed"). Opening the file from disk, or in a browser that fails the fetch, leaves the
static page intact.

Graphics are CC0 clip art from openclipart.org; `img/SOURCES.md` lists each file's origin.

Nothing here is maintained by hand from `data/`. The one link to the rest of the repo is
`status.json`, written by `pipeline/tools/status.py` and committed alongside `index.html`
so the page has something to read on GitHub Pages without a build step.

The `<head>` carries one JSON-LD block (schema.org): the WebSite, the project as an
Organization, each film as a Movie marked public domain, the code as SoftwareSourceCode
under MIT, and the transcriptions and translations as a Dataset under CC BY 4.0. Add a
Movie node when a film is added; add a ScreeningEvent only once a screening is booked.
Check edits at <https://validator.schema.org/>.
