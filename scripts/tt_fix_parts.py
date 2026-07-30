"""Repair tool: give each part of a split video its own caption.

`tt_post_povestitor.py` now writes `(1/3)`-style markers when it creates the
post, so this is only needed for posts queued the old way (or added by hand). It
looks at what is CURRENTLY scheduled, groups by identical caption, and numbers
each group — so it can only fix parts that sit in the queue at the same time.

  --dry     show what it would change, touch nothing
"""
import sys
from collections import defaultdict

import targets
from buffer_api import channel_by_name, default_org, gql

CHANNEL_NAME = targets.get("tiktok_channel_ro")

POSTS = ("query P($i: PostsInput!, $f: Int) { posts(input: $i, first: $f) "
         "{ edges { node { id status dueAt text } } } }")
ONE = ("query P($i: PostInput!) { post(input: $i) { id text "
       "assets { ... on VideoAsset { source } } } }")
EDIT = """
mutation E($i: EditPostInput!) {
  editPost(input: $i) {
    ... on PostActionSuccess { post { id text } }
    ... on MutationError { message }
  }
}
"""


def main():
    dry = "--dry" in sys.argv
    org = default_org()
    ch = channel_by_name(CHANNEL_NAME, org)

    r = gql(POSTS, {"i": {"organizationId": org,
                          "filter": {"channelIds": [ch["id"]],
                                     "status": ["scheduled"]}}, "f": 100})
    posts = sorted([e["node"] for e in r["posts"]["edges"]],
                   key=lambda x: x.get("dueAt") or "")

    groups = defaultdict(list)
    for p in posts:
        groups[(p.get("text") or "").strip()].append(p)
    todo = {t: pl for t, pl in groups.items() if len(pl) > 1 and t}

    print(f"canal: {ch['name']}   in coada: {len(posts)}   "
          f"grupuri cu acelasi caption: {len(todo)}\n")
    if not todo:
        print("nimic de corectat")
        return

    for text, pl in todo.items():
        n = len(pl)
        print(f"{n} postari cu: {text[:66]}")
        for k, p in enumerate(pl, start=1):
            print(f"   {(p.get('dueAt') or '')[:16]} -> ...({k}/{n})")
            if dry:
                continue
            # editPost REPLACES the post: omit assets and the video is dropped
            # ("TikTok posts require at least one image or video"). Read the
            # existing asset back and pass it through, with dueAt/mode so
            # editing the caption cannot move the post.
            cur = (gql(ONE, {"i": {"id": p["id"]}}) or {}).get("post") or {}
            src = next((a.get("source") for a in (cur.get("assets") or [])
                        if a.get("source")), None)
            if not src:
                print("      SARIT: nu gasesc videoul postarii")
                continue
            res = gql(EDIT, {"i": {"id": p["id"], "text": f"{text} ({k}/{n})",
                                   "schedulingType": "automatic",
                                   "mode": "customScheduled",
                                   "dueAt": p.get("dueAt"),
                                   "assets": [{"video": {"url": src}}]}}).get("editPost") or {}
            if res.get("message"):
                print(f"      ESUAT: {res['message'][:120]}")
        print()

    print("(--dry) nimic modificat" if dry else "gata")


if __name__ == "__main__":
    main()
