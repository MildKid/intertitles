# Going public

The order below matters: the license decides what contributors agree to, the repo has to
be public before Crowdin's GitHub integration and GitHub Pages can reach it, and the
Crowdin link goes on the site last, once strings are really there.

Anything this checklist could not confirm from Crowdin's documentation is marked
"(unconfirmed; check in the UI)". Treat those as the steps to read twice.

## 1. Decide the license (your call)

Decided 2026-09-03: the proposal below, as written, with `Copyright (c) 2026 Billy Mild` in
`LICENSE`. If the project later becomes a GSTOS project, add the organisation as a second
copyright line (or assign the copyright to it) in `LICENSE`; MIT allows either without
relicensing.

| What | License |
|---|---|
| The films | public domain in the US; name the print and confirm its status per film |
| Card text (transcriptions) | public domain, since they reproduce the films |
| Translations and card designs | CC BY 4.0, so other theatres can use them with credit |
| Code (`pipeline/`, `site/`) | MIT |

Once you decide, three things need to exist:

1. `LICENSE` at the repo root: the MIT text, `Copyright (c) 2026 <your name>`, plus two
   lines saying it covers the code and pointing at `data/README.md` for everything else.
   MIT is OSI-approved, which Crowdin's open-source plan asks for.
2. A **Licenses** section in `data/README.md`: transcriptions are public domain,
   translations and designer cards are CC BY 4.0, and contributing to this repo or to the
   Crowdin project places your work under those terms.
3. One sentence in the Crowdin project description, so volunteers see it before they
   type: "Translations contributed here are published under CC BY 4.0 and credited to
   their translators."

## 2. Check the repo, then flip it public

The history goes public with the repo, so check before, not after. From the repo root:

```
git ls-files | grep -i prints                       # expect no output
git ls-files | grep -Ei "\.(mp4|mkv|mov|avi|webm|ogv|m4v)$"   # expect no output
git log --all --diff-filter=A --name-only --pretty=format: | sort -u | grep -Ei "\.(mp4|mkv|mov|avi|env)$"
git log -p --all | grep -Ein "api[-_ ]?key|secret|token|password" | head
```

`.gitignore` already lists `*.mp4`, `*.mkv`, `*.mov`, `*.avi`, `out/`, and `prints/`. The
pipeline also recognizes `.webm`, `.ogv`, and `.m4v` as prints, so add those three lines
to `.gitignore` before someone drops such a file next to a tracked one.

Then, on GitHub:

1. Go to `https://github.com/MildKid/intertitles` → **Settings** → **General**.
2. Scroll to **Danger Zone** → **Change repository visibility** → **Change to public**.
3. Read the warnings, type the repository name, confirm.

## 3. Serve `site/` with GitHub Pages

GitHub Pages serves a branch root or a `/docs` folder, and this site lives in `site/`, so
the deployment runs from Actions. `.github/workflows/pages.yml` is in the repo: on every
push to `main` it uploads `site/` with `actions/upload-pages-artifact` and publishes it
with `actions/deploy-pages`, under `permissions: pages: write, id-token: write` and the
`github-pages` environment.

1. Push the workflow to `main`.
2. **Settings** → **Pages** → **Build and deployment** → **Source**: **GitHub Actions**.
3. Open the **Actions** tab and watch the `Pages` run. The URL appears on the run's
   `deploy` job and on the Pages settings screen: `https://mildkid.github.io/intertitles/`.

### Custom domain: intertitles.org

`site/CNAME` holds `intertitles.org`, so the Pages artifact carries the domain with it.
At the registrar, add these DNS records (GitHub's current Pages addresses; confirm at
<https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site>):

| Host | Type | Value |
|---|---|---|
| `@` | A | `185.199.108.153` |
| `@` | A | `185.199.109.153` |
| `@` | A | `185.199.110.153` |
| `@` | A | `185.199.111.153` |
| `@` | AAAA | `2606:50c0:8000::153` |
| `@` | AAAA | `2606:50c0:8001::153` |
| `@` | AAAA | `2606:50c0:8002::153` |
| `@` | AAAA | `2606:50c0:8003::153` |
| `www` | CNAME | `mildkid.github.io` |

Then **Settings** → **Pages** → **Custom domain**: type `intertitles.org`, save, wait for
the DNS check to pass, and tick **Enforce HTTPS** once the certificate is issued (up to an
hour). Verifying the domain under your account profile (**Settings** → **Pages** →
**Add a domain**) stops anyone else's Pages site from claiming it. Once live, the site is
`https://intertitles.org/` and `site/status.json` fetches relative to it, so nothing in
`site/` changes.

The page needs no build step. `site/status.json` is written by `pipeline/tools/status.py`
and committed, and `index.html` fetches it with a small inline script; served over HTTPS
from Pages, the progress board fills in. Re-run `status.py` and commit `status.json`
whenever the numbers change.

## 4. Set up Crowdin

You create the project by hand in your own account. The steps below assume you are the
project owner.

### 4.1 Pick the plan

Crowdin's open-source license is free and grants unlimited projects, strings, and
members. Request it at <https://crowdin.com/page/open-source-project-setup-request>. The
conditions listed there:

- an OSI-approved license (MIT covers the code)
- source publicly available
- no commercial product tied to the project
- the requester is the project lead
- the project has been active for at least three months
- an active community, a regularly updated website or news feed, and regular releases

A repo this new meets some of those and misses the three-months-of-activity and
regular-releases conditions. So start on the plain free plan, run the first film through
it, and send the open-source request once the repo has a few months of commits, tagged
releases, and a screening to point at. Which features the open-source plan excludes is
unconfirmed; the plain free plan's string and member limits are unconfirmed as well
(check <https://crowdin.com/pricing>).

If the free plan turns out too small for three films, the fallback is Weblate's hosted
libre plan, which reads the same PO files (`docs/design.md` § Crowdsourcing). Nothing in
the repo is Crowdin-specific apart from `crowdin.yml`.

### 4.2 Create the project

**Create Project** on the Crowdin dashboard:

- **Name**: `Intertitles` (public URL slug `intertitles`).
- **Source language**: English.
- **Target language**: Spanish, Mexico — Crowdin's code `es-MX`. Add this one only;
  `%locale%` in `crowdin.yml` expands to the full code with the region, which is what
  `data/locales/es-MX/` expects.
- **Description**: the license sentence from step 1 and a link to
  `https://github.com/MildKid/intertitles`.

Then **Project Settings** → **Privacy & Collaboration**:

- **Privacy**: **Public**, so anyone can find the project and join.
- **Moderated project joining**: **off**, so a volunteer starts translating without
  waiting for you to approve the request.

### 4.3 Export settings

**Project Settings** → **Export**:

- **Export only approved translations**: **on**. Translations then reach the repo after
  someone proofreads them. The option depends on the workflow carrying a proofreading
  step; if it is greyed out, open **Project Settings** → **Workflow**, add a
  **Proofread** step after translation, save, and set the option again. Leaving it off
  works too, at the cost of raw translations landing in the PR; `pipeline/tools/lint.py`
  and your review of the PR are the check in that case.
- **Skip untranslated strings**: **off**. The PO files then keep an empty `msgstr` for
  every untranslated card, which is what `export_po.py` merges against and what `lint.py`
  counts as missing. With the option on, untranslated cards vanish from the file and the
  counts go wrong.

The exact wording of both options on crowdin.com is unconfirmed; check in the UI.

### 4.4 Add the guidelines

Paste `docs/translating.md` into the project's guidelines field, under **Project
Settings** → **General** (the field is likely named **Project Description** or
**Translation Guidelines**; exact location unconfirmed; check in the UI). The rule about
staying shorter than the English is the one volunteers need first, so keep it at the top.

### 4.5 Connect the GitHub repo

1. Project → **Integrations** → **GitHub** → **Set Up Integration**.
2. Mode: **Source and translation files**.
3. **Authorize** Crowdin against your GitHub account. The repo must be public, or the
   authorization must cover private repos.
4. Choose `MildKid/intertitles` and the branch `main`.

Crowdin reads `crowdin.yml` from the repo root; it is already there and holds no
credentials:

```yaml
"preserve_hierarchy": true
files:
  - source: /data/locales/templates/*.pot
    translation: /data/locales/%locale%/%file_name%.po
```

Crowdin pushes translations to a service branch named `l10n_main` and opens a pull
request from it. Merge that PR into `main`, then run:

```
python pipeline/tools/lint.py
```

### 4.6 Screenshots of the cards

`pipeline/tools/crowdin_screenshots.py` uploads each card's frame grab and tags it to
that card's string, so a translator sees the title card in the editor's context panel.

```
$env:CROWDIN_TOKEN = "<personal token: Account Settings -> API>"
$env:CROWDIN_PROJECT_ID = "<numeric project id>"

python pipeline/tools/crowdin_screenshots.py --list-strings sherlock-jr-1924
python pipeline/tools/crowdin_screenshots.py sherlock-jr-1924 --dry-run
python pipeline/tools/crowdin_screenshots.py sherlock-jr-1924
```

Run `--list-strings` first: it prints ten strings with their identifier, context, and
text so you can confirm that the card ids match the way Crowdin reports this project's
gettext strings. The tool reads `out/<slug>/extract/grabs/<id>.png`, falls back to
`out/<slug>/extract/<id>.jpg`, names each screenshot `<slug>/<id>.png`, and skips names
already in the project, so a second run costs only the read calls. It never prints the
token.

Whether screenshots are available on the free plan is unconfirmed. A 403 on
`POST /projects/{id}/screenshots` is the answer. The fallback: commit small JPEG frames
per card as `data/films/<slug>/frames/<id>.jpg` and change `export_po.py` to put the
frame's GitHub Pages URL in each string's translator comment, where every plan shows it.
That change is not written yet.

### 4.7 First sync

1. `python pipeline/tools/export_po.py` and commit the POTs under
   `data/locales/templates/`.
2. Push to `main` and wait for Crowdin to sync (or use **Sync now** in the integration).
3. Open the project and confirm the strings appear, with the card number, type, speaker,
   duration, and context in the note above each one.
4. Invite one translator you know, or join as a second account yourself.
5. Translate and approve one card.
6. Confirm the `l10n_main` pull request arrives on GitHub.
7. Merge it, pull, and run `python pipeline/tools/lint.py`. The translated card shows up
   in `data/locales/es-MX/<slug>.po`.

## 5. Announce

1. `site/index.html`: replace the placeholder link (`<a href="#">Crowdin project</a>
   (link coming when the strings are ready)`) with the project URL, and drop the
   parenthetical. Update the "last updated" line.
2. `README.md`: put the Crowdin project URL under the film table and change each film's
   **Status** cell to what is true then (transcribed, translation open, and so on).
3. Push. The Pages workflow republishes the site on the same commit.
