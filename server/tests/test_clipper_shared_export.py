"""Normal jobs and replan probes execute the same recipe, including real media.

The media tests replace planning and the advisory reviewer, never the encoder,
the ASS parser, fingerprint writer, output probe or decoder. All DB/media lives
in the suite's disposable directory. No pilot export is rewritten.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from config import settings
from database import async_session
from models import ClipModel, ProjectModel
from services.clipper import dynamic_render, output_identity, render_input
from services.clipper import render as static_render
from services.clipper.ffmpeg_tools import ffmpeg_bin, run, video_info
from workers import clipper_render_jobs as jobs, clipper_render_output as output
from workers import clipper_render_plan as planning


def _replan_script():
    path = Path(__file__).parents[2] / "scripts" / "replan_and_rerender.py"
    spec = importlib.util.spec_from_file_location("shared_export_replan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frozen_script():
    path = Path(__file__).parents[2] / "scripts" / "rerender_pilots.py"
    spec = importlib.util.spec_from_file_location("shared_export_frozen", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _models(source: Path, identifier="shared"):
    project = ProjectModel(id=identifier, title="render integration", source_kind="file",
                           video_path=str(source), width=320, height=180, fps=24,
                           clipper_settings={})
    clip = ClipModel(id=identifier, project_id=identifier, title="fixture",
                     start_time=0.25, end_time=2.25, duration=2.0,
                     transcript_text="Caption test", selection_run_id="original-selection")
    return clip, project


def _decision(*, dynamic=True, burn=False, ass_path=None):
    return {
        "cfg": {}, "fps": 24, "watermark": "ClipForge test", "drop": [(0.75, 1.25)],
        "plan": {}, "caption_y": None, "ass_path": ass_path,
        "dyn": {"duration": 2.0, "src_w": 320, "src_h": 180,
                "style": {"push_amount": 0.0}, "hits": [],
                "shots": [{"t0": 0.0, "t1": 1.0, "composition": "crop",
                           "rect": {"x": 110, "y": 0, "w": 100, "h": 180}},
                          {"t0": 1.0, "t1": 2.0, "composition": "fit",
                           "rect": {"x": 0, "y": 0, "w": 320, "h": 180}}],
                "_review_faces": [{"t": 1.0, "boxes": [[1, 2, 3, 4]]}],
                "_face_space": {"width": 80, "height": 45, "clock": "source_requested"},
                "_rhythm": {"scenes": [1.0]}} if dynamic else None,
        "caption_policy": {"action": "burn" if burn else "suppress", "decided_by": "human"},
        "layout_policy": {"regions": "no_second_camera", "decided_by": "agent"},
        "edit_profile": None, "creator_view": None, "regime_view": None, "rhythm_view": None,
    }


@pytest.fixture
def encoded_source(tmp_path):
    if shutil.which(ffmpeg_bin()) is None:
        pytest.skip("ffmpeg is required for the real export integration test")
    paths = {}
    for audio in (False, True):
        path = tmp_path / f"source-{audio}.mp4"
        cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
               "color=c=0x203040:s=320x180:r=24:d=3"]
        if audio:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3"]
        cmd += ["-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        if audio:
            cmd += ["-c:a", "aac"]
        run(cmd + [str(path)], what="shared export source fixture")
        paths[audio] = path
    return paths


def _ass(path: Path):
    import pysubs2

    subtitles = pysubs2.SSAFile()
    subtitles.info.update({"PlayResX": "1080", "PlayResY": "1920"})
    subtitles.styles["Default"] = pysubs2.SSAStyle(
        fontname="Arial", fontsize=96, alignment=pysubs2.Alignment.MIDDLE_CENTER,
        primarycolor=pysubs2.Color(255, 255, 255), outline=2)
    # Explicitly on the trimmed output clock; events beyond the source cut are
    # not reconstructed from a later project setting.
    subtitles.events = [pysubs2.SSAEvent(start=0, end=1400, text="CAPTION TEST")]
    path.parent.mkdir(parents=True, exist_ok=True)
    subtitles.save(str(path))


class _Queue:
    async def update_progress(self, *_):
        pass

    def is_cancelled(self, _):
        return False


@pytest.mark.parametrize("dynamic,audio,burn", [
    (True, True, True), (True, False, False),
    (False, True, True), (False, False, False),
])
async def test_normal_export_and_replan_produce_the_same_actual_file(
        tmp_path, monkeypatch, encoded_source, dynamic, audio, burn):
    import cv2

    source = encoded_source[audio]
    ident = f"shared-{dynamic}-{audio}"
    clip, project = _models(source, ident)
    async with async_session() as session:
        session.add(project)
        session.add(clip)
        await session.commit()

    async def load(_):
        return clip, project

    decisions = []

    async def decide(_clip, _project, work, **_):
        ass = Path(work) / f"{clip.id}.ass"
        # A stale ASS also exists in the suppressed case. Its mere presence
        # must never make the export burn or report a caption layer.
        _ass(ass)
        d = _decision(dynamic=dynamic, burn=burn, ass_path=str(ass) if burn else None)
        decisions.append(d)
        return d

    async def review(*_):
        return {"verdict": "UNDECIDED", "findings": []}

    monkeypatch.setattr(settings, "clipper_export_crf", 26)
    monkeypatch.setattr(settings, "clipper_export_preset", "ultrafast")
    monkeypatch.setattr(settings, "clipper_vision_review", False)
    for module in (jobs, planning):
        monkeypatch.setattr(module, "_load", load)
        monkeypatch.setattr(module, "_decide_render", decide)
        monkeypatch.setattr(module, "_source_path", lambda _: str(source))
    monkeypatch.setattr(jobs, "_review", review)
    normal_dir = tmp_path / "normal"
    normal = normal_dir / f"{clip.id}.mp4"
    monkeypatch.setattr(jobs.storage, "paths", lambda _: {"exports_dir": normal_dir})
    monkeypatch.setattr(jobs.storage, "export_path", lambda *_: normal)

    await jobs.handle_export("shared-test", project.id, clip.id, {}, _Queue())
    normal_bytes = normal.read_bytes()
    original_sidecar = normal.with_suffix(".json").read_bytes()
    probe_dir = tmp_path / "probe"
    row = await _replan_script()._render_one(project.id, clip.id, output_dir=probe_dir)
    assert row["refused"] is None
    probe = Path(row["path"])
    a = json.loads(original_sidecar)
    b = json.loads(probe.with_suffix(".json").read_text(encoding="utf-8"))

    assert normal.read_bytes() == normal_bytes == probe.read_bytes()
    assert normal.with_suffix(".json").read_bytes() == original_sidecar
    assert a["input_fingerprint"] == b["input_fingerprint"]
    for body, path in ((a, normal), (b, probe)):
        assert body["fingerprint_schema"] == render_input.FINGERPRINT_SCHEMA_V2
        assert body["input_fingerprint"] == render_input.input_fingerprint(
            body, schema=body["fingerprint_schema"])
        assert output_identity.matches(body["output_identity"], path) == (True, None)
        assert body["selection_run_id"] == "original-selection"
        assert body["layout_policy"]["regions"] == "no_second_camera"
        assert body["render"]["fps"] == 24
        assert body["render"]["crf"] == 26
        assert body["render"]["preset"] == "ultrafast"
        assert body["render"]["out_w"] == 1080
        assert body["render_record"]["caption_filter"] is burn
        if burn:
            assert body["render_record"]["ass_events"]["intervals"] == [[0.0, 1.4]]
            assert body["render_record"]["ass_sha256"]
        else:
            assert body["render_record"]["ass_sha256"] is None
        if dynamic:
            assert not any(k.startswith("_") for k in body["dynamic_plan"])
        info = video_info(str(path))
        assert (info["width"], info["height"]) == (1080, 1920)
        assert info["duration"] == pytest.approx(1.5, abs=0.09)
        assert info["fps"] == pytest.approx(24)
        assert info["has_audio"] is audio
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_MSEC, 500)
        ok, frame = cap.read()
        cap.release()
        assert ok, "the completed file must decode"
        # The source is a solid dark colour. White pixels in the central text
        # zone measure the burned layer in the MP4, not merely its argv/ASS.
        white = (frame[850:1070, 80:1000].min(axis=2) > 200).sum()
        assert (white > 1000) == burn

    assert decisions[0] == decisions[1] or (
        {**decisions[0], "ass_path": None} == {**decisions[1], "ass_path": None})
    if dynamic:
        assert "_review_faces" in decisions[0]["dyn"]
    # Old probe outputs are never silently replaced by another candidate.
    with pytest.raises(FileExistsError):
        await _replan_script()._render_one(project.id, clip.id, output_dir=probe_dir)


async def test_failed_encode_keeps_the_previous_sidecar_and_does_not_claim_success(
        tmp_path, monkeypatch):
    clip, project = _models(tmp_path / "source.mp4")
    out = tmp_path / "result.mp4"
    out.write_bytes(b"previous video")
    sidecar = out.with_suffix(".json")
    sidecar.write_bytes(b"previous legacy sidecar")

    def fail(*_, **__):
        raise RuntimeError("encoder failed")

    monkeypatch.setattr(static_render, "_has_audio", lambda _: False)
    monkeypatch.setattr(dynamic_render, "render_dynamic_clip", fail)
    with pytest.raises(RuntimeError, match="encoder failed"):
        await output.render_export(clip, project, _decision(), out, src=str(project.video_path))
    assert out.read_bytes() == b"previous video"
    assert sidecar.read_bytes() == b"previous legacy sidecar"


@pytest.mark.parametrize("dynamic", [True, False])
async def test_cancelled_render_never_replaces_the_existing_generation(
        tmp_path, monkeypatch, dynamic):
    from job_queue import JobCancelledError

    clip, project = _models(tmp_path / "source.mp4")
    out = tmp_path / "result.mp4"
    out.write_bytes(b"previous video")
    sidecar = out.with_suffix(".json")
    sidecar.write_bytes(b"previous sidecar")
    monkeypatch.setattr(static_render, "_has_audio", lambda _: False)
    with pytest.raises(JobCancelledError):
        await output.render_export(clip, project, _decision(dynamic=dynamic), out,
                                   src=str(project.video_path), is_cancelled=lambda: True)
    assert out.read_bytes() == b"previous video"
    assert sidecar.read_bytes() == b"previous sidecar"


@pytest.mark.parametrize("burn", [True, False])
async def test_frozen_replay_preserves_the_recipe_when_current_settings_differ(
        tmp_path, monkeypatch, encoded_source, burn):
    import asyncio

    source = encoded_source[False]
    clip, project = _models(source, f"frozen-{burn}")
    work = tmp_path / project.id / "exports"
    work.mkdir(parents=True)
    out = work / f"{clip.id}.mp4"
    ass = out.with_suffix(".ass")
    _ass(ass)
    decision = _decision(burn=burn, ass_path=str(ass) if burn else None)
    monkeypatch.setattr(settings, "clipper_export_crf", 26)
    monkeypatch.setattr(settings, "clipper_export_preset", "ultrafast")
    await output.render_export(clip, project, decision, out, src=str(source))
    before = out.read_bytes()
    sidecar = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    # Both changing current settings and a stale ASS used to affect replay.
    monkeypatch.setattr(settings, "clipper_export_crf", 32)
    replay = _frozen_script()
    result = await asyncio.to_thread(replay._render, out.with_suffix(".json"), sidecar, False)
    assert not result.get("refused"), result
    after = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert out.read_bytes() == before
    assert after["input_fingerprint"] == sidecar["input_fingerprint"]
    assert after["render"]["crf"] == 26
    assert after["render_record"]["caption_filter"] is burn
    assert after["selection_run_id"] == sidecar["selection_run_id"]
    assert after["drop_spans"] == sidecar["drop_spans"]

    unknown = deepcopy(sidecar)
    unknown.pop("caption_policy")
    saved = out.with_suffix(".json").read_bytes()
    refused = replay._render(out.with_suffix(".json"), unknown, False)
    assert "caption_policy_missing" in refused["refused"]
    assert out.read_bytes() == before
    assert out.with_suffix(".json").read_bytes() == saved
