"""Create and configure the Crowdin project through API v2, so the maintainer never has to
click through the settings pages. Idempotent: rerun after any change to see or fix the state.

    python pipeline/tools/crowdin_setup.py --show                  # print the project's settings (needs CROWDIN_PROJECT_ID)
    python pipeline/tools/crowdin_setup.py --create                # create the project; prints the id to keep
    python pipeline/tools/crowdin_setup.py --configure             # apply the settings below to an existing project
    python pipeline/tools/crowdin_setup.py --upload-sources        # push data/locales/templates/*.pot as source files
    python pipeline/tools/crowdin_setup.py --delete-sources        # remove those files again

Settings applied (docs/design.md § Crowdsourcing, docs/go-public.md § Crowdin):
  source language en, target es-MX (Mexican Spanish, never Castilian)
  visibility open (anyone can find the project), languageAccessPolicy open (join without an invitation)
  exportApprovedOnly true (only proofread translations come back), skipUntranslatedStrings false
  description: the short project blurb plus a link to docs/translating.md

Token: CROWDIN_TOKEN in the environment or the one-line file ~/.crowdin_token (see
crowdin_screenshots.resolve_token). Project id: --project, CROWDIN_PROJECT_ID, or project_id in crowdin.yml.

--upload-sources is a bootstrap for testing before the GitHub integration is connected. The
integration manages the same files itself; run --delete-sources before connecting it so
the strings are not duplicated.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import crowdin_screenshots as cs  # noqa: E402

PROJECT_NAME = "Intertitles"
PROJECT_IDENTIFIER = "intertitles"
SOURCE_LANG = "en"
TARGET_LANGS = ["es-MX"]
DESCRIPTION = (
    "Bilingual title cards for public-domain silent films. Each string is one intertitle card; "
    "translations must fit the same card for the same seconds, so keep them as short as the "
    "English or shorter and keep the line breaks. Guidelines: "
    "https://github.com/MildKid/intertitles/blob/main/docs/translating.md . "
    "Translations are contributed under CC BY 4.0 (proposed; see docs/go-public.md)."
)
SETTINGS = {
    "visibility": "open",
    "languageAccessPolicy": "open",
    "exportApprovedOnly": True,
    "skipUntranslatedStrings": False,
    "description": DESCRIPTION,
}
SHOW_KEYS = ("id", "name", "identifier", "sourceLanguageId", "targetLanguageIds", "visibility",
             "languageAccessPolicy", "exportApprovedOnly", "skipUntranslatedStrings", "publicDownloads",
             "description")


def show(project: str) -> int:
    data = cs.api("GET", f"/projects/{project}")["data"]
    for k in SHOW_KEYS:
        if k in data:
            v = data[k]
            print(f"  {k}: {json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v[:120]}")
    missing = [k for k in ("visibility", "languageAccessPolicy", "exportApprovedOnly") if k not in data]
    if missing:
        print(f"  (fields not present in this account's project object: {', '.join(missing)}; "
              "check them in the Crowdin UI)")
    files = list(cs.paginate(f"/projects/{project}/files"))
    print(f"  files: {len(files)}" + "".join(f"\n    {f['id']}  {f['name']}" for f in files))
    return 0


def create() -> int:
    body = {
        "name": PROJECT_NAME,
        "identifier": PROJECT_IDENTIFIER,
        "sourceLanguageId": SOURCE_LANG,
        "targetLanguageIds": TARGET_LANGS,
        **SETTINGS,
    }
    data = cs.api("POST", "/projects", json_body=body)["data"]
    pid = data["id"]
    print(f"created project {pid}: {data.get('name')}  ->  set CROWDIN_PROJECT_ID={pid}")
    return show(str(pid))


def configure(project: str) -> int:
    current = cs.api("GET", f"/projects/{project}")["data"]
    ops = []
    for k, v in SETTINGS.items():
        if current.get(k) != v:
            ops.append({"op": "replace", "path": f"/{k}", "value": v})
    if set(current.get("targetLanguageIds") or []) != set(TARGET_LANGS):
        ops.append({"op": "replace", "path": "/targetLanguageIds", "value": TARGET_LANGS})
    if not ops:
        print("nothing to change")
        return show(project)
    print("applying: " + ", ".join(o["path"] for o in ops))
    cs.api("PATCH", f"/projects/{project}", json_body=ops)
    return show(project)


def upload_sources(project: str) -> int:
    existing = {f["name"]: f["id"] for f in cs.paginate(f"/projects/{project}/files")}
    for slug in common.list_films():
        pot = common.pot_path(slug)
        if not pot.exists():
            print(f"  {slug}: no POT (run export_po.py); skipped")
            continue
        name = pot.name
        storage = cs.api("POST", "/storages", raw=pot.read_bytes(), filename=name)["data"]["id"]
        if name in existing:
            cs.api("PUT", f"/projects/{project}/files/{existing[name]}", json_body={"storageId": storage})
            print(f"  {name}: updated (file {existing[name]})")
        else:
            data = cs.api("POST", f"/projects/{project}/files",
                          json_body={"storageId": storage, "name": name, "type": "gettext"})["data"]
            print(f"  {name}: added (file {data['id']})")
    return 0


def delete_sources(project: str) -> int:
    pots = {common.pot_path(s).name for s in common.list_films()}
    n = 0
    for f in cs.paginate(f"/projects/{project}/files"):
        if f["name"] in pots:
            cs.api("DELETE", f"/projects/{project}/files/{f['id']}")
            print(f"  deleted {f['name']}")
            n += 1
    print(f"{n} files deleted")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Create and configure the Crowdin project via API v2.")
    ap.add_argument("--project", default="", help="Crowdin project id (else CROWDIN_PROJECT_ID)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true")
    g.add_argument("--create", action="store_true")
    g.add_argument("--configure", action="store_true")
    g.add_argument("--upload-sources", action="store_true")
    g.add_argument("--delete-sources", action="store_true")
    a = ap.parse_args(argv)

    cs._TOKEN = cs.resolve_token()
    if not cs._TOKEN:
        print(cs.NO_TOKEN_MSG, file=sys.stderr)
        return 2
    project = (a.project or os.environ.get("CROWDIN_PROJECT_ID", "") or cs.project_id_from_yml()).strip()
    if not a.create and not project:
        print("No Crowdin project id. Pass --project <id> or set CROWDIN_PROJECT_ID "
              "(printed by --create; also in the project URL).", file=sys.stderr)
        return 2
    cs._PROJECT = project
    try:
        if a.create:
            return create()
        if a.show:
            return show(project)
        if a.configure:
            return configure(project)
        if a.upload_sources:
            return upload_sources(project)
        if a.delete_sources:
            return delete_sources(project)
    except cs.ApiError as e:
        print(f"failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
