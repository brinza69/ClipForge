"""Repara postarile programate care folosesc forma veche de URL Google Drive.

`https://drive.google.com/uc?export=download&id=…` serveste pagina HTML de
scanare antivirus pentru fisiere mari, deci Buffer primeste text/html in loc de
video si postarea esueaza la publicare. Forma buna, verificata pana la 115 MB:

    https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=t

Id-ul de fisier se ia din URL-ul existent al postarii, deci nu trebuie potrivit
nimic dupa text.

ATENTIE: editPost INLOCUIESTE postarea. Daca nu retrimiti `assets`, videoul
dispare. Se retrimit si `dueAt`/`mode`, altfel editarea ar muta postarea.

    python scripts/fix_drive_urls.py --channel franceza [--write]
"""
import re
import sys

import targets
from buffer_api import channel_by_name, default_org, gql

CHANNELS = {
    "franceza": "tiktok_channel_fr",
    "tiktok": "tiktok_channel_ro",
    "facebook": "facebook_channel",
}
VECHI = re.compile(r"https?://drive\.google\.com/uc\?export=download&id=([A-Za-z0-9_-]+)")
BUN = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"

LIST = ("query P($i: PostsInput!, $f: Int) { posts(input: $i, first: $f) "
        "{ edges { node { id status dueAt text assets { ... on VideoAsset "
        "{ source } } } } } }")
EDIT = """
mutation E($i: EditPostInput!) {
  editPost(input: $i) {
    ... on PostActionSuccess { post { id } }
    ... on MutationError { message }
  }
}
"""


def main():
    write = "--write" in sys.argv
    which = sys.argv[sys.argv.index("--channel") + 1] if "--channel" in sys.argv else "franceza"
    if which not in CHANNELS:
        raise SystemExit(f"--channel: {', '.join(CHANNELS)}")

    org = default_org()
    ch = channel_by_name(targets.get(CHANNELS[which]), org)
    r = gql(LIST, {"i": {"organizationId": org,
                         "filter": {"channelIds": [ch["id"]], "status": ["scheduled"]}},
                   "f": 100})
    posts = sorted([e["node"] for e in r["posts"]["edges"]], key=lambda x: x.get("dueAt") or "")
    print(f"canal: {ch['name']}   programate: {len(posts)}")

    de_reparat = []
    for p in posts:
        src = next((a.get("source") for a in (p.get("assets") or []) if a.get("source")), "")
        m = VECHI.search(src or "")
        if m:
            de_reparat.append((p, BUN.format(m.group(1))))

    print(f"cu URL vechi (risc de esec la publicare): {len(de_reparat)}\n")
    for p, nou in de_reparat:
        print(f"  {(p.get('dueAt') or '')[:16]}  {(p.get('text') or '')[:44]}")
        if not write:
            continue
        res = gql(EDIT, {"i": {"id": p["id"], "text": p.get("text") or "",
                               "schedulingType": "automatic", "mode": "customScheduled",
                               "dueAt": p.get("dueAt"),
                               "assets": [{"video": {"url": nou}}]}}).get("editPost") or {}
        print("     ESUAT: " + res["message"][:110] if res.get("message") else "     reparat")

    if not de_reparat:
        print("nimic de reparat")
    elif not write:
        print("\n(fara --write) nimic modificat")


if __name__ == "__main__":
    main()
