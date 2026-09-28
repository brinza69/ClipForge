"""S8b: no board disclosure until the immutable answer set is complete.

The HTTP edge is the contract. Tests call it directly, not just the disabled
button, including simultaneous requests and retry after the feedback DB fails.
"""

from __future__ import annotations

import asyncio
import copy
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from routers import clipper_review as api
from services.clipper import blind_review as review, review_lock


def _answer(handle, **kw):
    return {"review_item_id": handle, "worth_exporting": "yes", "self_contained": "yes",
            "hook": "strong", "start_boundary": "correct", "end_boundary": "correct",
            "technical_problem": "no", **kw}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_root", lambda: tmp_path)
    prefix = uuid.uuid4().hex[:12]
    rows = [{"clip_id": f"{prefix}{i}", "project_id": "source", "rank_position": i + 1,
             "shadow_rank": None if i == 0 else i + 1,
             "media": {"sha256": "a" * 64, "sidecar_sha256": "b" * 64,
                       "render_version": "test", "selection_run_id": "run",
                       "presentation": {"start_time": 0.0, "end_time": 5.0}}}
            for i in range(2)]
    session = review.create("sealed", rows, seed=1)
    api._save(session)
    # S8a's suite exercises real media. Here the only I/O substitute is that
    # media check; the actual session load, write, OS lock and feedback run.
    async def verified(_item):
        return tmp_path / "test.mp4"
    monkeypatch.setattr(api, "_verified_media", verified)
    return session


async def test_partial_result_cannot_reveal_membership_or_even_a_board_tally(client, state):
    for done in range(2):
        response = await client.get("/api/clipper/review/sealed/result")
        assert response.status_code == 409
        assert response.json() == {"detail": "review_not_complete"}
        handle = state["order"][done]
        assert (await client.post("/api/clipper/review/sealed/answer",
                                  json=_answer(handle))).status_code == 200
    result = await client.get("/api/clipper/review/sealed/result")
    assert result.status_code == 200
    assert result.json()["historical"] is False
    assert result.json()["blinding_policy"] == review.BLINDING_POLICY
    assert len(result.json()["items"]) == 2


async def test_same_retry_is_idempotent_but_changed_answer_is_refused(client, state):
    from database import async_session
    from services.clipper import feedback

    handle = state["order"][0]
    url = "/api/clipper/review/sealed/answer"
    first = await client.post(url, json=_answer(handle))
    assert first.status_code == 200
    saved = api._load("sealed")
    assert (await client.post(url, json=_answer(handle))).status_code == 200
    assert (await client.post(url, json=_answer(handle, hook="weak"))).status_code == 409
    assert api._load("sealed") == saved
    item = next(i for i in state["items"] if i["review_item_id"] == handle)
    async with async_session() as db:
        events = await feedback.events_for_clip(db, item["clip_id"])
    assert sum(e["payload"].get("review_item_id") == handle for e in events) == 1


async def test_revealed_result_cannot_be_rewritten(client, state):
    url = "/api/clipper/review/sealed/answer"
    for handle in state["order"]:
        assert (await client.post(url, json=_answer(handle))).status_code == 200
    first = (await client.get("/api/clipper/review/sealed/result")).json()
    assert (await client.post(url, json=_answer(state["order"][0], hook="weak"))).status_code == 409
    assert (await client.get("/api/clipper/review/sealed/result")).json() == first


@pytest.mark.parametrize("corruption", ["foreign_answer", "invalid_answer", "duplicate_slot", "no_slots"])
async def test_counts_are_not_proof_of_completion(client, state, corruption):
    for handle in state["order"]:
        payload = _answer(handle)
        payload.pop("review_item_id")
        review.record(state, handle, payload)
    if corruption == "foreign_answer":
        state["answers"]["foreign"] = state["answers"].pop(state["order"][0])
    elif corruption == "invalid_answer":
        state["answers"][state["order"][0]]["hook"] = ["strong"]
    elif corruption == "duplicate_slot":
        state["order"][1] = state["order"][0]
    else:
        state["order"], state["items"], state["answers"] = [], [], {}
    api._save(state)
    response = await client.get("/api/clipper/review/sealed/result")
    assert response.status_code == 409
    assert "items" not in response.json()
    if corruption != "foreign_answer":
        assert (await client.get("/api/clipper/review/sealed/next")).status_code == 409


@pytest.mark.parametrize("version", [1, 2])
async def test_historical_results_are_readable_but_read_only(client, state, version):
    state["schema_version"] = version
    state.pop("blinding_policy")
    api._save(state)
    response = await client.get("/api/clipper/review/sealed/result")
    assert response.status_code == 200
    assert response.json()["historical"] is True
    assert (await client.get("/api/clipper/review/sealed/next")).status_code == 409
    assert (await client.post("/api/clipper/review/sealed/answer",
                              json=_answer(state["order"][0]))).status_code == 409


async def test_missing_new_policy_is_corruption_not_permission_to_reveal(client, state):
    state.pop("blinding_policy")
    api._save(state)
    response = await client.get("/api/clipper/review/sealed/result")
    assert response.status_code == 409
    assert "unsupported_review_contract" in response.text


async def test_concurrent_answers_cannot_overwrite_the_other_writers_state(
        client, state, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(_item):
        entered.set()
        await release.wait()
    monkeypatch.setattr(api, "_verified_media", blocked)
    first = asyncio.create_task(client.post("/api/clipper/review/sealed/answer",
                                            json=_answer(state["order"][0])))
    await entered.wait()
    try:
        second = await client.post("/api/clipper/review/sealed/answer",
                                   json=_answer(state["order"][1]))
        assert second.status_code == 409
        assert "review_answer_in_progress" in second.text
    finally:
        release.set()
        result = await first
    assert result.status_code == 200
    retry = await client.post("/api/clipper/review/sealed/answer",
                              json=_answer(state["order"][1]))
    assert retry.status_code == 200
    assert review.complete(api._load("sealed"))


async def test_retry_mirrors_a_saved_answer_after_feedback_write_failure(client, state, monkeypatch):
    from database import async_session
    from services.clipper import feedback

    original = feedback.record
    async def fail(*args, **kw):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(feedback, "record", fail)
    handle = state["order"][0]
    with pytest.raises(RuntimeError, match="database unavailable"):
        await client.post("/api/clipper/review/sealed/answer", json=_answer(handle))
    saved = copy.deepcopy(api._load("sealed")["answers"][handle])
    monkeypatch.setattr(feedback, "record", original)
    assert (await client.post("/api/clipper/review/sealed/answer",
                              json=_answer(handle))).status_code == 200
    assert api._load("sealed")["answers"][handle] == saved
    item = next(i for i in state["items"] if i["review_item_id"] == handle)
    async with async_session() as db:
        events = await feedback.events_for_clip(db, item["clip_id"])
    assert sum(e["payload"].get("review_item_id") == handle for e in events) == 1


def test_the_answer_lock_is_shared_across_backend_processes(tmp_path):
    path = tmp_path / "session.json"
    code = ("from pathlib import Path; from services.clipper.review_lock import answer_lock; "
            f"ctx=answer_lock(Path({str(path)!r})); ctx.__enter__(); ctx.__exit__(None,None,None)")
    def child():
        return subprocess.run([sys.executable, "-c", code],
                              cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True, timeout=10)
    with review_lock.answer_lock(path):
        denied = child()
        assert denied.returncode != 0 and "ReviewBusy" in denied.stderr
    assert child().returncode == 0
