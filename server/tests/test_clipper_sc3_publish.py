"""SC3 (codex-verdict-next-33 §3–§4): blur — and only blur — is published by the normal export.

`handle_export` passes `PUBLISH_BLUR`: `check_destination` then accepts the export's own staged file for a
blur-only treatment, and `_publish_export` accepts the staged sidecar only when its label AND its v3
identity are an active blur on every segment. Erase, however it got into the decision, is refused before the
encode and never renamed over the original; scripts (no destination) stay refused as before. A changed
treatment or layer makes an older preview's inputs differ, so it is discarded.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from sc3_fixtures import make_clip, row
from services.clipper import source_treatment as st
from services.clipper import source_treatment_render as treat
from services.clipper import source_treatment_store as store
from services.clipper import storage
from test_clipper_source_treatment_render import Synth, encode_source



@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return encode_source(tmp_path_factory.mktemp("sc3-publish") / "plain.mp4")


def _sidecar(tmp_path: Path, *, label="blur", ident="blur", segs=("blur", "blur"), active=True,
             patch=True) -> Path:
    body = {"fingerprint_schema": "clipper_render_input_v3",
            "source_treatment": {"treatment": label, "decided_by": "human", "scope": "clip", "active": active}}
    if ident == "none":
        body["source_treatment_identity"] = {"schema": st.IDENTITY_SCHEMA, "treatment": "none"}
    else:
        body["source_treatment_identity"] = {
            "schema": st.IDENTITY_SCHEMA, "treatment": ident, "source": {}, "mask_sha256": "a" * 64,
            "glyphs": [], "params": {}, "window": {}, "pts_offset": 0, "frames": [], "overlay_filter": "",
            "patch": {"sha256": "b" * 64},
            "per_line": [{"line_id": f"L{i}", "k_from": i, "k_to": i + 1, "treatment": t}
                         for i, t in enumerate(segs)]}
        if not patch:  # an identity that names blur but does not bind a patch: unreadable, not active
            del body["source_treatment_identity"]["patch"]
    p = tmp_path / "staged.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def test_the_publish_guard_lets_through_an_active_blur_only_when_asked(tmp_path):
    treat.refuse_treated_publish(_sidecar(tmp_path), blur=True)
    with pytest.raises(st.SourceTreatmentRefused):                        # scripts, and the old default
        treat.refuse_treated_publish(_sidecar(tmp_path))


@pytest.mark.parametrize("kw", [
    {"label": "erase", "ident": "erase", "segs": ("erase",)},
    {"segs": ("blur", "erase")},                                          # an erase hidden in a segment
    {"label": "blur", "ident": "erase", "segs": ("erase",)},              # the label says blur, the file doesn't
    {"active": False},
    {"patch": False},
], ids=["erase", "mixed", "label_lies", "inactive", "unbound_identity"])
def test_the_publish_guard_refuses_anything_but_blur(tmp_path, kw):
    with pytest.raises(st.SourceTreatmentRefused) as e:
        treat.refuse_treated_publish(_sidecar(tmp_path, **kw), blur=True)
    assert e.value.reason == "treated_export_not_promoted"


def test_an_unreadable_sidecar_is_never_published(tmp_path):
    p = tmp_path / "staged.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(st.SourceTreatmentRefused):
        treat.refuse_treated_publish(p, blur=True)


def _blur_resolved(*segs) -> dict:
    return {"treatment": "blur", "per_line": [{"treatment": t} for t in segs]}


def test_publish_blur_accepts_only_the_exports_own_staged_file():
    project = SimpleNamespace(id="sc3-dest")
    exports = storage.paths(project.id)["exports_dir"]
    treat.check_destination(exports / ".c.job-1a2b3c4d.mp4", project, destination=treat.PUBLISH_BLUR,
                            resolved=_blur_resolved("blur"))
    for out in (exports / "c.mp4", exports / "sct1" / "c.mp4", exports.parent / ".c.x.mp4"):
        with pytest.raises(st.SourceTreatmentRefused) as e:
            treat.check_destination(out, project, destination=treat.PUBLISH_BLUR, resolved=_blur_resolved("blur"))
        assert e.value.reason == "destination_not_versioned"
    with pytest.raises(st.SourceTreatmentRefused) as e:
        treat.check_destination(exports / ".c.job-1.mp4", project, destination=treat.PUBLISH_BLUR,
                                resolved=_blur_resolved("blur", "erase"))
    assert e.value.reason == "erase_not_offered"
    with pytest.raises(st.SourceTreatmentRefused) as e:                  # a script: no destination
        treat.check_destination(exports / ".c.job-1.mp4", project, destination=None,
                                resolved=_blur_resolved("blur"))
    assert e.value.reason == "treated_export_not_promoted"


class _Queue:
    worker_id = "w1"

    async def update_progress(self, *_):
        pass

    def is_cancelled(self, _):
        return False


async def _export(src: Path, tmp_path: Path, monkeypatch, treatment: str):
    """The real handle_export on a clip whose decision carries the store's treatment, the way
    `_decide_render` merges it. Returns (clip row after, project id)."""
    from database import async_session
    from models import ClipModel, JobModel
    from workers import clipper_render_jobs as jobs

    pid, cid, sha = await make_clip(src, status="exporting")
    job = cid + "-j"
    async with async_session() as s:
        s.add(JobModel(id=job, project_id=pid, clip_id=cid, type="clipper_export", status="running",
                       worker_id="w1", attempt_count=1, metadata_json="{}",
                       lease_expires_at=dt.datetime.utcnow() + dt.timedelta(minutes=10)))
        (await s.get(ClipModel, cid)).export_job_id = job
        await s.commit()
    CLAIMED_ATTEMPT.set(ClaimedAttempt(job, 1, "w1"))
    synth = Synth(tmp_path, src, clip_id=cid)
    setting = {"treatment": treatment, "decided_by": "human", "mask_sha256": sha}

    async def decide(clip, project, scratch, **_):
        d = synth.decision(setting)
        if treatment == "blur":
            got = store.validate(clip, project, str(src), sha)            # what `for_render` returns
            d["source_treatment"], d["source_treatment_inputs"] = got["resolved"], got["inputs"]
        else:  # an erase that reached a decision: the store never gives one, a script might
            d["source_treatment_inputs"]["mask_path"] = str(store.mask_path(pid, sha))
            d["source_treatment_inputs"]["params_path"] = str(store.params_path(pid))
        return d

    monkeypatch.setattr(jobs, "_decide_render", decide)
    monkeypatch.setattr(jobs, "_source_path", lambda _p: str(src))
    return jobs, job, pid, cid


async def test_a_blur_export_is_published_through_the_normal_path(src, tmp_path, monkeypatch):
    jobs, job, pid, cid = await _export(src, tmp_path, monkeypatch, "blur")
    await jobs.handle_export(job, pid, cid, {}, _Queue())
    clip = await row(cid)
    assert clip.status == "exported" and clip.export_path == str(storage.export_path(pid, cid))
    side = json.loads(Path(clip.export_path).with_suffix(".json").read_text(encoding="utf-8"))
    assert side["source_treatment"]["treatment"] == "blur"
    assert side["source_treatment_identity"]["treatment"] == "blur"
    assert {s["treatment"] for s in side["source_treatment_identity"]["per_line"]} == {"blur"}


async def test_an_erase_is_refused_before_the_encode_and_nothing_is_published(src, tmp_path, monkeypatch):
    jobs, job, pid, cid = await _export(src, tmp_path, monkeypatch, "erase")
    with pytest.raises(st.SourceTreatmentRefused) as e:
        await jobs.handle_export(job, pid, cid, {}, _Queue())
    assert e.value.reason == "erase_not_offered"
    assert not storage.export_path(pid, cid).exists()
    exports = storage.paths(pid)["exports_dir"]
    assert not exports.exists() or not any(p.suffix == ".mp4" for p in exports.iterdir())
    shutil.rmtree(tmp_path, ignore_errors=True)


async def test_a_changed_treatment_or_layer_changes_the_previews_inputs(src):
    from database import async_session
    from models import ProjectModel
    from workers.clipper_preview_publish import _preview_inputs

    pid, cid, sha = await make_clip(src)
    async with async_session() as s:
        project = await s.get(ProjectModel, pid)
    clip = await row(cid)
    seen = _preview_inputs(clip, project)
    clip.source_caption_treatment = {"treatment": "blur", "decided_by": "human", "mask_sha256": sha}
    assert _preview_inputs(clip, project) != seen
    moved = _preview_inputs(clip, project)
    clip.caption_layer = "burn"
    assert _preview_inputs(clip, project) != moved


async def test_a_planner_fallback_to_static_is_refused_never_delivered_untreated(src, tmp_path, monkeypatch):
    """next-34 R3: the render-time check stays — a dynamic plan that fails falls back to static, and the
    stored blur then refuses the export instead of publishing the untreated source."""
    jobs, job, pid, cid = await _export(src, tmp_path, monkeypatch, "blur")
    real = jobs._decide_render

    async def fallback(clip, project, scratch, **kw):
        d = await real(clip, project, scratch, **kw)
        d["dyn"] = None
        return d

    monkeypatch.setattr(jobs, "_decide_render", fallback)
    with pytest.raises(st.SourceTreatmentRefused) as e:
        await jobs.handle_export(job, pid, cid, {}, _Queue())
    assert e.value.reason == "static_path_unsupported"
    assert not storage.export_path(pid, cid).exists()
