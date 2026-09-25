"""B3: a blind review compares the boards of ONE selection run per project.

A rescore keeps exports and person-touched clips with their old run id, so a
board mixes runs. These tests drive the real endpoints on the test DB with the
real trace writer (`_write_traces`), because the proof of a run's members is
whatever that writer puts on disk — a hand-written trace would test a format
nothing produces.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select, update

from database import async_session
from models import ClipModel, ProjectModel
from services.clipper import storage
from services.clipper.reasoning_trace import RunTrace
from workers.clipper_finalize import _write_clips, _write_traces

OLD, NEW = "oldrun000001", "newrun000001"


@pytest.fixture
def review_root(tmp_path, monkeypatch):
    from routers import clipper_review

    root = tmp_path / "sessions"
    root.mkdir()
    monkeypatch.setattr(clipper_review, "_root", lambda: root)
    return root


def _export(tmp_path, clip_id, project_id, run, start):
    path = tmp_path / f"{clip_id}.mp4"
    path.write_bytes(clip_id.encode() * 200)
    path.with_suffix(".json").write_text(json.dumps({
        "clip_id": clip_id, "project_id": project_id, "source_start": start,
        "source_end": start + 5.0, "duration": 5.0, "selection_run_id": run,
        "render_version": "r1", "transcript": "words"}), encoding="utf-8")
    return path


async def _clip(tmp_path, project_id, run, start, *, rank=None, shadow=None,
                status="candidate", shadow_run="same"):
    clip_id = uuid.uuid4().hex[:12]
    path = _export(tmp_path, clip_id, project_id, run, start)
    async with async_session() as session:
        if await session.get(ProjectModel, project_id) is None:
            session.add(ProjectModel(id=project_id, title="cohorts", source_kind="file"))
        session.add(ClipModel(
            id=clip_id, project_id=project_id, start_time=start, end_time=start + 5.0,
            duration=5.0, export_path=str(path), status=status, rank_position=rank,
            shadow_rank=shadow, selection_run_id=run,
            shadow_run_id=(run if shadow_run == "same" else shadow_run) if shadow else None))
        await session.commit()
    return clip_id


def _trace(project_id, run, field, shadow_detail="2 winners, 0 backfilled, 1 differ from legacy"):
    trace = RunTrace(project_id, mode="story_v2_shadow")
    trace.run_id = run
    if shadow_detail is not None:
        trace.note_stage("board_v2", "would", shadow_detail)
    _write_traces(project_id, trace, field, "story_v2_shadow")


async def _mixed_board(tmp_path):
    """Old export + old edited clip (kept by a rescore) + the new run's board."""
    project_id = uuid.uuid4().hex[:12]
    ids = {
        "old_export": await _clip(tmp_path, project_id, OLD, 0.0, rank=2, status="exported"),
        "old_edited": await _clip(tmp_path, project_id, OLD, 10.0, rank=5, status="approved"),
        "new_1": await _clip(tmp_path, project_id, NEW, 20.0, rank=1),
        "new_2": await _clip(tmp_path, project_id, NEW, 30.0, rank=2, shadow=1),
        "new_s2": await _clip(tmp_path, project_id, NEW, 40.0, shadow=2),
    }
    _trace(project_id, NEW, _field())
    return project_id, ids


def _field():
    """The candidates as the build hands them to `_write_traces`: shadow picks
    stamped by `clipper_build` (`shadow_rank`, `shadow_run_id`), never read back
    from the rows. 60.0 is a pool candidate neither board took."""
    return [{"start": 20.0, "end": 25.0, "rank_position": 1},
            {"start": 30.0, "end": 35.0, "rank_position": 2,
             "shadow_rank": 1, "shadow_run_id": NEW},
            {"start": 40.0, "end": 45.0, "rank_position": None,
             "shadow_rank": 2, "shadow_run_id": NEW},
            {"start": 60.0, "end": 65.0, "rank_position": None}]


async def _runs(project_id):
    async with async_session() as session:
        rows = (await session.execute(select(ClipModel.id, ClipModel.selection_run_id,
                                             ClipModel.shadow_run_id)
                                      .where(ClipModel.project_id == project_id))).all()
    return sorted(rows)


def _cohort(body, run):
    return next(c for c in body["cohorts"] if c["selection_run_id"] == run)


async def test_the_named_new_run_is_reviewed_and_the_kept_clips_stay_out(
        client, review_root, tmp_path):
    from routers import clipper_review

    project_id, ids = await _mixed_board(tmp_path)
    before = await _runs(project_id)
    implicit = await client.post("/api/clipper/review", json={"project_ids": [project_id]})
    assert implicit.status_code == 409
    assert "board_contains_different_selection_runs" in implicit.text

    listing = await client.get("/api/clipper/review/cohorts", params={"project_id": project_id})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["board_rows"] == 5 == sum(c["members"] for c in body["cohorts"])
    new, old = _cohort(body, NEW), _cohort(body, OLD)
    assert (new["eligible"], new["missing"], new["membership"]["status"]) == (True, [], "proven")
    assert (new["members"], new["legacy_members"], new["shadow_members"]) == (3, 2, 2)
    assert new["media"] == {"checked": 3, "valid": 3, "problems": {}}
    assert old["eligible"] is False
    assert old["membership"] == {"status": "unprovable", "reason": "trace_of_another_run",
                                 "expected_legacy": None, "expected_shadow": None}
    assert "no_shadow_board" in old["missing"]
    for clip_id in ids.values():
        assert clip_id not in listing.text

    response = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: NEW}, "seed": 3})
    assert response.status_code == 200, response.text
    assert response.json()["selection_runs"] == {project_id: NEW}
    state = clipper_review._load(response.json()["session_id"])
    assert sorted(i["clip_id"] for i in state["items"]) == sorted(
        [ids["new_1"], ids["new_2"], ids["new_s2"]])
    assert state["selection_identity_complete"] is True
    nxt = await client.get(f"/api/clipper/review/{state['session_id']}/next")
    for secret in (NEW, "proven", "selection_runs", *ids.values()):
        assert secret not in nxt.text
    assert await _runs(project_id) == before


async def test_old_runs_cannot_be_proven_even_when_named(client, review_root, tmp_path):
    project_id, _ = await _mixed_board(tmp_path)
    response = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: OLD}})
    assert response.status_code == 409
    [refused] = response.json()["detail"]["cohorts"]
    assert "membership_unprovable" in refused["missing"]
    assert list(review_root.iterdir()) == []


async def test_a_run_of_another_project_is_refused_and_nothing_is_relabelled(
        client, review_root, tmp_path):
    project_id, _ = await _mixed_board(tmp_path)
    other = uuid.uuid4().hex[:12]
    await _clip(tmp_path, other, "foreignrun01", 0.0, rank=1)
    before = await _runs(project_id)
    response = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: "foreignrun01"}})
    assert response.status_code == 409
    assert response.json()["detail"] == {"error": "cohort_not_eligible", "cohorts": [
        {"project_id": project_id, "selection_run_id": "foreignrun01",
         "missing": ["run_not_in_project"]}]}
    assert await _runs(project_id) == before
    assert list(review_root.iterdir()) == []


@pytest.mark.parametrize("which", ["unnamed", "unrequested"])
async def test_selection_runs_must_name_exactly_the_requested_projects(
        client, review_root, tmp_path, which):
    first, _ = await _mixed_board(tmp_path)
    second, _ = await _mixed_board(tmp_path)
    requested = [first, second] if which == "unnamed" else [first]
    response = await client.post("/api/clipper/review", json={
        "project_ids": requested,
        "selection_runs": {first: NEW} if which == "unnamed" else {first: NEW, second: NEW}})
    assert response.status_code == 400
    assert response.json()["detail"][which] == [second]
    assert list(review_root.iterdir()) == []


async def test_a_shadow_rank_naming_another_run_is_refused(client, review_root, tmp_path):
    project_id, ids = await _mixed_board(tmp_path)
    async with async_session() as session:
        await session.execute(update(ClipModel).where(ClipModel.id == ids["new_s2"])
                              .values(shadow_run_id="otherrun0001"))
        await session.commit()
    body = (await client.get("/api/clipper/review/cohorts",
                             params={"project_id": project_id})).json()
    assert "shadow_run_mismatch" in _cohort(body, NEW)["missing"]
    response = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: NEW}})
    assert response.status_code == 409
    assert "shadow_run_mismatch" in response.text
    assert list(review_root.iterdir()) == []


async def test_a_legacy_only_board_is_explicitly_unknown_not_a_comparison(
        client, review_root, tmp_path):
    from routers import clipper_review

    project_id = uuid.uuid4().hex[:12]
    await _clip(tmp_path, project_id, None, 0.0, rank=1)
    await _clip(tmp_path, project_id, None, 10.0, rank=2)
    body = (await client.get("/api/clipper/review/cohorts",
                             params={"project_id": project_id})).json()
    [unknown] = body["cohorts"]
    assert unknown["selection_run_id"] is None and unknown["eligible"] is False
    assert unknown["membership"]["reason"] == "unknown_run_has_no_trace"
    named = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: None}})
    assert named.status_code == 409
    assert "membership_unprovable" in named.text
    # Without the field: the pre-B3 session, still labelled incomplete.
    legacy = await client.post("/api/clipper/review", json={"project_ids": [project_id]})
    assert legacy.status_code == 200
    assert clipper_review._load(legacy.json()["session_id"])[
        "selection_identity_complete"] is False


async def test_null_never_borrows_identified_rows_and_a_run_never_borrows_unknown(
        client, review_root, tmp_path):
    from routers import clipper_review

    project_id, ids = await _mixed_board(tmp_path)
    unknown = await _clip(tmp_path, project_id, None, 50.0, rank=3)
    body = (await client.get("/api/clipper/review/cohorts",
                             params={"project_id": project_id})).json()
    assert [c["selection_run_id"] for c in body["cohorts"]] == [NEW, OLD, None]
    assert _cohort(body, None)["members"] == 1
    refused = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: None}})
    assert refused.status_code == 409
    response = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: NEW}})
    assert response.status_code == 200, response.text
    state = clipper_review._load(response.json()["session_id"])
    assert unknown not in {i["clip_id"] for i in state["items"]}
    assert ids["old_export"] not in {i["clip_id"] for i in state["items"]}


@pytest.mark.parametrize("change,reason", [
    ("delete_legacy", "legacy_board_differs_from_trace"),
    ("delete_shadow", "shadow_board_differs_from_trace"),
    ("trim", "member_window_changed"),
])
async def test_a_run_missing_members_is_not_presented_as_a_complete_board(
        client, review_root, tmp_path, change, reason):
    from sqlalchemy import delete

    project_id, ids = await _mixed_board(tmp_path)
    async with async_session() as session:
        if change == "trim":
            await session.execute(update(ClipModel).where(ClipModel.id == ids["new_1"])
                                  .values(start_time=21.0))
        else:
            gone = ids["new_1"] if change == "delete_legacy" else ids["new_s2"]
            await session.execute(delete(ClipModel).where(ClipModel.id == gone))
        await session.commit()
    body = (await client.get("/api/clipper/review/cohorts",
                             params={"project_id": project_id})).json()
    new = _cohort(body, NEW)
    assert new["eligible"] is False and reason in new["missing"]
    assert new["membership"]["status"] == "differs"
    assert (new["membership"]["expected_legacy"], new["membership"]["expected_shadow"]) == (2, 2)
    response = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: NEW}})
    assert response.status_code == 409 and reason in response.text
    assert list(review_root.iterdir()) == []


@pytest.mark.parametrize("detail", ["two winners", None])
async def test_a_shadow_count_that_cannot_be_read_is_not_a_zero(
        client, review_root, tmp_path, detail):
    project_id, _ = await _mixed_board(tmp_path)
    _trace(project_id, NEW, _field(), shadow_detail=detail)
    new = _cohort((await client.get("/api/clipper/review/cohorts",
                                    params={"project_id": project_id})).json(), NEW)
    assert new["eligible"] is False
    # None = no board_v2 stage: reasoning_run says 0, the recorded picks say 2.
    # Two artefacts of one run that disagree prove nothing.
    assert new["membership"]["reason"] == "trace_unreadable"


async def test_an_unrendered_member_is_counted_and_refused(client, review_root, tmp_path):
    project_id, ids = await _mixed_board(tmp_path)
    async with async_session() as session:
        await session.execute(update(ClipModel).where(ClipModel.id == ids["new_s2"])
                              .values(export_path=None))
        await session.commit()
    new = _cohort((await client.get("/api/clipper/review/cohorts",
                                    params={"project_id": project_id})).json(), NEW)
    assert new["media"] == {"checked": 3, "valid": 2, "problems": {"no_full_export": 1}}
    assert new["missing"] == ["media_invalid"]
    response = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: NEW}})
    assert response.status_code == 409 and "media_invalid" in response.text


async def test_an_unknown_project_is_404_not_an_empty_list(client):
    response = await client.get("/api/clipper/review/cohorts",
                                params={"project_id": "nosuchproject"})
    assert response.status_code == 404
    assert response.json()["detail"] == "project_not_found"


async def _refused(client, review_root, project_id):
    new = _cohort((await client.get("/api/clipper/review/cohorts",
                                    params={"project_id": project_id})).json(), NEW)
    response = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: NEW}})
    assert response.status_code == 409, response.text
    assert list(review_root.iterdir()) == []
    return new


@pytest.mark.parametrize("change,reason", [
    # Codex B3: same count, same ranks 1..M, every window in the run's pool.
    ("substitute", "member_window_changed"),
    ("permute", "member_window_changed"),
    ("same_pick_twice", "member_window_changed"),
    ("extra_copy", "shadow_board_differs_from_trace"),
])
async def test_a_shadow_board_other_than_the_recorded_one_is_refused(
        client, review_root, tmp_path, change, reason):
    project_id, ids = await _mixed_board(tmp_path)
    if change == "substitute":  # the pool candidate at 60.0 takes pick 2's rank
        ids["pool"] = await _clip(tmp_path, project_id, NEW, 60.0)
    elif change == "extra_copy":
        await _clip(tmp_path, project_id, NEW, 40.0, shadow=2)
    edits = {"substitute": {"new_s2": {"shadow_rank": None, "shadow_run_id": None},
                            "pool": {"shadow_rank": 2, "shadow_run_id": NEW}},
             "permute": {"new_2": {"shadow_rank": 2}, "new_s2": {"shadow_rank": 1}},
             "same_pick_twice": {"new_s2": {"start_time": 30.0, "end_time": 35.0}},
             "extra_copy": {}}[change]
    async with async_session() as session:
        for key, values in edits.items():
            await session.execute(update(ClipModel).where(ClipModel.id == ids[key])
                                  .values(**values))
        await session.commit()
    new = await _refused(client, review_root, project_id)
    if change != "extra_copy":
        assert new["shadow_members"] == new["membership"]["expected_shadow"] == 2
    assert new["membership"]["status"] == "differs"
    assert new["eligible"] is False and reason in new["missing"]


async def test_a_trace_without_the_recorded_shadow_selection_proves_nothing(
        client, review_root, tmp_path):
    """A trace written before B3r: legacy ranks, a shadow COUNT, and no picks.
    It stays readable and is never new proof — nor is it completed from the rows."""
    project_id, _ = await _mixed_board(tmp_path)
    trace = storage.read_artifact(project_id, "selection_trace")
    trace.pop("shadow_selection", None)
    storage.write_artifact(project_id, "selection_trace", trace)
    new = await _refused(client, review_root, project_id)
    assert new["membership"] == {"status": "unprovable",
                                 "reason": "trace_has_no_shadow_selection",
                                 "expected_legacy": None, "expected_shadow": None}
    assert storage.read_artifact(project_id, "selection_trace") == trace


@pytest.mark.parametrize("corrupt", ["duplicate_rank", "rank_gap", "other_run",
                                     "not_a_list", "bool_rank"])
async def test_a_recorded_selection_that_contradicts_itself_is_unreadable(
        client, review_root, tmp_path, corrupt):
    project_id, _ = await _mixed_board(tmp_path)
    trace = storage.read_artifact(project_id, "selection_trace")
    picks = trace["shadow_selection"]["picks"]
    if corrupt == "duplicate_rank":
        picks[1]["shadow_rank"] = 1
    elif corrupt == "rank_gap":
        picks[1]["shadow_rank"] = 3
    elif corrupt == "other_run":
        picks[0]["shadow_run_id"] = OLD
    elif corrupt == "bool_rank":
        picks[0]["shadow_rank"] = True
    else:
        trace["shadow_selection"]["picks"] = None
    storage.write_artifact(project_id, "selection_trace", trace)
    new = await _refused(client, review_root, project_id)
    assert new["membership"]["status"] == "unprovable"
    assert new["membership"]["reason"] == "trace_unreadable"


async def test_a_pick_whose_window_another_candidate_shares_cannot_be_proven(
        client, review_root, tmp_path):
    """The row carries a window, not a candidate id. When two candidates of the
    run had the same window, the window cannot say which of them the row is."""
    project_id, _ = await _mixed_board(tmp_path)
    _trace(project_id, NEW, _field() + [{"start": 40.0, "end": 45.0, "variant": "story"}])
    new = await _refused(client, review_root, project_id)
    assert new["membership"]["status"] == "unprovable"
    assert new["membership"]["reason"] == "trace_window_ambiguous"


async def test_a_fresh_run_written_by_the_real_writers_is_proven(
        client, review_root, tmp_path):
    """The positive case end to end: rows from `_write_clips`, trace from
    `_write_traces`, both fed the same stamped candidates the build produces.
    Only the export files are the test's — rendering is not what is under test."""
    from routers import clipper_review

    project_id, run = uuid.uuid4().hex[:12], "freshrun0001"
    async with async_session() as session:
        session.add(ProjectModel(id=project_id, title="fresh", source_kind="file"))
        await session.commit()
    field = [{"start": 0.0, "end": 5.0, "rank_position": 1},
             {"start": 10.0, "end": 15.0, "rank_position": 2,
              "shadow_rank": 1, "shadow_run_id": run},
             {"start": 20.0, "end": 25.0, "is_alternative": True,
              "shadow_rank": 2, "shadow_run_id": run},
             {"start": 30.0, "end": 35.0, "is_alternative": True}]
    await _write_clips(project_id, field, field[:2], "gaming", run)
    trace = RunTrace(project_id, mode="story_v2_shadow")
    trace.run_id = run
    trace.note_stage("board_v2", "would", "2 winners, 0 backfilled, 1 differ from legacy")
    _write_traces(project_id, trace, field, "story_v2_shadow")

    record = storage.read_artifact(project_id, "selection_trace")["shadow_selection"]
    assert record["version"] == "shadow_selection_v1"
    assert [(p["shadow_rank"], p["shadow_run_id"], p["start"], p["end"])
            for p in record["picks"]] == [(1, run, 10.0, 15.0), (2, run, 20.0, 25.0)]
    async with async_session() as session:
        rows = (await session.execute(select(ClipModel).where(
            ClipModel.project_id == project_id))).scalars().all()
        board = [r for r in rows if r.rank_position is not None or r.shadow_rank is not None]
        for row in board:
            row.export_path = str(_export(tmp_path, row.id, project_id, run, row.start_time))
        await session.commit()
    new = _cohort((await client.get("/api/clipper/review/cohorts",
                                    params={"project_id": project_id})).json(), run)
    assert new["membership"] == {"status": "proven", "reason": None,
                                 "expected_legacy": 2, "expected_shadow": 2}
    assert (new["eligible"], new["members"]) == (True, 3)
    response = await client.post("/api/clipper/review", json={
        "project_ids": [project_id], "selection_runs": {project_id: run}})
    assert response.status_code == 200, response.text
    state = clipper_review._load(response.json()["session_id"])
    assert sorted(i["clip_id"] for i in state["items"]) == sorted(r.id for r in board)


# The review's F5 probe. Hand-written ON PURPOSE: the writer cannot put one
# window at two shadow ranks (each pick is a distinct entry), so the only way
# to meet this record is a corrupt trace — with rows corrupted to match it.
def _hand_trace(picks):
    entries = [{"start": s, "end": s + 5.0, "rank_position": r, "eliminated_by": None}
               for s, r in ((0.0, 1), (10.0, 2), (20.0, None))]
    return {"trace_version": "selection_trace_v1", "run_id": NEW, "totals": {"winners": 2},
            "entries": entries, "shadow_selection": {"version": "shadow_selection_v1", "picks": [
                {"shadow_rank": r, "shadow_run_id": NEW, "start": s, "end": s + 5.0,
                 "eliminated_by": None} for r, s in picks]}}


def _hand_membership(pick_2_start):
    """Legacy 1 = 0–5, legacy 2 = shadow 1 = 10–15; shadow 2 in the record AND
    on its row at `pick_2_start`."""
    from services.clipper import review_cohorts

    rows = [{"clip_id": f"c{i}", "project_id": "p", "rank_position": rank,
             "shadow_rank": shadow, "shadow_run_id": NEW if shadow else None,
             "selection_run_id": NEW, "export_path": None, "start_time": s,
             "end_time": s + 5.0, "duration": 5.0, "transcript_text": ""}
            for i, (s, rank, shadow) in enumerate(
                [(0.0, 1, None), (10.0, 2, 1), (pick_2_start, None, 2)])]
    run = {"run_id": NEW, "stages": [{"name": "board_v2",
                                      "detail": "2 winners, 0 backfilled, 1 differ from legacy"}]}

    class _Media(Exception):
        pass
    report, _ = review_cohorts.evaluate(NEW, rows, 0, _hand_trace([(1, 10.0), (2, pick_2_start)]),
                                        run, lambda _m: {}, _Media)
    return report["membership"]


def test_a_record_that_picks_one_candidate_twice_is_not_proof():
    got = _hand_membership(10.0)
    assert (got["status"], got["reason"]) == ("unprovable", "trace_unreadable")


def test_a_window_on_both_boards_is_still_proven():
    """Guard: legacy rank 2 and shadow pick 1 share 10–15, which is legitimate."""
    got = _hand_membership(20.0)
    assert (got["status"], got["reason"]) == ("proven", None)
