"""SCB2 (codex-verdict-next-24 §2): the editor's preview VIDEO runs the export's source-caption gate.

Everything here goes through the real `handle_preview` — the real encoder at preview size, a claimed
job row, publication — with the decision injected, as batch 2 allows while no configuration is
persisted. Before this, `handle_preview` called the renderers directly: 0 `prepare` calls, and an
untreated 175,682-byte preview (next24-check/probe_sc_video_preview.py).
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import numpy as np
import pytest

from database import async_session
from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from models import ClipModel, JobModel, ProjectModel
from services.clipper import dynamic_render, storage
from services.clipper import source_treatment as st
from services.clipper import source_treatment_render as treat
from services.clipper.render import PREVIEW_H, PREVIEW_W
from test_clipper_source_treatment import write_mask
from test_clipper_source_treatment_render import (H, RECTS, W, Synth, decode, encode_source, line_at,
                                                  mask_doc, treated_set)
from workers import clipper_preview_publish as pub
from workers import clipper_render_jobs as jobs

LAYERS = pytest.mark.parametrize("burn", [True, False], ids=["burn", "suppress"])
SCALE = PREVIEW_W / W                     # the plan's single `fit` shot: 360x640 -> 540x960


@pytest.fixture(scope="module")
def src(tmp_path_factory) -> Path:
    return encode_source(tmp_path_factory.mktemp("scb2-preview") / "plain.mp4")


class _Queue:
    worker_id = "w1"

    async def update_progress(self, *_):
        pass

    def is_cancelled(self, _):
        return False


async def _run(s: Synth, decision: dict, name: str, monkeypatch, runs: list | None = None):
    """Rows as the API and the queue leave them, then the real handler. Returns (pid, selected path)."""
    pid, job = f"stpv-{name}", f"stpv-{name}-j"
    async with async_session() as session:
        session.add(ProjectModel(id=pid, title="scb2 preview", source_kind="file", video_path=str(s.src),
                                 status="ready", processing_mode="clipping", duration=3.0,
                                 width=W, height=H, fps=30, clipper_settings={"fps": 30}))
        session.add(ClipModel(id=s.clip.id, project_id=pid, title="c", start_time=s.clip.start_time,
                              end_time=s.clip.end_time, duration=s.clip.duration, transcript_text="",
                              status="approved"))
        session.add(JobModel(id=job, project_id=pid, clip_id=s.clip.id, type="clipper_preview",
                             status="running", worker_id="w1", attempt_count=1, metadata_json="{}",
                             lease_expires_at=dt.datetime.utcnow() + dt.timedelta(minutes=10)))
        await session.commit()
    CLAIMED_ATTEMPT.set(ClaimedAttempt(job, 1, "w1"))

    async def decide(clip, project, scratch):
        return decision

    monkeypatch.setattr(jobs, "_decide_render", decide)
    monkeypatch.setattr(jobs, "_source_path", lambda _project: str(s.src))
    if runs is not None:
        real = dynamic_render.run
        monkeypatch.setattr(dynamic_render, "run", lambda cmd, **kw: (runs.append(cmd), real(cmd, **kw))[1])
    await jobs.handle_preview(job, pid, s.clip.id, {}, _Queue())
    return pid, await pub.selected_path(s.clip.id)


async def _refused(reason: str, s: Synth, decision: dict, name: str, monkeypatch, *, ran: bool = False):
    """Refused with `reason`: nothing selected, no file of this attempt or its patch left behind, and
    unless `ran`, the encoder never started."""
    runs: list = []
    with pytest.raises(st.SourceTreatmentRefused) as e:
        await _run(s, decision, name, monkeypatch, runs)
    assert e.value.reason == reason, e.value
    assert await pub.selected_path(s.clip.id) is None
    previews = storage.paths(f"stpv-{name}")["previews_dir"]
    assert not (previews.exists() and any(previews.iterdir())), sorted(p.name for p in previews.iterdir())
    if not ran:
        assert runs == [], "the command ran before the refusal"


def _y(frames: np.ndarray) -> np.ndarray:
    return frames[:, :PREVIEW_W * PREVIEW_H].reshape(-1, PREVIEW_H, PREVIEW_W).astype(np.int16)


def _inside(lid: str) -> np.ndarray:
    x0, x1, y0, y1 = RECTS[lid]
    m = np.zeros((PREVIEW_H, PREVIEW_W), bool)
    m[int(y0 * SCALE):int((y1 + 1) * SCALE), int(x0 * SCALE):int((x1 + 1) * SCALE)] = True
    return m


async def test_control_none_publishes_the_untreated_preview(src, tmp_path, monkeypatch):
    s = Synth(tmp_path, src, clip_id="stpvnone")
    _, path = await _run(s, s.decision(None), "none", monkeypatch)
    assert path and Path(path).stat().st_size > 1000


@LAYERS
async def test_an_active_treatment_is_executed_and_confirmed_on_pixels(src, tmp_path, monkeypatch, burn):
    none = Synth(tmp_path / "n", src, clip_id=f"stpvbase{int(burn)}")
    _, base = await _run(none, none.decision(None, burn=burn), f"base{int(burn)}", monkeypatch)
    s = Synth(tmp_path / "t", src, clip_id=f"stpvtreat{int(burn)}")
    d = s.decision(s.setting("erase"), burn=burn)
    runs: list = []
    _, path = await _run(s, d, f"treat{int(burn)}", monkeypatch, runs)
    assert len(runs) == 1 and path
    a, b = _y(decode(Path(base), PREVIEW_W, PREVIEW_H)), _y(decode(Path(path), PREVIEW_W, PREVIEW_H))
    assert a.shape == b.shape
    # The preview is lossy (x264 CRF, 540x960), so "only inside the rect" is a count, not bit equality.
    # Measured on this fixture: a treated frame changes 1,675+ pixels by more than 30 inside its line's
    # rect; encoding noise changes at most 97 anywhere else. An untreated preview changes none inside.
    first_k = round(s.clip.start_time * 30)
    treated = treated_set(d["source_treatment"])
    seen = set()
    for i in range(len(a)):
        k, diff = first_k + i, np.abs(a[i] - b[i]) > 30
        lid = line_at(k)
        inside = _inside(lid) if lid else np.zeros_like(diff)
        assert int(diff[~inside].sum()) <= 400, f"k {k} changed outside its rect"
        if k in treated:
            seen.add(k)
            assert int(diff[inside].sum()) >= 800, f"k {k} was not treated in the preview"
    assert seen == {k for k in treated if first_k <= k < first_k + len(a)} and seen


@pytest.mark.parametrize("what, reason", [("json", "mask_hash_mismatch"), ("png", "glyph_hash_mismatch")])
async def test_a_mask_or_glyph_changed_after_the_decision_is_refused(src, tmp_path, monkeypatch, what, reason):
    s = Synth(tmp_path, src, clip_id=f"stpvmask{what}")
    d = s.decision(s.setting("erase"))                 # decided on the mask as it was
    if what == "json":
        doc = mask_doc(s.ident, clip_id=s.clip.id)
        doc["lines"][0]["id_note"] = "changed after the decision"
        s.mask_path.write_bytes(st.canonical_bytes(doc))
    else:
        (s.mask_path.parent / "g" / "L1.png").write_bytes(b"another picture")
    await _refused(reason, s, d, f"mask{what}", monkeypatch)


async def test_a_patch_replaced_after_its_manifest_is_refused_before_the_encode(src, tmp_path, monkeypatch):
    real = treat.build_patch

    def tamper(*a, **kw):
        made = real(*a, **kw)
        path = Path(made["file"])
        path.write_bytes(bytes([16]) * path.stat().st_size)
        return made

    monkeypatch.setattr(treat, "build_patch", tamper)
    s = Synth(tmp_path, src, clip_id="stpvpatch")
    await _refused("patch_hash_mismatch", s, s.decision(s.setting("erase")), "patch", monkeypatch)


async def test_the_static_path_refuses_a_treatment(src, tmp_path, monkeypatch):
    s = Synth(tmp_path, src, clip_id="stpvstatic")
    await _refused("static_path_unsupported", s, s.decision(s.setting("erase"), dynamic=False),
                   "static", monkeypatch)


async def test_the_static_renderer_refuses_a_patch_even_past_the_gate(src, tmp_path, monkeypatch):
    """The gate refuses a static treatment in `prepare`; the renderer is the second line. Hand the
    static path a real patch anyway: it still refuses, and nothing untreated is published."""
    s = Synth(tmp_path, src, clip_id="stpvstatic2")
    dynamic = s.decision(s.setting("erase"))
    real = treat.prepare
    monkeypatch.setattr(treat, "prepare", lambda clip, project, _d, **kw: real(clip, project, dynamic, **kw))
    await _refused("static_path_unsupported", s, s.decision(s.setting("erase"), dynamic=False),
                   "static2", monkeypatch)
