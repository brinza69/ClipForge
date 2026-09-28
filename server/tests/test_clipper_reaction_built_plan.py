"""RX1: the reaction PUT checks the caption plan the render burns, stored or built.

RSK (B, 28 Sept): on three clips with no stored `caption_plan` the PUT answered
200 and every export was then refused ("reaction caption placement failed"). The
PUT checked only a stored plan; the render builds one (`_plan_for_render`) and
places that. Contract: codex-verdict-next-36 §3. The transcript is read through
the real path (a TranscriptModel row) unless a case needs that read to fail.
asyncio_mode=auto in pytest.ini.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, inspect, select, update

from config import settings
from database import async_session
from models import ClipFeedbackModel, ClipModel, JobModel, ProjectModel, TranscriptModel

FACE = {"x": 0, "y": 0, "w": 84, "h": 60}
SQUARE = {"x": 0, "y": 0, "w": 180, "h": 176}      # no caption gap (the hint tests' box)
_WORDS = (("you", 1.2), ("play", 1.7), ("fortnite", 2.2), ("every", 2.8), ("single", 3.4),
          ("day", 4.0))


def _segments(timing: str = "timed") -> list[dict]:
    seg = {"start": 1.2, "end": 4.6, "text": " ".join(w for w, _ in _WORDS)}
    if timing == "timed":
        seg["words"] = [{"word": w, "start": s, "end": s + 0.4} for w, s in _WORDS]
    elif timing == "incomplete":   # half the words lose their end: some timed, some not
        seg["words"] = [{"word": w, "start": s, **({"end": s + 0.4} if i < 3 else {})}
                        for i, (w, s) in enumerate(_WORDS)]
    return [seg]


@pytest.fixture
def small_source(tmp_path):
    from services.clipper.ffmpeg_tools import ffmpeg_bin, run
    p = tmp_path / "source.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=6",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(p)],
        what="built plan fixture")
    return p


@pytest.fixture
def seed(small_source):
    """A clip as `_write_clips` leaves an alternative: no caption plan, no framing."""
    async def make(transcript: str | None = "timed", project_cfg: dict | None = None,
                   **clip_kw) -> tuple[str, str]:
        pid = "rxbuilt-" + uuid.uuid4().hex[:8]
        cfg = {"dynamic_edit": False, "trim_silence": False, **(project_cfg or {})}
        async with async_session() as s:
            s.add(ProjectModel(id=pid, title="built plan", source_kind="file", status="ready",
                               video_path=str(small_source), width=320, height=180, fps=24,
                               clipper_settings=cfg))
            s.add(ClipModel(id=pid + "-c", project_id=pid, title="t", start_time=1.0,
                            end_time=5.0, duration=4.0,
                            **{"status": "candidate", "caption_plan": None, **clip_kw}))
            if transcript:
                s.add(TranscriptModel(project_id=pid, language="en", full_text="x",
                                      segments=_segments(transcript)))
            await s.commit()
        return pid + "-c", str(small_source)
    return make


def _body(src: str, content: dict) -> dict:
    from services.clipper.reaction_edit import compute_source_version
    return {"content_rect": content, "face_rect": FACE,
            "source_version": compute_source_version(src, 320, 180),
            "source_start": 1.0, "source_end": 5.0, "src_w": 320, "src_h": 180}


def _put(client, cid: str, body: dict):
    return client.put(f"/api/clipper/clips/{cid}/reaction-layout", json=body)


async def _snapshot(cid: str) -> dict:
    """Every column of the clip and its project, its events and its jobs: "nothing written"."""
    async with async_session() as s:
        clip = await s.get(ClipModel, cid)
        project = await s.get(ProjectModel, clip.project_id)
        events = await s.scalar(select(func.count()).select_from(ClipFeedbackModel)
                                .where(ClipFeedbackModel.clip_id == cid))
        jobs = (await s.execute(select(JobModel.id).where(JobModel.clip_id == cid))).all()
        return {"clip": {a.key: getattr(clip, a.key) for a in inspect(ClipModel).column_attrs},
                "project": {a.key: getattr(project, a.key)
                            for a in inspect(ProjectModel).column_attrs},
                "events": events, "jobs": len(jobs)}


def _no_build(monkeypatch):
    from workers import clipper_captions

    async def refuse(*_a, **_k):
        raise AssertionError("this case must not build a caption plan")
    monkeypatch.setattr(clipper_captions, "_plan_for_render", refuse)


# ── the witness: no plan stored, a box with no caption slot ──────────────────

async def test_without_a_stored_plan_the_put_refuses_atomically_and_names_a_height(
        client, seed):
    cid, src = await seed()
    before = await _snapshot(cid)
    r = await _put(client, cid, _body(src, SQUARE))
    assert r.status_code == 422, r.text                  # was 200, then every export refused
    detail = r.json()["detail"]
    assert detail["error"] == "caption_placement_failed"
    h = detail["max_content_height"]
    assert isinstance(h, int) and 2 <= h < SQUARE["h"] and f"at most {h} px" in detail["message"]
    assert await _snapshot(cid) == before


async def test_the_framing_the_put_refuses_is_one_the_render_refuses(seed, tmp_path):
    """Stored the way the old PUT stored it: the export's own decision refuses it."""
    from unittest.mock import AsyncMock, patch

    from services.clipper.reaction_edit import BINDING_SCHEMA, compute_source_version
    from services.clipper.reaction_layout import plan_reaction_layout
    from workers import clipper_render_plan as crp
    cid, src = await seed()
    plan = plan_reaction_layout(content_rect=SQUARE, face_rect=FACE, src_w=320, src_h=180,
                                face_pct=0.40)
    plan["reaction_binding"] = {
        "schema": BINDING_SCHEMA, "source_version": compute_source_version(src, 320, 180),
        "source_start": 1.0, "source_end": 5.0, "src_w": 320, "src_h": 180, "by": "human"}
    async with async_session() as s:
        (await s.get(ClipModel, cid)).layout_plan = plan
        await s.commit()
        clip = await s.get(ClipModel, cid)
        project = await s.get(ProjectModel, clip.project_id)
    with patch.object(crp, "_dead_spans", new_callable=AsyncMock, return_value=[]):
        with pytest.raises(RuntimeError, match="reaction caption placement failed"):
            await crp._decide_render(clip, project, tmp_path)


class _Queue:
    worker_id = "rx1-worker"

    async def update_progress(self, *_):
        pass

    def is_cancelled(self, _):
        return False


async def test_the_named_height_saves_and_the_real_handler_burns_it(client, seed, tmp_path,
                                                                   monkeypatch):
    from workers import clipper_render_jobs as jobs
    cid, src = await seed()
    h = (await _put(client, cid, _body(src, SQUARE))).json()["detail"]["max_content_height"]
    r = await _put(client, cid, _body(src, {**SQUARE, "h": h}))
    assert r.status_code == 200, r.text
    assert r.json()["caption_placement"] == {"verified": True, "reason": None}
    assert r.json()["clip"]["caption_plan"] is None        # checked, never stored

    out = tmp_path / "exports" / f"{cid}.mp4"
    monkeypatch.setattr(settings, "clipper_export_crf", 26)
    monkeypatch.setattr(settings, "clipper_export_preset", "ultrafast")
    monkeypatch.setattr(settings, "clipper_vision_review", False)
    monkeypatch.setattr(jobs.storage, "export_path", lambda *_: out)
    job = "rx1-" + uuid.uuid4().hex[:8]
    async with async_session() as s:
        s.add(JobModel(id=job, project_id=cid[:-2], clip_id=cid, type="clipper_export",
                       status="running", worker_id=_Queue.worker_id))
        await s.execute(update(ClipModel).where(ClipModel.id == cid)
                        .values(status="exporting", export_job_id=job))
        await s.commit()
    await jobs.handle_export(job, cid[:-2], cid, {"origin": "manual"}, _Queue())

    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    state = side["caption_plan_state"]
    assert (state["origin"], state["outcome"], state["reason"]) == ("built", "burn", None)
    assert side["render_record"]["caption_filter"] is True
    assert max(_white(out, 800), _white(out, 2500)) > 100, "the captions are in the pixels"
    async with async_session() as s:
        row = await s.get(ClipModel, cid)
    assert row.status == "exported" and row.caption_plan is None


def _white(path, at_ms: int) -> int:
    """Bright pixels in one decoded frame: the source is flat blue, so they are caption ink."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, at_ms)
    ok, frame = cap.read()
    cap.release()
    assert ok, "the exported file must decode"
    return int((frame.min(axis=2) > 200).sum())


# ── what could not be checked: missing timing saves, a failed read refuses ───

@pytest.mark.parametrize("transcript,reason", [(None, "no_transcript"),
                                                ("untimed", "no_timed_words"),
                                                ("incomplete", "incomplete_timing")])
async def test_missing_timing_saves_and_says_nothing_was_checked(client, seed, transcript,
                                                                 reason):
    cid, src = await seed(transcript)
    r = await _put(client, cid, _body(src, SQUARE))
    assert r.status_code == 200, r.text
    assert r.json()["caption_placement"] == {"verified": False, "reason": reason}
    assert r.json()["clip"]["layout_plan"]["game_content_fit"] is True


BAND = {"x": 0, "y": 0, "w": 320, "h": 60}          # a short band that leaves a slot


def fail_read_or_build(monkeypatch, case: str) -> None:
    """The two inputs `_plan_for_render` reads, made to fail (the "stored" case needs neither)."""
    from services.clipper import captions
    from workers import clipper_captions

    async def unreadable(*_a, **_k):
        raise OSError("transcript store unreadable")

    def unbuildable(*_a, **_k):
        raise ValueError("builder failed")
    if case == "read":
        monkeypatch.setattr(clipper_captions, "_project_transcript", unreadable)
    elif case == "build":
        monkeypatch.setattr(captions, "build_caption_plan", unbuildable)


@pytest.mark.parametrize("case,reason", [("read", "transcript_unreadable"),
                                          ("build", "build_failed"),
                                          ("stored", "unreadable_plan")])
async def test_a_check_that_could_not_run_refuses_and_writes_nothing(client, seed, monkeypatch,
                                                                     case, reason):
    cid, src = await seed(**({"caption_plan": ["not", "a", "plan"]} if case == "stored" else {}))
    fail_read_or_build(monkeypatch, case)
    before = await _snapshot(cid)
    r = await _put(client, cid, _body(src, BAND))
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert (detail["error"], detail["reason"]) == ("caption_check_failed", reason)
    assert reason in detail["message"]
    assert await _snapshot(cid) == before


# ── what the stored plan and the layer already decided stays as it was ──────

@pytest.mark.parametrize("case", ["suppress", "empty", "manual"])
async def test_suppress_empty_and_manual_save_with_no_build(client, seed, monkeypatch, case):
    kw = {"suppress": {"caption_layer": "suppress"}, "empty": {"caption_plan": {"chunks": []}},
          "manual": {"caption_plan": {"chunks": [{"text": "HI", "start": 0.0, "end": 1.0}],
                                      "y_pct": 0.5, "y_pct_manual": True}}}[case]
    cid, src = await seed(**kw)
    _no_build(monkeypatch)
    r = await _put(client, cid, _body(src, SQUARE))
    assert r.status_code == 200, r.text
    assert r.json()["caption_placement"] is None


async def test_a_stored_plan_is_still_the_one_checked(client, seed, monkeypatch):
    cid, src = await seed(caption_plan={"preset_id": "bold_impact", "position": "bottom",
                                        "y_pct": 0.75, "chunks": [
                                            {"text": "YOU PLAY FORTNITE", "start": 0.0,
                                             "end": 1.0}]})
    _no_build(monkeypatch)
    r = await _put(client, cid, _body(src, SQUARE))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "caption_placement_failed"


# ── the refusals that come first are unchanged ───────────────────────────────

@pytest.mark.parametrize("case,status,error", [
    ("exporting", 409, "clip_exporting"), ("stale", 409, "stale_source_version"),
    ("blur", 422, "static_path_unsupported")])
async def test_earlier_refusals_still_come_first(client, seed, monkeypatch, case, status, error):
    kw = {"exporting": {"status": "exporting"},
          "blur": {"source_caption_treatment": {"treatment": "blur", "mask_sha256": "0" * 64}}}
    cid, src = await seed(**kw.get(case, {}))
    _no_build(monkeypatch)
    body = _body(src, SQUARE)
    if case == "stale":
        body["source_version"] = "stale-" + body["source_version"]
    r = await _put(client, cid, body)
    assert (r.status_code, r.json()["detail"]["error"]) == (status, error), r.text
