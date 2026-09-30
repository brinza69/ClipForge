"""Download a public Twitch VOD's chat replay via the public GQL endpoint.

Usage: python fetch_chat.py VIDEO_ID DURATION_S chat.json

Writes chat-downloader-shaped JSON: [{"time_in_seconds", "message",
"message_type": "text_message"}]. Commenter names are NOT kept: the analysis
never reads them. Cursor pagination first; if a cursor page comes back without
data, falls back to offset pagination.

`gql.twitch.tv` is Twitch's own web endpoint, not a documented API; the
Client-ID below is the public one its web client sends. CA4/CA7 in production
would go through Helix with app credentials instead.
"""
import json
import sys
import time
import urllib.request

GQL = "https://gql.twitch.tv/gql"
CLIENT_ID = "kd1unb4b3q4t58fwlpcbzcbnm76a8fp"
HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


def ask(video_id: str, *, cursor=None, offset=None):
    variables = {"videoID": video_id}
    if cursor:
        variables["cursor"] = cursor
    else:
        variables["contentOffsetSeconds"] = int(offset or 0)
    body = json.dumps([{"operationName": "VideoCommentsByOffsetOrCursor",
                        "variables": variables,
                        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": HASH}}}])
    req = urllib.request.Request(GQL, data=body.encode(), method="POST",
                                 headers={"Client-ID": CLIENT_ID,
                                          "Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read())
            return out[0]
        except Exception as exc:  # network hiccup: back off and retry
            time.sleep(1.5 * (attempt + 1))
            last = exc
    raise RuntimeError(f"gql failed: {last}")


def main(video_id: str, duration: float, out_path: str) -> int:
    seen = set()
    msgs = []
    cursor = None
    offset = 0.0
    pages = 0
    mode = "cursor"
    while True:
        res = ask(video_id, cursor=cursor if mode == "cursor" else None, offset=offset)
        comments = (((res.get("data") or {}).get("video") or {}).get("comments")) if res else None
        if not comments:
            if mode == "cursor" and cursor:
                mode = "offset"  # cursor refused: continue by offset
                cursor = None
                continue
            break
        edges = comments.get("edges") or []
        pages += 1
        new = 0
        for e in edges:
            node = e.get("node") or {}
            nid = node.get("id")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            frags = ((node.get("message") or {}).get("fragments")) or []
            text = "".join(f.get("text") or "" for f in frags)
            msgs.append({"time_in_seconds": node.get("contentOffsetSeconds"),
                         "message": text,
                         "message_type": "text_message"})
            new += 1
        last_t = msgs[-1]["time_in_seconds"] if msgs else offset
        info = comments.get("pageInfo") or {}
        if pages % 50 == 0:
            print(f"pages {pages} msgs {len(msgs)} t={last_t}s mode={mode}", flush=True)
        if mode == "cursor":
            if not info.get("hasNextPage") or not edges:
                break
            cursor = edges[-1].get("cursor")
        else:
            if new == 0:
                offset = float(last_t) + 1.0
            else:
                offset = float(last_t)
            if offset > duration:
                break
    msgs.sort(key=lambda m: m["time_in_seconds"] or 0)
    json.dump(msgs, open(out_path, "w", encoding="utf-8"))
    print(f"done: {len(msgs)} messages, {pages} pages, last t={msgs[-1]['time_in_seconds'] if msgs else None}")
    return 0 if msgs else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], float(sys.argv[2]), sys.argv[3]))
