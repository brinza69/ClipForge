"""
Smoke tests for ClipForge's public API surface.

These confirm endpoints respond with the right status + a sane shape — they
do NOT exercise the heavy pipeline (no downloads, no ffmpeg). The goal is to
catch gross regressions (a router failing to import, a schema break) before
they reach the user.

Run:
    cd server
    .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/pytest tests/ -v
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_system(client):
    r = await client.get("/api/system")
    assert r.status_code == 200
    j = r.json()
    assert "gpu_available" in j
    assert "whisper_model" in j
    assert "disk_free_gb" in j


async def test_tts_engines(client):
    r = await client.get("/api/tts/engines")
    assert r.status_code == 200
    engines = r.json()["engines"]
    # xtts + elevenlabs + local_clone
    assert len(engines) >= 3
    assert {e["id"] for e in engines} >= {"xtts", "elevenlabs"}


async def test_transcript_engines(client):
    r = await client.get("/api/transcript/engines")
    assert r.status_code == 200
    j = r.json()
    ids = {e["id"] for e in j["engines"]}
    assert {"ollama", "openai", "anthropic"} <= ids


async def test_whisper_device_status(client):
    # verify=false so it doesn't pay the model-load cost
    r = await client.get("/api/transcript/device")
    assert r.status_code == 200
    j = r.json()
    assert "configured_model" in j
    assert "cuda_available" in j
    assert "medium" in j["models"] or "large-v3" in j["models"]


async def test_drive_status(client):
    r = await client.get("/api/drive-auth/status")
    assert r.status_code == 200
    assert "connected" in r.json()


async def test_sheets_config_shape(client):
    r = await client.get("/api/sheets/config")
    assert r.status_code == 200
    # Either configured (with fields) or {"configured": false}
    assert "configured" in r.json()


async def test_parallel_recent(client):
    r = await client.get("/api/parallel/recent")
    assert r.status_code == 200
    assert "runs" in r.json()


async def test_remix_recent_pagination(client):
    r = await client.get("/api/remix/recent?limit=5&offset=0")
    assert r.status_code == 200
    j = r.json()
    assert "runs" in j
    assert "total" in j


async def test_variant_presets_list(client):
    r = await client.get("/api/variant-presets")
    assert r.status_code == 200
    assert "presets" in r.json()


async def test_jobs_status_filter(client):
    # Trailing slash is the canonical form; without it FastAPI 307-redirects
    # (the frontend fetch + curl -L follow it automatically).
    r = await client.get("/api/jobs/?status=running")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_auto_requires_url_or_sheets(client):
    # No url + from_sheets=false → 400 (with a valid preset id list shape so
    # validation reaches the url check).
    r = await client.post("/api/auto", json={"variant_preset_ids": ["nope"], "from_sheets": False})
    # Either 400 (missing url) or 404 (preset not found) — both prove the
    # endpoint validates rather than 500ing.
    assert r.status_code in (400, 404)
