"""The editor's still and the exported MP4 must show the same saved edit."""
import cv2
import numpy as np
import pytest

from database import async_session
from models import ClipModel, ProjectModel
from services.clipper.ffmpeg_tools import ffmpeg_bin, run
from workers import clipper_render_plan as planning, clipper_render_output as output
from workers.clipper_preview_frame import _render_frame


@pytest.mark.parametrize("dynamic,suppress", [(False, False), (True, False), (True, True)])
async def test_editor_frame_matches_decoded_export_after_trim(
        client, tmp_path, monkeypatch, dynamic, suppress):
    source = tmp_path / "source.mp4"
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:s=320x180:r=30:d=7,drawbox=x=160:y=0:w=160:h=180:color=blue:t=fill",
         "-c:v", "libx264", "-preset", "ultrafast", str(source)], what="editor fixture")
    ident = f"editor-{dynamic}-{suppress}"
    old_export = tmp_path / "previous.mp4"
    old_export.write_bytes(b"old render retained for comparison")
    project = ProjectModel(id=ident, video_path=str(source), width=320, height=180,
                           duration=7, fps=30, clipper_settings={"dynamic_edit": dynamic,
                           "trim_silence": True, "source_has_burned_captions": suppress})
    clip = ClipModel(id=ident, project_id=ident, start_time=1, end_time=6, duration=5,
                     status="exported", export_path=str(old_export), preview_path=str(old_export),
                     caption_plan={"preset_id": "clean_minimal", "y_pct": .5,
                     "chunks": [{"text": "SECOND", "start": 1.55, "end": 2.3}]})
    async with async_session() as session:
        session.add(project)
        session.add(clip)
        await session.commit()

    async def dynamic_plan(*args, **kwargs):
        return {"duration": 5.0, "shots": [
            {"t0": 0.0, "t1": 1.5, "composition": "crop", "anchor": [50, 90],
             "rect": {"x": 0, "y": 0, "w": 100, "h": 180}},
            {"t0": 1.5, "t1": 5.0, "composition": "crop", "anchor": [270, 90],
             "rect": {"x": 220, "y": 0, "w": 100, "h": 180}}]}

    async def drops(*args):
        return [(0.5, 1.0)]

    monkeypatch.setattr(planning, "_dynamic_plan", dynamic_plan)
    monkeypatch.setattr(planning, "_dead_spans", drops)
    monkeypatch.setattr(planning, "_layout_plan", lambda *args: {})
    # Policy, ASS writing, graph construction, encoder and decoder are REAL.
    response = await client.get(f"/api/clipper/clips/{ident}/preview-frame?t=1.1")
    assert response.status_code == 200, response.text[:500]
    assert response.headers["cache-control"] == "no-store"
    assert float(response.headers["x-clip-duration"]) == 4.5
    assert response.headers["x-caption-action"] == ("suppress" if suppress else "burn")
    still = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    assert still.shape[:2] == (1920, 1080)

    work = tmp_path / "export"
    work.mkdir()
    decision = await planning._decide_render(clip, project, work)
    decision["render"] = {"preset": "ultrafast", "crf": 18}
    exported = work / "clip.mp4"
    await output.render_export(clip, project, decision, exported, src=str(source))
    cap = cv2.VideoCapture(str(exported))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, 1100)
        ok, delivered = cap.read()
        assert ok
    finally:
        cap.release()
    assert np.abs(still.astype(float) - delivered.astype(float)).mean() < 3
    white = int((still.min(axis=2) > 200).sum())
    assert (white > 500) is not suppress
    if dynamic:
        # The right-hand shot begins at INPUT 1.5s = OUTPUT 1.0s. Applying
        # sendcmd after cutting, or seeking to source start+t, would show red.
        b, g, r = still[300, 540]
        assert b > 200 and r < 30 and g < 30
    if not dynamic:
        changed = await client.patch(f"/api/clipper/clips/{ident}", json={
            "caption_preset_id": "bold_impact", "caption_plan": {
                **clip.caption_plan, "y_pct": .7, "y_pct_manual": True}})
        assert changed.status_code == 200
        saved = changed.json()["clip"]["caption_plan"]
        assert changed.json()["clip"]["export_path"] is None
        assert changed.json()["clip"]["preview_path"] is None
        assert changed.json()["clip"]["status"] == "approved"
        assert old_export.read_bytes() == b"old render retained for comparison"
        assert (await client.get(f"/api/clipper/clips/{ident}/export-file")).status_code == 404
        assert saved["preset_id"] == "bold_impact"
        assert saved["chunks"] == clip.caption_plan["chunks"]
        assert saved["y_pct"] == .7
        updated = await client.get(f"/api/clipper/clips/{ident}/preview-frame?t=1.1")
        new_still = cv2.imdecode(np.frombuffer(updated.content, np.uint8), cv2.IMREAD_COLOR)
        white_y = np.where(new_still.min(axis=2) > 200)[0]
        assert len(white_y) > 500 and white_y.mean() > 1250
        assert not np.array_equal(still, new_still)
        invalid = await client.patch(f"/api/clipper/clips/{ident}",
                                     json={"caption_preset_id": "nonexistent"})
        assert invalid.status_code == 400
    end = await client.get(f"/api/clipper/clips/{ident}/preview-frame?t=999")
    assert end.status_code == 200
    assert 4.4 < float(end.headers["x-clip-time"]) < 4.5
    for bad in ("-1", "nan", "inf"):
        invalid = await client.get(f"/api/clipper/clips/{ident}/preview-frame?t={bad}")
        assert invalid.status_code == 400


async def test_caption_edits_wait_for_the_active_export(client):
    project = ProjectModel(id="editor-busy", clipper_settings={})
    clip = ClipModel(id="editor-busy", project_id=project.id, start_time=0, end_time=3,
                     duration=3, status="exporting")
    async with async_session() as session:
        session.add(project)
        session.add(clip)
        await session.commit()
    changed = await client.patch("/api/clipper/clips/editor-busy",
                                 json={"caption_preset_id": "bold_impact"})
    assert changed.status_code == 409
    rebuilt = await client.post("/api/clipper/clips/editor-busy/regenerate", json={"what": "captions"})
    assert rebuilt.status_code == 409


@pytest.mark.parametrize("fps", [30, 60])
async def test_fractional_source_seek_selects_the_requested_export_frame(tmp_path, fps):
    source = tmp_path / "moving.mp4"
    # Every source frame has a different grey value. A static colour fixture
    # cannot see a one-frame offset, even when its PNG and MP4 look identical.
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "nullsrc=s=320x180:r=60:d=3,geq=lum='mod(N*17,220)+16':cb=128:cr=128",
         "-c:v", "libx264", "-preset", "ultrafast", str(source)], what="frame clock fixture")
    clip = ClipModel(id="fractional", start_time=.47, end_time=2.47, duration=2)
    project = ProjectModel(id="fractional", width=320, height=180)
    decision = {"fps": fps, "watermark": "", "drop": [], "dyn": None,
                "plan": {}, "ass_path": None, "caption_y": None, "cfg": {},
                "caption_policy": {"action": "suppress"}, "layout_policy": {},
                "edit_profile": None, "creator_view": None, "regime_view": None, "rhythm_view": None,
                "render": {"preset": "ultrafast", "crf": 18}}
    exported = tmp_path / "export.mp4"
    await output.render_export(clip, project, decision, exported, src=str(source))
    png = _render_frame(clip, project, decision, str(source), tmp_path, .8)
    still = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    cap = cv2.VideoCapture(str(exported))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, round(.8 * fps))
        ok, delivered = cap.read()
        assert ok
        assert np.abs(still.astype(float) - delivered.astype(float)).mean() < 3
    finally:
        cap.release()
