"""D2: an alternative has no stored caption plan, and `burn` must still burn.

`_write_clips` stores `caption_plan` only for planned clips (winners and shadow
picks). Measured on two probe projects, 32/32 and 36/44 alternatives had none,
and an exported alternative shipped `caption_policy.action: "burn"` with
`render_record.caption_filter: false`, no `.ass` and no warning.

The plan is now built on the common decision path, for the render only; a
stored plan is never replaced; suppress builds nothing; and a burn that cannot
be built is reported on the sidecar and on `clip.warnings`. The MP4 tests run
the real encoder and look at pixels, not at the argv.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select, update

from config import settings
from database import async_session
from models import ClipFeedbackModel, ClipModel, JobModel, ProjectModel, TranscriptModel
from services.clipper.ffmpeg_tools import ffmpeg_bin, run
from services.clipper.serialize import clip_to_dict
from workers import clipper_captions as captions
from workers import clipper_render_jobs as jobs
from workers import clipper_render_plan as planning

# Timed words across the whole 0.25–2.25s window, on the SOURCE clock.
SPOKEN = [("HELLO", 0.30, 0.60), ("THERE", 0.62, 0.95), ("FRIENDS", 1.00, 1.40),
          ("WELCOME", 1.45, 1.80), ("BACK", 1.82, 2.20)]


def _segments(timed=True):
    seg = {"start": 0.25, "end": 2.25, "text": " ".join(w for w, *_ in SPOKEN)}
    if timed:
        seg["words"] = [{"word": w, "start": s, "end": e} for w, s, e in SPOKEN]
    return [seg]


async def _seed(ident, *, source="", cfg=None, caption_plan=None, transcript="timed"):
    """An ALTERNATIVE as `_write_clips` leaves it: no caption plan, no layout plan."""
    project = ProjectModel(id=ident, title="d2", source_kind="file", video_path=source,
                           width=320, height=180, fps=24,
                           clipper_settings={"dynamic_edit": False, "trim_silence": False,
                                             "fps": 24, **(cfg or {})})
    clip = ClipModel(id=ident, project_id=ident, title="alternative", start_time=0.25,
                     end_time=2.25, duration=2.0, transcript_text="hello there friends",
                     is_alternative=True, rank_position=None, shadow_rank=None,
                     caption_plan=caption_plan, layout_plan=None,
                     warnings=["Layout: kept from analysis"], status="candidate")
    async with async_session() as session:
        session.add(project)
        session.add(clip)
        if transcript:
            session.add(TranscriptModel(project_id=ident, language="en", full_text="x",
                                        segments=_segments(transcript == "timed")))
        await session.commit()
    return await planning._load(ident)


def _ass_text(decision):
    return Path(decision["ass_path"]).read_text(encoding="utf-8")


async def _no_build(*_a, **_k):
    raise AssertionError("a stored or suppressed plan must never trigger a build")


# ── the decision ─────────────────────────────────────────────────────────────

async def test_alternative_with_speech_and_burn_gets_captions(tmp_path):
    clip, project = await _seed("d2burn")
    decision = await planning._decide_render(clip, project, tmp_path)

    assert decision["caption_policy"]["action"] == "burn"
    assert decision["caption_plan_state"] == {"origin": "built", "outcome": "burn",
                                              "reason": None}
    assert decision["caption_plan"]["chunks"], "the built plan has no chunks"
    ass = _ass_text(decision)
    assert "HELLO" in ass and "BACK" in ass
    # Built for this render only: the row is untouched, no edit event exists,
    # and the in-memory row the caller holds was not mutated either.
    assert clip.caption_plan is None
    async with async_session() as session:
        row = await session.get(ClipModel, "d2burn")
        events = (await session.execute(select(ClipFeedbackModel.event_type)
                                        .where(ClipFeedbackModel.clip_id == "d2burn"))).all()
    assert row.caption_plan is None and events == []
    assert (row.is_alternative, row.rank_position, row.shadow_rank) == (True, None, None)


async def test_suppress_builds_nothing_and_burns_nothing(tmp_path, monkeypatch):
    clip, project = await _seed("d2supp", cfg={"source_has_burned_captions": True})
    monkeypatch.setattr(captions, "_project_transcript", _no_build)
    decision = await planning._decide_render(clip, project, tmp_path)

    assert decision["caption_policy"]["action"] == "suppress"
    assert decision["caption_plan_state"] == {"origin": None, "outcome": "suppressed",
                                              "reason": None}
    assert decision["ass_path"] is None and decision["caption_plan"] is None
    assert not list(tmp_path.glob("*.ass"))


async def test_a_manual_plan_is_kept_and_never_rebuilt(tmp_path, monkeypatch):
    manual = {"chunks": [{"text": "MANUAL EDIT", "start": 0.1, "end": 1.5, "words": []}],
              "style": {}, "x_pct": 0.5, "y_pct": 0.4, "y_pct_manual": True,
              "scale": 1.0, "preset_id": "bold_impact"}
    clip, project = await _seed("d2manual", caption_plan=manual)
    monkeypatch.setattr(captions, "_project_transcript", _no_build)
    decision = await planning._decide_render(clip, project, tmp_path)

    assert decision["caption_plan_state"] == {"origin": "stored", "outcome": "burn",
                                              "reason": None}
    assert decision["caption_plan"] == manual
    ass = _ass_text(decision)
    assert "MANUAL EDIT" in ass and "HELLO" not in ass


async def test_an_intentionally_empty_plan_is_not_missing(tmp_path, monkeypatch):
    """A plan with no chunks is an answer (a person cleared it, or nobody spoke).
    Reading it as 'missing' would rebuild captions somebody removed."""
    clip, project = await _seed("d2empty", caption_plan={"chunks": [], "y_pct": 0.8})
    monkeypatch.setattr(captions, "_project_transcript", _no_build)
    decision = await planning._decide_render(clip, project, tmp_path)

    assert decision["caption_plan_state"] == {"origin": "stored", "outcome": "empty",
                                              "reason": None}
    assert decision["ass_path"] is None
    assert captions._caption_warnings(["a"], decision["caption_plan_state"]) == ["a"]


@pytest.mark.parametrize("transcript,reason", [("untimed", "no_timed_words"),
                                                (None, "no_transcript")])
async def test_burn_without_timed_words_is_reported_not_silent(tmp_path, transcript, reason):
    clip, project = await _seed(f"d2no{reason[:6]}", transcript=transcript)
    decision = await planning._decide_render(clip, project, tmp_path)

    assert decision["caption_policy"]["action"] == "burn"
    assert decision["caption_plan_state"] == {"origin": None, "outcome": "unavailable",
                                              "reason": reason}
    assert decision["ass_path"] is None
    warned = captions._caption_warnings(clip.warnings, decision["caption_plan_state"])
    assert warned[0] == "Layout: kept from analysis"
    assert warned[1].startswith(captions.CAPTION_WARNING) and reason in warned[1]
    # And a later render that could burn takes its own warning back, and only it.
    assert captions._caption_warnings(warned, {"origin": "built", "outcome": "burn",
                                               "reason": None}) == ["Layout: kept from analysis"]


async def test_preview_and_export_decide_the_same_captions(tmp_path, monkeypatch):
    clip, project = await _seed("d2agree")
    seen = []
    real = planning._decide_render

    async def spy(*args, **kwargs):
        decision = await real(*args, **kwargs)
        seen.append((decision, _ass_text(decision)))
        return decision

    async def fake_preview(*a, **_k):
        # D2r-2: the preview publishes by renaming the file it rendered.
        Path(a[4]).write_bytes(b"mp4")

    import services.clipper.render as static_render
    monkeypatch.setattr(jobs, "_decide_render", spy)
    monkeypatch.setattr(jobs, "_source_path", lambda _: "unused.mp4")
    monkeypatch.setattr(static_render, "render_preview", fake_preview)
    await jobs.handle_preview("d2p", project.id, clip.id, {}, _Queue())
    # The export's decision, through the same entry point and the same inputs.
    await spy(clip, project, tmp_path / "export")

    (a, ass_a), (b, ass_b) = seen
    assert a["caption_plan_state"] == b["caption_plan_state"] == {
        "origin": "built", "outcome": "burn", "reason": None}
    assert a["caption_plan"] == b["caption_plan"]
    assert (a["caption_y"], a["caption_policy"]) == (b["caption_y"], b["caption_policy"])
    assert ass_a == ass_b
    async with async_session() as session:
        row = await session.get(ClipModel, "d2agree")
    assert row.caption_plan is None and row.warnings == ["Layout: kept from analysis"]


# ── the MP4 ──────────────────────────────────────────────────────────────────

class _Queue:
    worker_id = "d2-worker"

    async def update_progress(self, *_):
        pass

    def is_cancelled(self, _):
        return False


@pytest.fixture
def dark_source(tmp_path):
    if shutil.which(ffmpeg_bin()) is None:
        pytest.skip("ffmpeg is required for the MP4 probe")
    path = tmp_path / "dark.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=0x203040:s=320x180:r=24:d=3", "-t", "3", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], what="d2 source")
    return path


async def _export(ident, tmp_path, monkeypatch, source, **seed):
    clip, project = await _seed(ident, source=str(source), **seed)
    out = tmp_path / "exports" / f"{ident}.mp4"
    monkeypatch.setattr(settings, "clipper_export_crf", 26)
    monkeypatch.setattr(settings, "clipper_export_preset", "ultrafast")
    monkeypatch.setattr(settings, "clipper_vision_review", False)
    monkeypatch.setattr(jobs.storage, "export_path", lambda *_: out)
    job = f"{ident}-j"[:12]
    async with async_session() as session:
        session.add(JobModel(id=job, project_id=ident, clip_id=ident, type="clipper_export",
                             status="running", worker_id=_Queue.worker_id))
        await session.execute(update(ClipModel).where(ClipModel.id == ident)
                              .values(status="exporting", export_job_id=job))
        await session.commit()
    await jobs.handle_export(job, ident, ident, {"origin": "manual"}, _Queue())
    async with async_session() as session:
        row = await session.get(ClipModel, ident)
    return out, json.loads(out.with_suffix(".json").read_text(encoding="utf-8")), row


def _white(path: Path, at_ms: int) -> int:
    import cv2

    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, at_ms)
    ok, frame = cap.read()
    cap.release()
    assert ok, "the exported file must decode"
    # The source is one dark colour and there is no watermark: every bright
    # pixel in the frame is burned caption text.
    return int((frame.min(axis=2) > 200).sum())


@pytest.mark.parametrize("policy", ["burn", "suppress"])
async def test_mp4_probe_alternative_burn_and_suppress(tmp_path, monkeypatch, dark_source, policy):
    cfg = {"source_has_burned_captions": policy == "suppress"}
    out, side, row = await _export(f"d2mp4{policy[:3]}", tmp_path, monkeypatch,
                                   dark_source, cfg=cfg)
    burn = policy == "burn"

    assert side["caption_policy"]["action"] == policy
    assert side["render_record"]["caption_filter"] is burn
    assert side["caption_plan_state"]["outcome"] == ("burn" if burn else "suppressed")
    # The sidecar's plan is the one the file was made FROM, so a frozen replay
    # of it burns the same text; `origin` says it was built, not stored.
    assert (side["caption_plan"] is not None) is burn
    assert (side["caption_plan_state"]["origin"] == "built") is burn
    assert out.with_suffix(".ass").exists() is burn
    assert max(_white(out, 500), _white(out, 1200)) > 400 if burn else (
        _white(out, 500) == 0 and _white(out, 1200) == 0)
    # No promotion and no stored edit: the alternative is still an alternative.
    assert row.status == "exported" and row.caption_plan is None
    assert (row.is_alternative, row.rank_position, row.shadow_rank) == (True, None, None)
    assert row.warnings == ["Layout: kept from analysis"]


async def test_mp4_probe_burn_without_timed_words_warns_on_the_card(
        tmp_path, monkeypatch, dark_source):
    out, side, row = await _export("d2mp4none", tmp_path, monkeypatch, dark_source,
                                   transcript="untimed")

    assert side["caption_policy"]["action"] == "burn"
    assert side["render_record"]["caption_filter"] is False
    assert side["caption_plan_state"] == {"origin": None, "outcome": "unavailable",
                                          "reason": "no_timed_words"}
    assert _white(out, 500) == 0
    # Where the UI already looks: the card renders `clip.warnings`.
    card = clip_to_dict(row, None)
    assert any(w.startswith(captions.CAPTION_WARNING) for w in card["warnings"])
    assert row.is_alternative is True and row.rank_position is None
