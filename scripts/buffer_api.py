"""Buffer GraphQL client shared by the posting scripts.

The personal API key lives in `data/buffer_config.json` (`data/` is gitignored)
— never print it. Everything is one POST to https://api.buffer.com; the query
shapes that actually validate are documented in `docs/runbook-cont-nou.md` §8.

Run it directly to list the connected channels:

    server\\.venv\\Scripts\\python.exe scripts\\buffer_api.py
"""
import json
import os
import pathlib
import urllib.error
import urllib.request

ENDPOINT = "https://api.buffer.com"
_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _config_path() -> pathlib.Path:
    data = os.environ.get("CLIPFORGE_DATA_DIR")
    base = pathlib.Path(data) if data else _ROOT / "data"
    if not base.is_absolute():
        base = _ROOT / base
    return base / "buffer_config.json"


def key() -> str:
    p = _config_path()
    if not p.exists():
        raise SystemExit(f"lipseste {p} — pune acolo {{\"api_key\": \"...\"}} "
                         f"(Buffer → Settings → API → Personal Keys)")
    return json.loads(p.read_text(encoding="utf-8"))["api_key"]


def gql(query: str, variables: dict | None = None, timeout: int = 60) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key()}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # The key is in the request, never in the body — safe to surface.
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    if out.get("errors"):
        raise RuntimeError("GraphQL: " + json.dumps(out["errors"])[:500])
    return out.get("data") or {}


def default_org() -> str:
    """The organisation id every other query needs. Read, not hardcoded, so
    this keeps working on a freshly created Buffer account."""
    orgs = (gql("query { account { organizations { id name } } }")
            .get("account") or {}).get("organizations") or []
    if not orgs:
        raise SystemExit("contul Buffer nu are nicio organizatie")
    if len(orgs) > 1:
        raise SystemExit(f"mai multe organizatii: {[(o['id'], o['name']) for o in orgs]}")
    return orgs[0]["id"]


# `channels` REQUIRES the org id, and the field is `type` — there is no
# `serviceType` (that guess is what broke the previous copy of this file).
CHANNELS_Q = """
query C($i: ChannelsInput!) {
  channels(input: $i) {
    id name service type timezone isDisconnected isLocked isQueuePaused externalLink
  }
}
"""


def channels(org: str | None = None) -> list[dict]:
    return gql(CHANNELS_Q, {"i": {"organizationId": org or default_org()}})["channels"]


def channel_by_name(name: str, org: str | None = None) -> dict:
    chans = channels(org)
    hit = [c for c in chans if c["name"] == name]
    if not hit:
        raise SystemExit(f"canalul {name} nu e conectat in Buffer. "
                         f"Conectate: {[c['name'] for c in chans]}")
    return hit[0]


if __name__ == "__main__":
    org = default_org()
    chans = channels(org)
    print(f"organizatie {org} — {len(chans)} canale conectate\n")
    for c in chans:
        flags = [k for k in ("isDisconnected", "isLocked", "isQueuePaused") if c.get(k)]
        print(f"  {c['name']}  [{c['service']}/{c['type']}]")
        print(f"     id={c['id']}  tz={c.get('timezone')}  {c.get('externalLink') or ''}")
        if flags:
            print(f"     ATENTIE: {', '.join(flags)}")
