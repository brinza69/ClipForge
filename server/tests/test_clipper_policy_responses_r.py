"""Every clip dict the clip and reaction routers return carries the real
`effective_caption_policy` (B2 amendment C3).

Contract: codex-verdict-wave1.md C3. PATCH, regenerate, approve/reject, GET and
the reaction PUT/DELETE replace the clip object in the UI; a `null` (or worse, a
`default` computed without the project) there would overwrite what the board
said. The project answers True and the clip inherits, so the right value is the
project's suppress — distinguishable from both `null` and the default.
"""
from __future__ import annotations

import pytest

import test_clipper_project_caption_source as pcs
from test_clipper_mutation_atomicity import headline_barrier  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio

_PROJECT_TRUE = pcs._PROJECT_TRUE


async def _project(tmp_path):
    return await pcs._setup(tmp_path, True, clips={"c": {}})


def _clip(resp):
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body.get("clip", body)


@pytest.mark.parametrize("name", [
    "get", "patch", "patch_no_op", "approve", "approve_again", "reject",
    "regenerate_captions", "list"])
async def test_each_clip_endpoint_carries_the_policy(client, tmp_path, name):
    _pid, ids = await _project(tmp_path)
    cid = ids["c"]
    url = f"/api/clipper/clips/{cid}"
    if name == "get":
        resp = await client.get(url)
    elif name in ("patch", "patch_no_op"):
        resp = await client.patch(url, json={"title": "c" if name == "patch_no_op" else "new"})
    elif name in ("approve", "reject"):
        resp = await client.post(f"{url}/{name}")
    elif name == "approve_again":
        await client.post(f"{url}/approve")
        resp = await client.post(f"{url}/approve")
    elif name == "regenerate_captions":
        resp = await client.post(f"{url}/regenerate", json={"what": "captions"})
    else:
        resp = await client.get("/api/clipper/clips", params={"project_id": _pid})
        assert resp.status_code == 200, resp.text
        (row,) = resp.json()
        assert row["effective_caption_policy"] == _PROJECT_TRUE
        return
    assert _clip(resp)["effective_caption_policy"] == _PROJECT_TRUE, name


async def test_the_regenerated_headline_carries_the_policy(client, tmp_path, headline_barrier):
    headline_barrier["release"].set()
    _pid, ids = await _project(tmp_path)
    for _ in range(2):                                     # the change, then the no-op
        resp = await client.post(f"/api/clipper/clips/{ids['c']}/regenerate",
                                 json={"what": "headline"})
        assert _clip(resp)["effective_caption_policy"] == _PROJECT_TRUE


async def test_the_reaction_put_and_delete_carry_the_policy(client, tmp_path):
    _pid, ids = await _project(tmp_path)
    url = f"/api/clipper/clips/{ids['c']}/reaction-layout"
    body = pcs._reaction_body(tmp_path / "source.mp4", pcs._BAND)
    for resp in (await client.put(url, json=body),        # the change
                 await client.put(url, json=body),        # the same framing: no-op
                 await client.delete(url),                # the change
                 await client.delete(url)):               # nothing to delete: no-op
        assert _clip(resp)["effective_caption_policy"] == _PROJECT_TRUE
