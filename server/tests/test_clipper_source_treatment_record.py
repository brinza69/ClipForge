"""The manifest binds what was generated to what the encode consumed (SC-addendum-v2 §6).

SC batch 2. `render_record.record` reads the patch path and the overlay from the
ARGV, hashes the patch before the encode and again after, and corroborates each
against the manifest THIS attempt wrote; any gap fails the attempt before a
sidecar exists. Driven through `render_export`, `render_dynamic_clip`,
`_publish_export` and `record` itself on the synthetic lossless source.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import update

from services.clipper import dynamic_render, render_record
from services.clipper import source_treatment as st
from services.clipper import source_treatment_render as treat
from services.clipper.source_treatment_manifest import NONE_IDENTITY
from test_clipper_source_treatment_render import (CLIP, H, PROJECT, W, Synth, encode_source,
                                                  write_sendcmd)
from workers import clipper_render_output as output


@pytest.fixture(scope="module")
def src(tmp_path_factory) -> Path:
    return encode_source(tmp_path_factory.mktemp("sc2-record") / "plain.mp4")


def _refused(reason: str, fn, *a, **kw):
    with pytest.raises(st.SourceTreatmentRefused) as e:
        fn(*a, **kw)
    assert e.value.reason == reason, e.value
    return e.value


async def _export(s: Synth, decision: dict, out: Path, attempt=None) -> dict:
    return await output.render_export(s.clip, s.project, decision, out, src=str(s.src),
                                      destination="versioned", attempt=attempt)


def _nothing_at(out: Path) -> None:
    assert not out.exists() and not out.with_suffix(".json").exists()
    assert not [p for p in out.parent.glob(".*st-*")], "an attempt's scratch was left behind"


def _cmd(s: Synth, decision: dict, patch: dict | None, out: Path) -> list[str]:
    dyn = decision["dyn"]
    return dynamic_render.build_dynamic_cmd(
        str(s.src), dyn, write_sendcmd(dyn, W, H, out.with_suffix(".cmd.txt")), None, str(out),
        start=s.clip.start_time, duration=float(dyn["duration"]), src_w=W, src_h=H,
        has_audio=False, source_patch=patch)


# ── altered, swapped, stale ──────────────────────────────────────────────────

async def test_a_patch_altered_after_the_manifest_is_refused_and_nothing_is_written(
        src, tmp_path, monkeypatch):
    s = Synth(tmp_path, src)
    real = treat.build_patch

    def tamper(*a, **kw):
        made = real(*a, **kw)
        data = bytearray(Path(made["file"]).read_bytes())
        data[100] ^= 0xFF
        Path(made["file"]).write_bytes(bytes(data))
        return made

    monkeypatch.setattr(treat, "build_patch", tamper)
    out = tmp_path / "probe" / f"{CLIP}.mp4"
    with pytest.raises(st.SourceTreatmentRefused) as e:
        await _export(s, s.decision(s.setting("erase")), out)
    assert e.value.reason == "patch_hash_mismatch"
    _nothing_at(out)


async def test_a_patch_altered_during_the_encode_is_refused(src, tmp_path, monkeypatch):
    s = Synth(tmp_path, src)
    real = dynamic_render.run

    def encode_then_tamper(cmd, **kw):
        result = real(cmd, **kw)
        patch = Path(cmd[cmd.index("rawvideo") + 8])
        patch.write_bytes(patch.read_bytes()[::-1])
        return result

    monkeypatch.setattr(dynamic_render, "run", encode_then_tamper)
    out = tmp_path / "probe" / f"{CLIP}.mp4"
    with pytest.raises(st.SourceTreatmentRefused) as e:
        await _export(s, s.decision(s.setting("erase")), out)
    assert e.value.reason == "patch_hash_mismatch"
    _nothing_at(out)


def test_another_attempts_manifest_is_refused_even_for_the_same_recipe(src, tmp_path):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    a, b = s.patch(d, "a"), s.patch(d, "b")
    cmd = _cmd(s, d, b, tmp_path / "x.mp4")  # the argv reads B's patch
    got = render_record.record(cmd, treatment_manifest=a)["source_treatment"]
    assert got["corroborated"] is False and got["refused"] == "manifest_of_another_attempt"
    got = render_record.record(cmd, treatment_manifest={**b, "manifest_sha256": a["manifest_sha256"]})
    assert got["source_treatment"]["refused"] == "manifest_hash_mismatch"
    ran = []
    real_run = dynamic_render.run
    dynamic_render.run = lambda *a, **kw: ran.append(a) or real_run(*a, **kw)
    try:
        _refused("manifest_of_another_attempt", dynamic_render.render_dynamic_clip, str(s.src),
                 d["dyn"], str(tmp_path / "y.mp4"), start=s.clip.start_time, work_dir=tmp_path,
                 src_w=W, src_h=H, has_audio=False,
                 source_patch={**b, "manifest_path": a["manifest_path"],
                               "manifest_sha256": a["manifest_sha256"]})
    finally:
        dynamic_render.run = real_run
    assert ran == [], "a record that does not bind the patch must stop the encode before ffmpeg"
    assert not (tmp_path / "y.mp4").exists()
    assert render_record.record(cmd, treatment_manifest=b)["source_treatment"]["corroborated"] is True


async def test_a_png_altered_under_the_same_mask_json_is_refused(src, tmp_path):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    png = s.mask_path.parent / "g" / "L2.png"
    png.write_bytes(png.read_bytes() + b"\x00")
    out = tmp_path / "probe" / f"{CLIP}.mp4"
    with pytest.raises(st.SourceTreatmentRefused) as e:
        await _export(s, d, out)
    assert e.value.reason == "glyph_hash_mismatch"
    _nothing_at(out)


@pytest.mark.parametrize("change, reason", [
    (lambda c: c.__setitem__(c.index("-ss") + 1, "0.300"), "record_not_corroborated"),
    (lambda c: c.__setitem__(c.index("-filter_complex") + 1,
                             c[c.index("-filter_complex") + 1].replace("eof_action=pass", "eof_action=endall")),
     "record_not_corroborated"),
    (lambda c: c.__setitem__(c.index("-s") + 1, "2x2"), "record_not_corroborated"),
    (lambda c: c.__setitem__(c.index("-pix_fmt") + 1, "yuv444p"), "record_not_corroborated"),
])
def test_an_argv_that_is_not_the_manifests_is_not_corroborated(src, tmp_path, change, reason):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    patch = s.patch(d, "p")
    cmd = _cmd(s, d, patch, tmp_path / "x.mp4")
    change(cmd)
    got = render_record.record(cmd, treatment_manifest=patch)["source_treatment"]
    assert got["corroborated"] is False and got["refused"] == reason, got
    _refused(reason, render_record.require_corroborated, {"source_treatment": got})


def test_a_patch_input_the_manifest_does_not_name_is_refused(src, tmp_path):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    patch = s.patch(d, "p")
    cmd = _cmd(s, d, patch, tmp_path / "x.mp4")
    nameless = render_record.record(cmd)["source_treatment"]
    assert nameless["refused"] == "manifest_missing" and "no manifest names it" in nameless["detail"]
    i = cmd.index(str(s.src)) + 1  # another input first: the patch is now #3, the graph reads [1:v]
    swapped = cmd[:i] + ["-f", "lavfi", "-i", "nullsrc"] + cmd[i:]
    got = render_record.record(swapped, treatment_manifest=patch)["source_treatment"]
    assert got["refused"] == "record_not_corroborated"
    gone = {**patch, "manifest_path": str(tmp_path / "missing.json")}
    assert render_record.record(cmd, treatment_manifest=gone)["source_treatment"]["refused"] == \
        "manifest_missing"
    bad = render_record.record("not an argv", treatment_manifest=patch)["source_treatment"]
    assert bad["corroborated"] is False
    _refused("record_not_corroborated", render_record.require_corroborated, {"source_treatment": bad})
    _refused("record_not_corroborated", render_record.require_corroborated, {})


def test_an_untreated_argv_keeps_its_record_exactly(src, tmp_path):
    s = Synth(tmp_path, src)
    cmd = _cmd(s, s.decision(None), None, tmp_path / "x.mp4")
    assert cmd.count("-i") == 1
    assert "source_treatment" not in render_record.record(cmd)


# ── retries, supersession, none ──────────────────────────────────────────────

async def test_a_retry_writes_its_own_manifest_and_patch(src, tmp_path):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    one = await _export(s, d, tmp_path / "a" / f"{CLIP}.mp4", {"job_id": "job-1", "nonce": "n0nce-one"})
    two = await _export(s, d, tmp_path / "b" / f"{CLIP}.mp4", {"job_id": "job-1", "nonce": "n0nce-two"})
    r1 = one["sidecar"]["render_record"]["source_treatment"]
    r2 = two["sidecar"]["render_record"]["source_treatment"]
    assert r1["corroborated"] is r2["corroborated"] is True
    assert ".st-n0nce-one" in r1["manifest_path"] and ".st-n0nce-two" in r2["manifest_path"]
    assert "n0nce-one" not in json.dumps(two["sidecar"]) and "n0nce-two" not in json.dumps(one["sidecar"])
    assert r1["manifest_sha256"] != r2["manifest_sha256"]
    assert r1["patch"]["sha256_before"] == r1["patch"]["sha256_after"] == r2["patch"]["sha256_after"]
    assert r1["identity_sha256"] == r2["identity_sha256"]
    assert one["sidecar"]["input_fingerprint"] == two["sidecar"]["input_fingerprint"]
    for out in (tmp_path / "a", tmp_path / "b"):
        assert not list(out.glob(".*st-*")), "the patch outlived its encode"


async def _seed(ident: str, job: str, worker: str) -> None:
    from database import async_session
    from models import ClipModel, JobModel, ProjectModel

    async with async_session() as session:
        session.add(ProjectModel(id=ident, title="sc2", source_kind="file", video_path="",
                                 width=W, height=H, fps=30, clipper_settings={}))
        session.add(ClipModel(id=ident, project_id=ident, title="t", start_time=0.0,
                              end_time=3.0, duration=3.0, status="candidate"))
        session.add(JobModel(id=job, project_id=ident, clip_id=ident, type="clipper_export",
                             status="running", worker_id=worker))
        await session.flush()
        await session.execute(update(ClipModel).where(ClipModel.id == ident)
                              .values(status="exporting", export_job_id=job))
        await session.commit()


async def test_a_treated_or_superseded_attempt_is_never_published(src, tmp_path):
    from workers import clipper_render_jobs as jobs

    s = Synth(tmp_path, src)
    treated = await _export(s, s.decision(s.setting("erase")), tmp_path / "t" / f"{CLIP}.mp4",
                            {"job_id": "old", "nonce": "n"})
    out = tmp_path / "exports" / "sc2pub.mp4"
    out.parent.mkdir()
    for suffix in (".mp4", ".json"):
        out.with_suffix(suffix).write_bytes(b"the original " + suffix.encode())
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.parent.iterdir()}
    staged = out.with_name(".sc2pub.old.mp4")
    staged.write_bytes(b"treated mp4")
    staged.with_suffix(".json").write_text(json.dumps(treated["sidecar"]), encoding="utf-8")

    await _seed("sc2pub", "new", "w")
    # superseded: not the clip's current attempt — nothing moves
    assert await jobs._publish_export("old", "sc2pub", "w", staged, out, None) is False
    # current, but the staged sidecar carries a treatment — refused, nothing moves
    await _seed_current("sc2pub", "old")
    with pytest.raises(st.SourceTreatmentRefused) as e:
        await jobs._publish_export("old", "sc2pub", "w", staged, out, None)
    assert e.value.reason == "treated_export_not_promoted"
    assert staged.exists() and staged.with_suffix(".json").exists()
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.parent.iterdir()
             if not p.name.startswith(".sc2pub.old")}
    assert after == before
    # and an unreadable staged sidecar proves nothing: refused the same way
    staged.with_suffix(".json").write_text("{not json", encoding="utf-8")
    with pytest.raises(st.SourceTreatmentRefused):
        await jobs._publish_export("old", "sc2pub", "w", staged, out, None)


async def _seed_current(ident: str, job: str) -> None:
    from database import async_session
    from models import ClipModel, JobModel

    async with async_session() as session:
        session.add(JobModel(id=job, project_id=ident, clip_id=ident, type="clipper_export",
                             status="running", worker_id="w"))
        await session.flush()
        await session.execute(update(ClipModel).where(ClipModel.id == ident)
                              .values(export_job_id=job))
        await session.commit()


@pytest.mark.parametrize("identity, labelled", [
    ({"schema": st.IDENTITY_SCHEMA, "treatment": "none", "mask_sha256": "0" * 64}, None),
    (None, None),
    (NONE_IDENTITY, {"treatment": "erase"}),
])
def test_the_publish_guard_refuses_anything_it_cannot_prove_is_none(tmp_path, identity, labelled):
    body = {"fingerprint_schema": "clipper_render_input_v3", "source_treatment_identity": identity}
    if labelled is not None:
        body["source_treatment"] = labelled
    path = tmp_path / "s.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    _refused("treated_export_not_promoted", treat.refuse_treated_publish, path)
    path.write_text(json.dumps({**body, "source_treatment_identity": NONE_IDENTITY,
                                "source_treatment": {"treatment": "none"}}), encoding="utf-8")
    treat.refuse_treated_publish(path)
    path.write_text("[]", encoding="utf-8")
    _refused("treated_export_not_promoted", treat.refuse_treated_publish, path)


async def test_none_writes_no_manifest_and_no_patch_input(src, tmp_path):
    s = Synth(tmp_path, src)
    out = tmp_path / "probe" / f"{CLIP}.mp4"
    got = await output.render_export(s.clip, s.project, s.decision(None), out, src=str(s.src))
    body = got["sidecar"]
    assert "source_treatment" not in body["render_record"]
    assert body["source_treatment_identity"] == NONE_IDENTITY
    assert body["source_treatment"] == {"treatment": "none", "decided_by": "default",
                                        "scope": "default", "active": False}
    assert not list(out.parent.glob(".*st-*")) and not list(tmp_path.glob("**/patch.yuv"))
    assert PROJECT == s.project.id


def test_a_manifest_the_identity_cannot_be_taken_from_is_not_corroborated(src, tmp_path):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    patch = s.patch(d, "p")
    cmd = _cmd(s, d, patch, tmp_path / "x.mp4")
    got = render_record.record(cmd, treatment_manifest={k: v for k, v in patch.items()
                                                        if k != "resolved"})["source_treatment"]
    assert got["corroborated"] is False and got["refused"] == "record_not_corroborated"
