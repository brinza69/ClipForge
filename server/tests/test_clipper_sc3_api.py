"""SC3 (codex-verdict-next-33 §3–§4): PUT/GET /api/clipper/clips/{id}/source-treatment, through the app.

Per clip, none or blur, strict types, the mask named by its hash and validated from the project's store,
`decided_by` written by the server, `source_has_burned_captions` never touched, a real change invalidating the
render in the same transaction, the same configuration again a no-op, and every refusal writing nothing.
"""
from __future__ import annotations

import pytest

from sc3_fixtures import events, make_clip, row
from services.clipper import source_treatment_store as store
from test_clipper_source_treatment_render import encode_source



@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return encode_source(tmp_path_factory.mktemp("sc3-api") / "plain.mp4")


def _url(cid: str) -> str:
    return f"/api/clipper/clips/{cid}/source-treatment"


def _blur(sha: str, layer=None) -> dict:
    return {"source_caption_treatment": {"treatment": "blur", "mask_sha256": sha}, "caption_layer": layer}


async def test_blur_is_stored_by_the_server_reloaded_and_invalidates_the_render(client, src):
    pid, cid, sha = await make_clip(src, export_path="/tmp/old.mp4", preview_path="/tmp/old_preview.mp4")
    r = await client.put(_url(cid), json=_blur(sha))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert body["clip"]["source_caption_treatment"] == {"treatment": "blur", "decided_by": "human",
                                                        "mask_sha256": sha}
    assert body["clip"]["export_path"] is None and body["clip"]["preview_path"] is None
    assert body["clip"]["source_has_burned_captions"] is True       # the source's truth, untouched
    assert body["effective"] == {"layer": "suppress", "layer_decided_by": "human", "refused": None,
                                 "treatment": "blur"}
    assert body["availability"]["blur"]["available"] is True
    assert body["availability"]["blur"]["mask_sha256"] == sha
    got = (await client.get(_url(cid))).json()                        # reload
    assert got["requested"] == {"source_caption_treatment": body["clip"]["source_caption_treatment"],
                                "caption_layer": None}
    assert [e.event_type for e in await events(cid)] == ["caption_changed"]


async def test_the_same_configuration_again_writes_nothing(client, src):
    pid, cid, sha = await make_clip(src)
    assert (await client.put(_url(cid), json=_blur(sha, "burn"))).status_code == 200
    from database import async_session
    from models import ClipModel

    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
        clip.export_path = "/tmp/after.mp4"
        await s.commit()
    r = await client.put(_url(cid), json=_blur(sha, "burn"))
    assert r.status_code == 200 and r.json()["changed"] is False
    assert (await row(cid)).export_path == "/tmp/after.mp4"          # not invalidated again
    assert len(await events(cid)) == 1


@pytest.mark.parametrize("layer, action", [(None, "suppress"), ("suppress", "suppress"), ("burn", "burn")])
async def test_each_layer_choice_is_what_the_render_will_do(client, src, layer, action):
    _pid, cid, sha = await make_clip(src)
    r = await client.put(_url(cid), json=_blur(sha, layer))
    assert r.status_code == 200, r.text
    assert r.json()["effective"]["layer"] == action
    assert r.json()["clip"]["caption_layer"] == layer


async def test_an_explicit_none_differs_from_null_in_provenance_only(client, src):
    _pid, cid, _sha = await make_clip(src)
    r = await client.put(_url(cid), json={"source_caption_treatment": {"treatment": "none"},
                                          "caption_layer": None})
    assert r.status_code == 200, r.text
    assert r.json()["clip"]["source_caption_treatment"] == {"treatment": "none", "decided_by": "human"}
    assert r.json()["effective"]["treatment"] == "none"
    r = await client.put(_url(cid), json={"source_caption_treatment": None, "caption_layer": None})
    assert r.json()["clip"]["source_caption_treatment"] is None and r.json()["effective"]["treatment"] == "none"


BAD = [
    ({"source_caption_treatment": {"treatment": "erase", "mask_sha256": "0" * 64}, "caption_layer": None},
     "treatment_not_offered"),
    ({"source_caption_treatment": None, "caption_layer": None, "extra": 1}, "invalid_body"),
    ({"source_caption_treatment": None}, "invalid_body"),
    ({"source_caption_treatment": None, "caption_layer": "BURN"}, "invalid_value"),
    ({"source_caption_treatment": None, "caption_layer": True}, "invalid_value"),
    ({"source_caption_treatment": {"treatment": "blur"}, "caption_layer": None}, "invalid_value"),
    ({"source_caption_treatment": {"treatment": "none", "mask_sha256": "0" * 64}, "caption_layer": None},
     "invalid_value"),
    ({"source_caption_treatment": {"treatment": "blur", "mask_sha256": "0" * 64}, "caption_layer": None},
     "blur_unavailable"),
    ({"source_caption_treatment": None, "caption_layer": "burn"}, "burn_over_untreated_source_captions"),
    ({"source_caption_treatment": {"treatment": "none"}, "caption_layer": "burn"},
     "burn_over_untreated_source_captions"),
]


@pytest.mark.parametrize("payload, code", BAD, ids=[c for _, c in BAD])
async def test_a_refused_request_writes_nothing(client, src, payload, code):
    _pid, cid, _sha = await make_clip(src, export_path="/tmp/keep.mp4")
    r = await client.put(_url(cid), json=payload)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == code
    clip = await row(cid)
    assert (clip.source_caption_treatment, clip.caption_layer, clip.export_path) == (None, None, "/tmp/keep.mp4")
    assert await events(cid) == []


async def test_a_burn_over_a_source_nobody_answered_needs_the_blur_too(client, src):
    _pid, cid, sha = await make_clip(src, burned=None)
    r = await client.put(_url(cid), json={"source_caption_treatment": None, "caption_layer": "burn"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "burn_over_untreated_source_captions"
    assert (await client.put(_url(cid), json=_blur(sha, "burn"))).status_code == 200
    _pid, cid2, _ = await make_clip(src, burned=False)            # a source confirmed WITHOUT text
    assert (await client.put(_url(cid2), json={"source_caption_treatment": None,
                                               "caption_layer": "burn"})).status_code == 200


async def test_a_changed_glyph_or_a_window_the_mask_does_not_cover_refuses_blur(client, src):
    pid, cid, sha = await make_clip(src)
    glyph = next((store.mask_dir(pid, sha) / "g").glob("*.png"))
    glyph.write_bytes(glyph.read_bytes() + b"x")
    r = await client.put(_url(cid), json=_blur(sha))
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "glyph_hash_mismatch"
    _pid, cid2, sha2 = await make_clip(src, start=0.0)             # before the mask's first frame
    r = await client.put(_url(cid2), json=_blur(sha2))
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "mask_window_does_not_cover_clip"
    assert (await row(cid2)).source_caption_treatment is None


async def test_an_export_in_progress_is_409_and_nothing_changes(client, src):
    _pid, cid, sha = await make_clip(src, status="exporting")
    r = await client.put(_url(cid), json=_blur(sha))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "export_in_progress"
    assert (await row(cid)).source_caption_treatment is None


async def test_availability_names_why_blur_cannot_be_chosen(client, src):
    _pid, cid, _ = await make_clip(src, with_mask=False)
    got = (await client.get(_url(cid))).json()
    assert got["availability"]["blur"] == {"available": False, "mask_sha256": None,
                                           "reason": "no_validated_mask", "masks": []}


async def test_an_erase_hidden_in_a_stored_override_is_refused_not_rendered(client, src):
    _pid, cid, sha = await make_clip(src)
    from database import async_session
    from models import ClipModel

    async with async_session() as s:                              # written behind the API's back
        clip = await s.get(ClipModel, cid)
        clip.source_caption_treatment = {"treatment": "blur", "decided_by": "human", "mask_sha256": sha,
                                         "overrides": [{"k_from": 20, "k_to": 35, "treatment": "erase"}]}
        await s.commit()
    got = (await client.get(_url(cid))).json()
    assert got["effective"]["treatment"] is None
    assert got["effective"]["refused"]["reason"] == "setting_invalid"


async def _set(cid: str, pid: str, *, clip=None, project=None):
    from database import async_session
    from models import ClipModel, ProjectModel

    async with async_session() as s:
        if clip:
            c = await s.get(ClipModel, cid)
            for k, v in clip.items():
                setattr(c, k, v)
        if project:
            p = await s.get(ProjectModel, pid)
            for k, v in project.items():
                setattr(p, k, v)
        await s.commit()


@pytest.mark.parametrize("where", ["dynamic_edit_off", "reaction_framing"])
async def test_a_clip_that_renders_on_the_static_path_is_refused_blur_with_the_reason(client, src, where):
    """next-34 R3: known BEFORE the render — the executor runs on the multi-shot path only."""
    pid, cid, sha = await make_clip(src)
    if where == "dynamic_edit_off":
        await _set(cid, pid, project={"clipper_settings": {"fps": 30, "dynamic_edit": False}})
    else:
        await _set(cid, pid, clip={"layout_plan": {"game_content_fit": True}})
    got = (await client.get(_url(cid))).json()["availability"]["blur"]
    assert got["available"] is False and got["reason"] == "static_path_unsupported"
    r = await client.put(_url(cid), json=_blur(sha))
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "static_path_unsupported"
    assert (await row(cid)).source_caption_treatment is None
