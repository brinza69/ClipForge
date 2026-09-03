"""S8a: a review answer stays bound to the bytes the session presented.

Media stubs are intentionally not decodable video: this contract binds files,
not picture quality, which requires the separate neutral-render/human gates.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import update

from database import async_session
from models import ClipFeedbackModel, ClipModel, ProjectModel
from services.clipper import blind_review, review_media


def _media(tmp_path, *, clip_id="clip123", project_id="project123", run="run123",
           version="old-render"):
    path = tmp_path / f"{clip_id}.mp4"
    path.write_bytes(b"x" * 2048)
    sidecar = {"clip_id": clip_id, "project_id": project_id,
               "source_start": 1.0, "source_end": 6.0, "duration": 5.0,
               "selection_run_id": run, "render_version": version, "transcript": "old words"}
    path.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")
    return {"clip_id": clip_id, "project_id": project_id,
            "export_path": str(path), "start_time": 1.0, "end_time": 6.0,
            "duration": 5.0, "selection_run_id": run, "transcript_text": "old words"}


def test_capture_uses_the_observed_sidecar_not_the_installed_renderer(tmp_path):
    row = _media(tmp_path)
    snapshot = review_media.capture(row)
    assert snapshot["render_version"] == "old-render"
    assert snapshot["selection_run_id"] == "run123"
    assert snapshot["size_bytes"] == 2048
    assert review_media.verify(snapshot) == tmp_path / "clip123.mp4"


def test_historical_absence_is_not_backfilled(tmp_path):
    row = _media(tmp_path, run=None, version=None)
    snapshot = review_media.capture(row)
    assert snapshot["render_version"] is None
    assert snapshot["selection_run_id"] is None


def test_hash_covers_the_tail_not_just_a_prefix_or_length(tmp_path):
    row = _media(tmp_path)
    path = tmp_path / "clip123.mp4"
    path.write_bytes(b"a" * (8 * 1024 * 1024) + b"old-tail")
    snapshot = review_media.capture(row)
    with path.open("r+b") as stream:
        stream.seek(-8, 2)
        stream.write(b"new-tail")
    assert path.stat().st_size == snapshot["size_bytes"]
    with pytest.raises(review_media.MediaChanged, match="export_changed"):
        review_media.verify(snapshot)


@pytest.mark.parametrize("key,value", [
    ("clip_id", "another"), ("project_id", "another"),
    ("source_start", 2.0), ("source_end", 8.0), ("duration", 4.0),
    ("source_start", float("nan")), ("selection_run_id", "another"),
    ("render_version", 7),
])
def test_a_sidecar_that_does_not_describe_the_row_is_refused(tmp_path, key, value):
    row = _media(tmp_path)
    sidecar = tmp_path / "clip123.json"
    data = json.loads(sidecar.read_text())
    data[key] = value
    sidecar.write_text(json.dumps(data))
    with pytest.raises(review_media.MediaChanged):
        review_media.capture(row)


def test_changed_provenance_is_not_silently_attached_to_the_same_video(tmp_path):
    snapshot = review_media.capture(_media(tmp_path))
    sidecar = tmp_path / "clip123.json"
    sidecar.write_text(sidecar.read_text() + " ")
    with pytest.raises(review_media.MediaChanged, match="sidecar_changed"):
        review_media.verify(snapshot)


@pytest.mark.parametrize("key,value", [("render_version", "new-render"),
                                      ("selection_run_id", "different")])
def test_a_changed_manifest_label_cannot_keep_claiming_the_original_sidecar(tmp_path, key, value):
    snapshot = review_media.capture(_media(tmp_path))
    snapshot[key] = value
    with pytest.raises(review_media.MediaChanged, match="declarations_changed"):
        review_media.verify(snapshot)


def test_capture_uses_the_exports_transcript_not_later_db_edits(tmp_path):
    row = _media(tmp_path)
    row["transcript_text"] = "edited after the encode"
    assert review_media.capture(row)["presentation"]["transcript_text"] == "old words"


def test_missing_export_transcript_does_not_fall_back_to_current_db_text(tmp_path):
    row = _media(tmp_path)
    path = tmp_path / "clip123.json"
    sidecar = json.loads(path.read_text())
    sidecar.pop("transcript")
    path.write_text(json.dumps(sidecar))
    assert review_media.capture(row)["presentation"]["transcript_text"] == ""


@pytest.mark.parametrize("key,value", [("start_time", 2.0), ("transcript_text", "changed")])
def test_corrupt_snapshot_presentation_is_not_masked_by_valid_media_hashes(tmp_path, key, value):
    snapshot = review_media.capture(_media(tmp_path))
    snapshot["presentation"][key] = value
    with pytest.raises(review_media.MediaChanged, match="declarations_changed"):
        review_media.verify(snapshot)


@pytest.fixture
def review_root(tmp_path, monkeypatch):
    from routers import clipper_review

    root = tmp_path / "sessions"
    root.mkdir()
    monkeypatch.setattr(clipper_review, "_root", lambda: root)
    return root


async def _seed(tmp_path, *, project_id=None, run="run123", version="old-render"):
    project_id = project_id or uuid.uuid4().hex[:12]
    clip_id = uuid.uuid4().hex[:12]
    row = _media(tmp_path, clip_id=clip_id, project_id=project_id, run=run, version=version)
    async with async_session() as session:
        if await session.get(ProjectModel, project_id) is None:
            session.add(ProjectModel(id=project_id, title="review media", source_kind="file"))
        session.add(ClipModel(
            id=clip_id, project_id=project_id, start_time=1.0, end_time=6.0, duration=5.0,
            transcript_text="old words", export_path=row["export_path"],
            selection_run_id=run, shadow_run_id=run, shadow_rank=1, rank_position=1))
        await session.commit()
    return row


async def _start(client, project_ids):
    from routers import clipper_review

    response = await client.post("/api/clipper/review", json={"project_ids": project_ids})
    assert response.status_code == 200, response.text
    sid = response.json()["session_id"]
    return sid, clipper_review._load(sid)


def _answer(handle):
    return {"review_item_id": handle, "worth_exporting": "yes",
            "self_contained": "yes", "hook": "strong", "start_boundary": "correct",
            "end_boundary": "correct", "technical_problem": "no"}


async def test_api_creation_binds_actual_media_and_keeps_membership_private(
        client, review_root, tmp_path):
    row = await _seed(tmp_path)
    sid, state = await _start(client, [row["project_id"]])
    assert state["render_version"] == "old-render"
    assert state["media_snapshot_complete"] is True
    assert state["selection_identity_complete"] is True
    handle = state["order"][0]
    nxt = await client.get(f"/api/clipper/review/{sid}/next")
    assert nxt.status_code == 200
    assert nxt.json()["item"]["transcript"] == "old words"
    for secret in (row["clip_id"], row["project_id"], "run123", "old-render", "sha256"):
        assert secret not in nxt.text
    video = await client.get(f"/api/clipper/review/{sid}/item/{handle}/video")
    assert video.content == b"x" * 2048
    assert row["clip_id"] not in video.headers["content-disposition"]


async def test_a_missing_requested_project_cannot_shrink_the_session(
        client, review_root, tmp_path):
    row = await _seed(tmp_path)
    response = await client.post("/api/clipper/review", json={
        "project_ids": [row["project_id"], "missing-project"]})
    assert response.status_code == 409
    assert "missing-project" in response.text
    assert list(review_root.iterdir()) == []


async def test_creation_refuses_an_unrendered_member_not_just_the_whole_empty_board(
        client, review_root, tmp_path):
    row = await _seed(tmp_path)
    await _seed(tmp_path, project_id=row["project_id"])
    async with async_session() as session:
        await session.execute(update(ClipModel).where(ClipModel.id == row["clip_id"])
                              .values(export_path=None))
        await session.commit()
    response = await client.post("/api/clipper/review", json={"project_ids": [row["project_id"]]})
    assert response.status_code == 409
    assert list(review_root.iterdir()) == []


async def test_boards_from_two_runs_in_one_project_are_not_compared(
        client, review_root, tmp_path):
    row = await _seed(tmp_path)
    await _seed(tmp_path, project_id=row["project_id"], run="different")
    response = await client.post("/api/clipper/review", json={"project_ids": [row["project_id"]]})
    assert response.status_code == 409
    assert "different_selection_runs" in response.text
    assert list(review_root.iterdir()) == []


async def test_mixed_render_versions_are_reported_not_relabelled(
        client, review_root, tmp_path):
    row = await _seed(tmp_path)
    await _seed(tmp_path, project_id=row["project_id"], version="another-render")
    _, state = await _start(client, [row["project_id"]])
    assert state["render_version"] is None
    assert state["render_versions"] == ["another-render", "old-render"]


async def test_later_db_edits_do_not_change_the_presented_window_or_transcript(
        client, review_root, tmp_path):
    row = await _seed(tmp_path)
    sid, _ = await _start(client, [row["project_id"]])
    async with async_session() as session:
        await session.execute(update(ClipModel).where(ClipModel.id == row["clip_id"])
                              .values(start_time=200.0, end_time=900.0,
                                      duration=700.0, transcript_text="new words"))
        await session.commit()
    nxt = (await client.get(f"/api/clipper/review/{sid}/next")).json()["item"]
    assert (nxt["start"], nxt["end"], nxt["duration"], nxt["transcript"]) == (1.0, 6.0, 5.0, "old words")


async def test_replacement_blocks_video_and_answer_and_leaves_session_untouched(
        client, review_root, tmp_path):
    from routers import clipper_review

    row = await _seed(tmp_path)
    sid, state = await _start(client, [row["project_id"]])
    handle = state["order"][0]
    (tmp_path / f"{row['clip_id']}.mp4").write_bytes(b"y" * 2048)
    for endpoint in ("next", f"item/{handle}/video"):
        assert (await client.get(f"/api/clipper/review/{sid}/{endpoint}")).status_code == 409
    answer = await client.post(f"/api/clipper/review/{sid}/answer", json=_answer(handle))
    assert answer.status_code == 409
    assert clipper_review._load(sid)["answers"] == {}


async def test_answer_persists_the_presented_hashes_in_session_and_feedback(
        client, review_root, tmp_path):
    from routers import clipper_review
    from sqlalchemy import select

    row = await _seed(tmp_path)
    sid, state = await _start(client, [row["project_id"]])
    handle, media = state["order"][0], state["items"][0]["media"]
    answer = await client.post(f"/api/clipper/review/{sid}/answer", json=_answer(handle))
    assert answer.status_code == 200
    stored = clipper_review._load(sid)["answers"][handle]["presented_media"]
    assert stored["sha256"] == media["sha256"]
    assert stored["selection_run_id"] == "run123"
    async with async_session() as session:
        feedback = (await session.execute(select(ClipFeedbackModel)
                    .where(ClipFeedbackModel.clip_id == row["clip_id"]))).scalar_one()
    assert feedback.event_type == "reviewed"
    assert feedback.payload["presented_media"] == stored


async def test_old_results_stay_readable_but_cannot_accept_new_unbound_answers(
        client, review_root):
    from routers import clipper_review

    state = blind_review.create("historical", [{"clip_id": "old", "project_id": "old",
                                                "rank_position": 1}], seed=1)
    state["schema_version"] = 1
    state.pop("blinding_policy")
    clipper_review._save(state)
    handle = state["order"][0]
    assert (await client.get("/api/clipper/review/historical/result")).status_code == 200
    assert (await client.get("/api/clipper/review/historical/next")).status_code == 409
    assert (await client.post("/api/clipper/review/historical/answer",
                             json=_answer(handle))).status_code == 409
