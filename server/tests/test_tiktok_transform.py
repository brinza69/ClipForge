"""
Tests for the Video Transformare TikTok wizard.

Storage tests are pure (no network, no ffmpeg): they exercise the project.json
schema, the step state machine and the resume path. API tests drive the real
FastAPI app in-process via the shared `client` fixture.

Media-heavy steps (download, vision, ElevenLabs, encode) are covered by the
documented manual end-to-end run in docs/clipforge-transformation-decisions.md —
they need real network, API credits and a GPU, so they are not unit-tested here.
"""

import json

import pytest


@pytest.fixture
def tiktok_project(tmp_path, monkeypatch):
    """A throwaway project rooted in tmp_path, so tests never touch data/tiktok."""
    from config import settings
    from services.tiktok_transform import storage

    monkeypatch.setattr(type(settings), "tiktok_dir", property(lambda _self: tmp_path))
    project = storage.create_project({"url": "https://vm.tiktok.com/ZTEST/", "title": "t"})
    return storage, project


def test_create_project_uses_narator_defaults(tiktok_project):
    storage, project = tiktok_project
    s = project["settings"]
    # Voice + caption defaults must come from data/variant_presets/narator.json
    # (decision D6) so the wizard matches the rest of the rig out of the box.
    assert s["tts_voice_id"], "voice id should be populated from the Narator preset"
    assert 0.7 <= float(s["tts_speed"]) <= 1.2
    assert s["caption_template_id"]
    # Spec §7 script length window and the user's >=60s output requirement.
    assert s["min_chars"] == 1100 and s["max_chars"] == 1250
    assert s["min_output_duration_s"] == 60.0
    # Spec §15 export preset.
    assert (s["export_width"], s["export_height"], s["export_fps"]) == (1080, 1920, 60)


def test_project_layout_created_on_disk(tiktok_project):
    storage, project = tiktok_project
    paths = storage.paths(project["id"])
    assert paths["project_json"].exists()
    for key in ("candidates_dir", "selected_dir", "preview_dir"):
        assert paths[key].is_dir(), f"{key} should be created up front"
    # project.json must be valid UTF-8 JSON (Romanian text lives in it).
    data = json.loads(paths["project_json"].read_text(encoding="utf-8"))
    assert data["id"] == project["id"]


def test_all_nine_steps_start_pending(tiktok_project):
    storage, project = tiktok_project
    assert list(project["steps"]) == storage.STEP_IDS
    assert len(storage.STEP_IDS) == 9
    assert all(v["status"] == "pending" for v in project["steps"].values())


def test_mark_step_persists_and_survives_reload(tiktok_project):
    """The resume path: state lives on disk, not in memory."""
    storage, project = tiktok_project
    pid = project["id"]
    storage.mark_step(pid, "import", "running")
    storage.mark_step(pid, "import", "done")
    storage.mark_step(pid, "frames", "failed", "boom")

    reloaded = storage.load_project(pid)
    assert reloaded["steps"]["import"]["status"] == "done"
    assert reloaded["steps"]["frames"]["status"] == "failed"
    assert "boom" in reloaded["steps"]["frames"]["error"]
    assert reloaded["steps"]["script"]["status"] == "pending"


def test_mark_step_rejects_unknown_step(tiktok_project):
    storage, project = tiktok_project
    with pytest.raises(ValueError):
        storage.set_step(project, "not_a_step", "done")


def test_mark_step_does_not_clobber_concurrent_writes(tiktok_project):
    """mark_step must reload from disk, not trust a stale in-memory copy."""
    storage, project = tiktok_project
    pid = project["id"]

    stale = storage.load_project(pid)          # snapshot taken before…
    storage.mark_step(pid, "voice", "done")    # …another stage writes
    stale["title"] = "stale write"             # and the stale copy is mutated

    storage.mark_step(pid, "script", "done")   # must not resurrect the snapshot
    fresh = storage.load_project(pid)
    assert fresh["steps"]["voice"]["status"] == "done"
    assert fresh["steps"]["script"]["status"] == "done"
    assert fresh["title"] != "stale write"


def test_patch_settings_merges_and_ignores_unknown_keys(tiktok_project):
    storage, project = tiktok_project
    pid = project["id"]
    before_voice = project["settings"]["tts_voice_id"]

    updated = storage.patch_settings(pid, {"subtitle_mode": "none", "bogus": 1})
    assert updated["settings"]["subtitle_mode"] == "none"
    assert "bogus" not in updated["settings"], "unknown keys must not be persisted"
    assert updated["settings"]["tts_voice_id"] == before_voice, "untouched keys survive"


def test_list_and_delete_project(tiktok_project):
    storage, project = tiktok_project
    pid = project["id"]
    rows = storage.list_projects()
    assert [r["id"] for r in rows] == [pid]
    # The summary must stay compact — no candidate frame blobs in the list view.
    assert "frames" not in rows[0] and rows[0]["frames_selected"] == 0

    assert storage.delete_project(pid) is True
    assert storage.list_projects() == []
    assert storage.delete_project(pid) is False


def test_list_projects_skips_corrupt_project_json(tiktok_project, tmp_path):
    storage, project = tiktok_project
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "project.json").write_text("{ not json", encoding="utf-8")
    # One unreadable project must not take the whole list down.
    assert [r["id"] for r in storage.list_projects()] == [project["id"]]


# ── API ──────────────────────────────────────────────────────────────────────

async def test_list_endpoint_ok(client):
    r = await client.get("/api/tiktok/projects")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_create_rejects_invalid_url(client):
    r = await client.post("/api/tiktok/projects", json={"url": "not a url"})
    assert r.status_code == 400


async def test_unknown_project_is_404(client):
    r = await client.get("/api/tiktok/projects/doesnotexist")
    assert r.status_code == 404
