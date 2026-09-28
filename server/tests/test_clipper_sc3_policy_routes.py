"""SC3 (codex-verdict-next-34 R4): the older policy routes respect a clip's own layer choice.

The project's answer moves only the inheriting clips whose EFFECTIVE action flips (a chosen layer never
does), 409 only for those, and refuses — nothing written — when it would put a chosen burn without a blur
over the source's text. The clip's own answer does the same for its clip and invalidates only when the
render would change. A reaction framing (static path) is refused on a clip that blurs the source.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from sc3_fixtures import make_clip, row
from test_clipper_reaction_editor import _valid_put_body, reaction_db, small_source  # noqa: F401
from test_clipper_source_treatment_render import encode_source


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return encode_source(tmp_path_factory.mktemp("sc3-routes") / "plain.mp4")


async def _answer(pid: str, old, new):
    from database import async_session
    from models import ProjectModel
    from routers.clipper_caption_source import apply_project_answer

    async with async_session() as s:
        project = await s.get(ProjectModel, pid)
        out = await apply_project_answer(s, project, old, new)
        await s.commit()
    return out


async def test_an_explicit_suppress_is_not_moved_by_the_projects_answer(src):
    pid, cid, _ = await make_clip(src, burned=None, caption_layer="suppress", export_path="previous.mp4")
    out = await _answer(pid, False, True)
    assert out["invalidated_clip_ids"] == [] and out["affected_clip_ids"] == []
    assert (await row(cid)).export_path == "previous.mp4"


async def test_a_legacy_clip_still_follows_the_projects_answer(src):
    pid, cid, _ = await make_clip(src, burned=None, export_path="previous.mp4")
    out = await _answer(pid, False, True)
    assert out["invalidated_clip_ids"] == [cid]
    assert (await row(cid)).export_path is None


async def test_an_explicit_burn_over_a_blur_is_not_moved_either(src):
    pid, cid, sha = await make_clip(src, burned=None, caption_layer="burn", export_path="previous.mp4")
    from database import async_session
    from models import ClipModel

    async with async_session() as s:
        (await s.get(ClipModel, cid)).source_caption_treatment = {
            "treatment": "blur", "decided_by": "human", "mask_sha256": sha}
        await s.commit()
    out = await _answer(pid, False, True)
    assert out["invalidated_clip_ids"] == [] and (await row(cid)).export_path == "previous.mp4"


@pytest.mark.parametrize("new", [True, None])
async def test_an_answer_that_would_put_a_chosen_burn_over_text_is_refused(src, new):
    pid, cid, _ = await make_clip(src, burned=None, caption_layer="burn", export_path="previous.mp4")
    with pytest.raises(HTTPException) as e:
        await _answer(pid, False, new)
    assert e.value.status_code == 422 and e.value.detail["error"] == "burn_over_untreated_source_captions"
    assert e.value.detail["blocking_clips"][0]["clip_id"] == cid
    assert (await row(cid)).export_path == "previous.mp4"


async def test_only_an_export_the_answer_really_moves_is_409(src):
    pid, _cid, _ = await make_clip(src, burned=None, caption_layer="suppress", status="exporting")
    out = await _answer(pid, False, True)                  # the exporting clip keeps its chosen layer
    assert out["affected_clip_ids"] == []
    pid2, cid2, _ = await make_clip(src, burned=None, status="exporting")
    with pytest.raises(HTTPException) as e:
        await _answer(pid2, False, True)
    assert e.value.status_code == 409 and e.value.detail["clip_ids"] == [cid2]


async def test_the_clips_own_answer_respects_its_layer(client, src):
    _pid, cid, _ = await make_clip(src, burned=False, caption_layer="burn", export_path="previous.mp4")
    r = await client.put(f"/api/clipper/clips/{cid}/caption-source", json={"source_has_burned_captions": True})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "burn_over_untreated_source_captions"
    assert (await row(cid)).source_has_burned_captions is False
    _pid, cid2, _ = await make_clip(src, burned=False, caption_layer="suppress", export_path="previous.mp4")
    r = await client.put(f"/api/clipper/clips/{cid2}/caption-source", json={"source_has_burned_captions": True})
    assert r.status_code == 200, r.text
    clip = await row(cid2)
    assert clip.source_has_burned_captions is True and clip.export_path == "previous.mp4"  # same render


async def test_a_reaction_framing_is_refused_on_a_clip_that_blurs_the_source(client, reaction_db):  # noqa: F811
    from database import async_session
    from models import ClipModel

    cid, src = reaction_db["cid"], reaction_db["src"]
    async with async_session() as s:
        (await s.get(ClipModel, cid)).source_caption_treatment = {
            "treatment": "blur", "decided_by": "human", "mask_sha256": "0" * 64}
        await s.commit()
    r = await client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=_valid_put_body(src))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "static_path_unsupported"
    assert not ((await row(cid)).layout_plan or {}).get("game_content_fit")
