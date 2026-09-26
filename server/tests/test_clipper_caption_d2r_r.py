"""D2r: the corrections Codex asked for before closing D2 (closure-2 verdict).

K1  a plan built on demand refuses a window whose timing is incomplete, instead
    of letting the builder's fallback invent a clock for the untimed words;
K2  `outcome: burn` is not reported when the dead-air remap leaves nothing;
K3  a render applies its caption report to the CURRENT row, and an old preview
    does not overwrite an edit or a newer export;
K4  `warnings` is a list of strings at the PATCH, and a legacy non-list value
    is left alone, never iterated into keys or characters;
K5  the recipe `_plan_for_render` passes the builder is the one finalize and
    "regenerate captions" pass for the same inputs.
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest
from sqlalchemy import update

from database import async_session
from models import ClipModel, JobModel, ProjectModel, TranscriptModel
from services.clipper import captions as cap_mod
from workers import clipper_captions as captions
from workers import clipper_render_jobs as jobs
from workers import clipper_render_plan as planning

REAL = {"word": "real", "start": 0.1, "end": 0.5}
BURN = {"origin": "stored", "outcome": "burn", "reason": None}


async def _seed(ident, *, segments=None, cfg=None, caption_plan=None, warnings=None,
                start=0.0, end=3.0):
    """An alternative as `_write_clips` leaves it: no caption plan, no layout plan."""
    project = ProjectModel(id=ident, title="d2r", source_kind="file", video_path="",
                           width=320, height=180, fps=24,
                           clipper_settings={"dynamic_edit": False, "trim_silence": False,
                                             "fps": 24, **(cfg or {})})
    clip = ClipModel(id=ident, project_id=ident, title="alternative", start_time=start,
                     end_time=end, duration=end - start, transcript_text="real invented clock",
                     is_alternative=True, caption_plan=caption_plan, layout_plan=None,
                     warnings=["Layout: kept from analysis"] if warnings is None else warnings,
                     status="candidate")
    async with async_session() as session:
        session.add(project)
        session.add(clip)
        if segments is not None:
            session.add(TranscriptModel(project_id=ident, language="en", full_text="x",
                                        segments=segments))
        await session.commit()
    return await planning._load(ident)


def _texts(decision) -> str:
    return " ".join(c["text"] for c in (decision["caption_plan"] or {}).get("chunks") or [])


# ── K1: incomplete timing is refused, not filled in ───────────────────────────

async def test_k1_codex_counterexample_mixed_timed_and_untimed_is_refused(tmp_path):
    """Codex's case, reproduced: `real [0.1,0.5]` and a segment [1,3] whose text
    has no words. The builder would spread `invented [1,2]`, `clock [2,3]`."""
    segments = [{"start": 0.0, "end": 0.6, "text": "real", "words": [REAL]},
                {"start": 1.0, "end": 3.0, "text": "invented clock"}]
    # The builder's global behaviour is unchanged — it still spreads — which is
    # exactly why the on-demand path has to refuse before calling it.
    spread = cap_mod._clip_words({"start": 0.0, "end": 3.0}, {"segments": segments})
    assert [(w["word"], w["start"], w["end"]) for w in spread] == [
        ("real", 0.1, 0.5), ("invented", 1.0, 2.0), ("clock", 2.0, 3.0)]

    clip, project = await _seed("d2rmixed", segments=segments)
    decision = await planning._decide_render(clip, project, tmp_path)

    assert decision["caption_policy"]["action"] == "burn"
    assert decision["caption_plan_state"] == {"origin": None, "outcome": "unavailable",
                                              "reason": "incomplete_timing"}
    assert decision["ass_path"] is None and decision["caption_plan"] is None
    warned = captions._caption_warnings(clip.warnings, decision["caption_plan_state"])
    assert warned[0] == "Layout: kept from analysis"
    assert warned[1].startswith(captions.CAPTION_WARNING) and "incomplete_timing" in warned[1]


# `end == start` was refused here until D2r-2: closure-3 §1 admits an ASR point
# timestamp, and test_clipper_caption_d2r2_r covers it as an admission.
@pytest.mark.parametrize("bad", [
    {"word": "broken", "start": 0.9, "end": 0.7},              # end < start
    {"word": "broken", "start": 0.8, "end": float("nan")},
    {"word": "broken", "start": float("nan"), "end": 0.9},
    {"word": "broken", "start": 0.8, "end": float("inf")},
    {"word": "broken", "start": 0.8, "end": "nan"},
    {"word": "broken", "start": 0.8},                          # missing end
    {"word": "broken", "end": 0.9},                            # missing start
    {"word": "broken", "start": None, "end": 0.9},
], ids=["end_lt_start", "nan_end", "nan_start", "inf_end", "str_nan_end",
        "missing_end", "missing_start", "none_start"])
async def test_k1_an_invalid_word_in_the_window_is_refused(request, monkeypatch, bad):
    transcript = {"segments": [{"start": 0.0, "end": 1.0, "text": "real broken",
                                "words": [REAL, bad]}]}

    async def read(*_a):
        return transcript

    monkeypatch.setattr(captions, "_project_transcript", read)
    clip, project = await _seed(f"d2rbad-{request.node.callspec.id}")
    _, state = await captions._plan_for_render(clip, project, {}, "burn")
    assert state == {"origin": None, "outcome": "unavailable", "reason": "incomplete_timing"}


async def test_k1_timing_outside_the_window_does_not_refuse(tmp_path, monkeypatch):
    """Only the segments the builder reads count: an untimed segment after the
    window, and a timed word of an overlapping segment past its end, are not
    burned and so cannot invent anything."""
    transcript = {"segments": [
        {"start": 0.0, "end": 4.0, "text": "real later",
         "words": [REAL, {"word": "later", "start": 3.5, "end": 3.9}]},
        {"start": 5.0, "end": 8.0, "text": "untimed elsewhere"}]}

    async def read(*_a):
        return transcript

    monkeypatch.setattr(captions, "_project_transcript", read)
    clip, project = await _seed("d2routside")
    built, state = await captions._plan_for_render(clip, project, {}, "burn")
    assert state == {"origin": "built", "outcome": "burn", "reason": None}
    text = " ".join(c["text"] for c in built.caption_plan["chunks"]).lower()
    assert "real" in text and "later" not in text and "untimed" not in text


async def test_k1_a_stored_plan_is_never_checked_or_rebuilt(tmp_path):
    """The refusal is for the plan built on demand only; a stored plan over an
    incomplete transcript is used as it is (manual or machine)."""
    stored = {"chunks": [{"text": "SAVED", "start": 0.2, "end": 1.0, "words": []}],
              "style": {}, "x_pct": 0.5, "y_pct": 0.7, "y_pct_manual": True}
    clip, project = await _seed("d2rstored", caption_plan=stored,
                                segments=[{"start": 1.0, "end": 3.0, "text": "untimed"}])
    decision = await planning._decide_render(clip, project, tmp_path)
    assert decision["caption_plan_state"] == BURN
    assert decision["caption_plan"] == stored and "SAVED" in _texts(decision)


# ── K2: the effective result, not the intention ───────────────────────────────

def _stored(*spans):
    return {"chunks": [{"text": t, "start": s, "end": e, "words": []} for t, s, e in spans],
            "style": {}, "x_pct": 0.5, "y_pct": 0.7}


def test_k2_all_events_removed_by_the_remap_is_empty_after_remap(tmp_path):
    from types import SimpleNamespace

    state = dict(BURN)
    clip = SimpleNamespace(id="k2all", caption_plan=_stored(("GONE", 0.2, 0.8)))
    assert captions._write_ass(clip, tmp_path, [(0.0, 1.0)], None, state) is None
    assert state == {"origin": "stored", "outcome": "empty_after_remap",
                     "reason": "all_captions_in_removed_time"}
    # A demonstrated absence, not a failure: never `unavailable`, and the card
    # says what happened rather than "could not be built".
    warned = captions._caption_warnings([], state)
    assert len(warned) == 1 and "empty_after_remap" in warned[0]
    assert "unavailable" not in warned[0]


def test_k2_some_events_removed_is_still_burn(tmp_path):
    from types import SimpleNamespace

    state = dict(BURN)
    clip = SimpleNamespace(id="k2some",
                           caption_plan=_stored(("GONE", 0.2, 0.8), ("KEPT", 1.2, 1.8)))
    ass = captions._write_ass(clip, tmp_path, [(0.0, 1.0)], None, state)
    assert ass is not None and state == BURN
    text = Path(ass).read_text(encoding="utf-8")
    assert "KEPT" in text and "GONE" not in text
    assert captions._caption_warnings(["x"], state) == ["x"]


@pytest.mark.parametrize("drop,outcome", [([(0.0, 1.0)], "empty_after_remap"),
                                          ([(0.0, 0.1)], "burn")])
async def test_k2_through_the_decision(tmp_path, monkeypatch, drop, outcome):
    """The decision and so the sidecar (`render_output` copies the decision's
    state) carry the effective outcome; the plan stays the intended one."""
    plan = _stored(("ONLY", 0.2, 0.8))
    clip, project = await _seed(f"d2rdec{outcome[:5]}", caption_plan=plan,
                                cfg={"trim_silence": True})

    async def spans(*_a):
        return drop

    monkeypatch.setattr(planning, "_dead_spans", spans)
    decision = await planning._decide_render(clip, project, tmp_path)
    assert decision["caption_plan_state"]["outcome"] == outcome
    assert decision["caption_plan"] == plan
    assert (decision["ass_path"] is None) is (outcome != "burn")


# ── K3: the current row, not the snapshot ─────────────────────────────────────

class _Queue:
    worker_id = "d2r-worker"

    async def update_progress(self, *_):
        pass

    def is_cancelled(self, _):
        return False


async def _suspended_preview(monkeypatch, ident):
    """Start `handle_preview` and hold it inside the render; returns (task, go)."""
    import services.clipper.render as static_render

    rendering, go = asyncio.Event(), asyncio.Event()

    async def held(*a, **_k):
        # The file it is told to render: since D2r-2 the preview publishes by
        # renaming its own attempt file into place.
        Path(a[4]).parent.mkdir(parents=True, exist_ok=True)
        Path(a[4]).write_bytes(b"mp4")
        rendering.set()
        await go.wait()

    monkeypatch.setattr(static_render, "render_preview", held)
    monkeypatch.setattr(jobs, "_source_path", lambda _: "unused.mp4")
    task = asyncio.create_task(jobs.handle_preview(f"{ident}-p", ident, ident, {}, _Queue()))
    await asyncio.wait_for(rendering.wait(), 30)
    return task, go


async def _row(ident):
    async with async_session() as session:
        return await session.get(ClipModel, ident)


UNTIMED = [{"start": 0.0, "end": 3.0, "text": "nobody timed this"}]


async def test_k3a_a_patch_of_warnings_during_a_preview_is_kept(monkeypatch, client):
    await _seed("d2rk3a", segments=UNTIMED)
    task, go = await _suspended_preview(monkeypatch, "d2rk3a")
    r = await client.patch("/api/clipper/clips/d2rk3a", json={"warnings": ["Person note"]})
    assert r.status_code == 200, r.text
    go.set()
    await task

    warnings = (await _row("d2rk3a")).warnings
    # The person's list, plus this preview's own (still valid) caption report.
    assert warnings[0] == "Person note" and len(warnings) == 2
    assert warnings[1].startswith(captions.CAPTION_WARNING) and "no_timed_words" in warnings[1]


async def test_k3a_an_edit_of_a_caption_input_during_a_preview_is_not_overwritten(
        monkeypatch, client):
    await _seed("d2rk3i", segments=UNTIMED)
    task, go = await _suspended_preview(monkeypatch, "d2rk3i")
    plan = _stored(("SAVED", 0.2, 1.0))
    r = await client.patch("/api/clipper/clips/d2rk3i", json={"caption_plan": plan})
    assert r.status_code == 200, r.text
    go.set()
    with pytest.raises(RuntimeError):      # D2r-2 K7: the whole preview is discarded
        await task

    # The preview decided from inputs that no longer exist: its "no timed
    # words" is not a statement about the saved plan, so it is not written.
    assert (await _row("d2rk3i")).warnings == ["Layout: kept from analysis"]


async def test_k3b_an_old_preview_does_not_overwrite_a_newer_export(monkeypatch, tmp_path):
    ident = "d2rk3b"
    await _seed(ident, segments=UNTIMED)
    task, go = await _suspended_preview(monkeypatch, ident)

    # A newer export, through the real publish: claimed, rendered, published.
    job = f"{ident}-e"
    staged, out = tmp_path / "staged.mp4", tmp_path / "out.mp4"
    staged.write_bytes(b"mp4")
    staged.with_suffix(".json").write_text("{}", encoding="utf-8")
    async with async_session() as session:
        session.add(JobModel(id=job, project_id=ident, clip_id=ident, type="clipper_export",
                             status="running", worker_id=_Queue.worker_id))
        await session.execute(update(ClipModel).where(ClipModel.id == ident)
                              .values(status="exporting", export_job_id=job))
        await session.commit()
    # Published with no caption report of its own (the call shape both the D2
    # and the D2r publish accept), so the list it leaves is the row's as it was.
    assert await jobs._publish_export(job, ident, _Queue.worker_id, staged, out, None)
    exported = await _row(ident)
    assert exported.status == "exported"
    assert exported.warnings == ["Layout: kept from analysis"]

    go.set()
    with pytest.raises(RuntimeError):      # D2r-2 K7: the whole preview is discarded
        await task
    # The preview loaded the row before that export published. Its report
    # ("no timed words") must not replace the newer export's result.
    assert (await _row(ident)).warnings == ["Layout: kept from analysis"]


async def test_k3_the_export_applies_its_report_to_the_current_list(tmp_path):
    ident = "d2rk3e"
    await _seed(ident, segments=UNTIMED)
    job = f"{ident}-e"
    staged, out = tmp_path / "staged.mp4", tmp_path / "out.mp4"
    staged.write_bytes(b"mp4")
    staged.with_suffix(".json").write_text("{}", encoding="utf-8")
    async with async_session() as session:
        session.add(JobModel(id=job, project_id=ident, clip_id=ident, type="clipper_export",
                             status="running", worker_id=_Queue.worker_id))
        # Written after the export loaded its row (by a rescore, say).
        await session.execute(update(ClipModel).where(ClipModel.id == ident)
                              .values(status="exporting", export_job_id=job,
                                      warnings=["Written meanwhile"]))
        await session.commit()
    state = {"origin": None, "outcome": "unavailable", "reason": "no_timed_words"}
    assert await jobs._publish_export(job, ident, _Queue.worker_id, staged, out, None,
                                      caption_state=state)
    warnings = (await _row(ident)).warnings
    assert warnings[0] == "Written meanwhile" and len(warnings) == 2
    assert "Result of the last export" in warnings[1]


# ── K4: the shape of `warnings` ───────────────────────────────────────────────

@pytest.mark.parametrize("value,status,stored", [
    (["a", "b"], 200, ["a", "b"]),
    (None, 200, None),
    ({"a": 1}, 400, ["Layout: kept from analysis"]),
    ("ab", 400, ["Layout: kept from analysis"]),
    (["a", 3], 400, ["Layout: kept from analysis"]),
], ids=["list", "null", "dict", "string", "list_with_a_number"])
async def test_k4_patch_validates_warnings(client, value, status, stored):
    ident = f"d2rk4{status}{type(value).__name__[:3]}{len(str(value))}"
    await _seed(ident)
    r = await client.patch(f"/api/clipper/clips/{ident}", json={"warnings": value})
    assert r.status_code == status, r.text
    if status == 400:
        assert r.json()["detail"]["error"] == "invalid_warnings"
    assert (await _row(ident)).warnings == stored


@pytest.mark.parametrize("legacy", [{"a": 1}, "ab"], ids=["dict", "string"])
def test_k4_a_legacy_non_list_is_left_alone_not_iterated(legacy):
    state = {"origin": None, "outcome": "unavailable", "reason": "no_transcript"}
    # Not ["a", <warning>] and not ["a", "b", <warning>]: None = "do not write".
    assert captions._caption_warnings(legacy, state) is None
    assert captions._caption_warnings(legacy, BURN) is None


def test_k4_null_and_list_are_read_as_lists():
    state = {"origin": None, "outcome": "unavailable", "reason": "no_transcript"}
    assert len(captions._caption_warnings(None, state)) == 1
    assert captions._caption_warnings(None, BURN) == []
    assert captions._caption_warnings(["x"], BURN) == ["x"]


async def test_k4_a_legacy_dict_survives_a_preview(monkeypatch):
    await _seed("d2rk4leg", segments=UNTIMED, warnings={"legacy": "shape"})
    task, go = await _suspended_preview(monkeypatch, "d2rk4leg")
    go.set()
    await task
    assert (await _row("d2rk4leg")).warnings == {"legacy": "shape"}


# ── K5: one recipe, three callers ─────────────────────────────────────────────

@pytest.mark.parametrize("clip_preset", [None, "neon_pop"])
async def test_k5_render_regenerate_and_finalize_pass_the_same_arguments(
        monkeypatch, client, clip_preset):
    from services.clipper.clip_mutations import _project_transcript
    from workers import clipper_finalize

    ident = f"d2rk5{clip_preset or 'none'}"[:12]
    segments = [{"start": 0.0, "end": 3.0, "text": "real",
                 "words": [REAL, {"word": "words", "start": 0.6, "end": 1.2}]}]
    cfg = {"caption_preset_id": "clean_minimal", "caption_position": "top"}
    clip, project = await _seed(ident, segments=segments, cfg=cfg)
    layout = planning._layout_plan(clip, project)
    async with async_session() as session:
        await session.execute(update(ClipModel).where(ClipModel.id == ident)
                              .values(layout_plan=layout, caption_preset_id=clip_preset))
        await session.commit()
        transcript = await _project_transcript(session, ident)
    clip, project = await planning._load(ident)

    calls = []
    real = cap_mod.build_caption_plan

    def record(cand, tr, **kw):
        calls.append(((float(cand["start"]), float(cand["end"])), tr, kw))
        return real(cand, tr, **kw)

    monkeypatch.setattr(cap_mod, "build_caption_plan", record)

    await captions._plan_for_render(clip, project, planning._layout_plan(clip, project), "burn")
    r = await client.post(f"/api/clipper/clips/{ident}/regenerate", json={"what": "captions"})
    assert r.status_code == 200, r.text
    render, regenerate = calls[0], calls[1]
    assert render == regenerate

    if clip_preset is None:
        # Finalize runs before a clip row exists, so it has no clip preset to
        # read; for a clip without one the three must agree exactly.
        monkeypatch.setattr("services.clipper.layout.plan_layout", lambda *a, **k: layout)
        cand = {"start": 0.0, "end": 3.0, "text": "real invented clock"}
        clipper_finalize.plan_winners([cand], {**project.clipper_settings}, transcript,
                                      {}, [], 320, 180, "gaming")
        assert calls[2] == render
    else:
        assert render[2]["preset_id"] == clip_preset
    assert render[2]["max_words"] == 3 and render[2]["position"] == "top"
    assert all(math.isfinite(x) for x in render[0])


def test_k6_the_call_site_documents_the_alternative_cut_grid_gap():
    """K6 is documentation: the call site says the alternative's cut grid does
    not get the built words (the build runs after `_dynamic_plan`)."""
    import inspect

    src = inspect.getsource(planning._decide_render)
    at = src.index("_plan_for_render(")
    assert "cut grid" in src[max(0, at - 700):at]
