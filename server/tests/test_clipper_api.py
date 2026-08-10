"""
Integration tests for the AI Stream Clipper HTTP surface.

These drive the real FastAPI app in-process through the shared `client`
fixture, so a router that fails to import or a schema that drifts from
src/types/clipper.ts shows up here rather than in the browser.

Deliberately no network and no ffmpeg: the URL-policy path is exercised with
addresses that are rejected before any DNS lookup or fetch, and the project
round-trip uses a local file so nothing is downloaded. The media-heavy stages
(download, transcribe, render) are covered by the pure unit tests on their
building blocks plus the manual run documented in
docs/ai-stream-clipper-runbook.md.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def clipper_tmp(tmp_path, monkeypatch):
    """Point the clipper artifact root at a throwaway dir so tests never write
    into the real data/clipper tree."""
    from config import settings

    monkeypatch.setattr(type(settings), "clipper_dir", property(lambda _s: tmp_path))
    return tmp_path


# ── Read-only surface ────────────────────────────────────────────────────────


async def test_projects_list_ok(client):
    r = await client.get("/api/clipper/projects")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_presets_come_from_the_shared_store(client):
    """The clipper must not ship its own preset list — it reads the same
    DEFAULT_PRESETS the rest of the app uses."""
    from services.captioner_presets import DEFAULT_PRESETS

    r = await client.get("/api/clipper/presets")
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert {p["id"] for p in presets} == set(DEFAULT_PRESETS)
    assert all(p["name"] and p["font_family"] for p in presets)


async def test_unknown_project_is_404(client):
    r = await client.get("/api/clipper/projects/doesnotexist")
    assert r.status_code == 404


async def test_unknown_clip_is_404(client):
    r = await client.get("/api/clipper/clips/doesnotexist")
    assert r.status_code == 404


# ── Source policy ────────────────────────────────────────────────────────────


async def test_preview_rejects_empty_url(client):
    r = await client.post("/api/clipper/preview", json={"url": "   "})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://10.0.0.5/video.mp4",                 # RFC1918
        "http://127.0.0.1:8420/api/health",          # the backend itself
        "file:///etc/passwd",                        # non-http scheme
    ],
)
async def test_preview_refuses_unsafe_urls(client, url):
    """The guard must answer with a reason, never fetch. A 200-with-error body
    is the contract: the form renders it inline next to the field."""
    r = await client.post("/api/clipper/preview", json={"url": url})
    assert r.status_code in (200, 400)
    body = r.json()
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
    assert detail.get("error") or detail.get("error_code"), f"no error code for {url}"


async def test_create_requires_rights_confirmation(client):
    r = await client.post(
        "/api/clipper/projects",
        json={"source_kind": "url", "url": "https://www.youtube.com/watch?v=x"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "rights_not_confirmed"


async def test_create_refuses_a_private_url_even_with_rights(client):
    """Confirming ownership does not unlock the SSRF guard."""
    r = await client.post(
        "/api/clipper/projects",
        json={
            "source_kind": "url",
            "url": "http://192.168.1.10/stream.mp4",
            "rights_confirmed": True,
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] not in ("rights_not_confirmed",)


async def test_upload_rejects_a_non_video_extension(client):
    r = await client.post(
        "/api/clipper/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsupported_upload"


# ── Full round trip ──────────────────────────────────────────────────────────


async def test_project_round_trip(client, clipper_tmp):
    """create → read → patch settings/override → delete, against the real DB."""
    staged = clipper_tmp / "staged.mp4"
    staged.write_bytes(b"\x00" * 2048)  # never decoded: analysis is not started

    created = await client.post(
        "/api/clipper/projects",
        json={
            "source_kind": "upload",
            "upload_path": str(staged),
            "title": "round trip",
            "rights_confirmed": True,
            "settings": {"clip_count": 3, "platform": "youtube_shorts"},
        },
    )
    assert created.status_code == 200, created.text
    project = created.json()
    pid = project["id"]

    try:
        assert project["source_kind"] == "upload"
        assert project["rights_confirmed"] is True
        assert project["clipper_settings"]["clip_count"] == 3
        assert project["clipper_settings"]["platform"] == "youtube_shorts"

        detail = await client.get(f"/api/clipper/projects/{pid}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["clips"] == []
        assert body["active_job"] is None

        # Detection must always be overridable (brief §16).
        patched = await client.patch(
            f"/api/clipper/projects/{pid}/settings",
            json={"content_type_override": "gaming"},
        )
        assert patched.status_code == 200
        assert "content_type_override" in patched.json()["changed"]
        assert patched.json()["project"]["content_type_override"] == "gaming"
    finally:
        deleted = await client.delete(f"/api/clipper/projects/{pid}")
        assert deleted.status_code == 200

    assert (await client.get(f"/api/clipper/projects/{pid}")).status_code == 404


async def test_content_type_override_triggers_a_rescore_when_there_is_analysis(
    client, clipper_tmp
):
    """The override picks the scoring profile AND the default layout, both of
    which are frozen onto the clips at score time. Without a re-score the
    control would visibly do nothing, so it must enqueue one — but only when
    there are cached candidates to re-score."""
    from services.clipper import storage

    staged = clipper_tmp / "override.mp4"
    staged.write_bytes(b"\x00" * 2048)
    created = await client.post(
        "/api/clipper/projects",
        json={"source_kind": "upload", "upload_path": str(staged), "rights_confirmed": True},
    )
    pid = created.json()["id"]
    try:
        # No analysis yet -> nothing to re-score, so no job is queued.
        first = await client.patch(
            f"/api/clipper/projects/{pid}/settings",
            json={"content_type_override": "gaming"},
        )
        assert first.status_code == 200
        assert first.json()["rescore_job_id"] is None

        # With cached candidates on disk the override must queue a re-score.
        storage.write_artifact(pid, "candidates", [{"start": 0.0, "end": 20.0}])
        second = await client.patch(
            f"/api/clipper/projects/{pid}/settings",
            json={"content_type_override": "podcast"},
        )
        assert second.status_code == 200
        assert second.json()["rescore_job_id"], "an override with analysis must re-score"
    finally:
        await client.delete(f"/api/clipper/projects/{pid}")


async def test_settings_are_clamped_not_trusted(client, clipper_tmp):
    """An out-of-range value from a hand-rolled API call must never reach the
    pipeline — a 10-hour max_clip_s would try to encode the whole VOD."""
    staged = clipper_tmp / "staged2.mp4"
    staged.write_bytes(b"\x00" * 2048)

    created = await client.post(
        "/api/clipper/projects",
        json={
            "source_kind": "upload",
            "upload_path": str(staged),
            "rights_confirmed": True,
            "settings": {
                "clip_count": 9999,
                "min_clip_s": 500,
                "max_clip_s": 5,
                "face_pct": 12.0,
                "platform": "myspace",
            },
        },
    )
    assert created.status_code == 200
    pid = created.json()["id"]
    try:
        cfg = created.json()["clipper_settings"]
        assert cfg["clip_count"] <= 20
        assert cfg["min_clip_s"] < cfg["max_clip_s"], "an inverted range must be repaired"
        assert 0.15 <= cfg["face_pct"] <= 0.6
        assert cfg["platform"] == "tiktok", "an unknown platform falls back to the default"
    finally:
        await client.delete(f"/api/clipper/projects/{pid}")


async def test_artifact_name_is_allowlisted(client, clipper_tmp):
    """The artifacts endpoint is reachable over HTTP, so a traversal attempt
    must be refused by name, not by luck."""
    staged = clipper_tmp / "staged3.mp4"
    staged.write_bytes(b"\x00" * 2048)
    created = await client.post(
        "/api/clipper/projects",
        json={"source_kind": "upload", "upload_path": str(staged), "rights_confirmed": True},
    )
    pid = created.json()["id"]
    try:
        bad = await client.get(f"/api/clipper/projects/{pid}/artifacts/..%2F..%2Fconfig")
        assert bad.status_code in (400, 404)
        missing = await client.get(f"/api/clipper/projects/{pid}/artifacts/signals")
        assert missing.status_code == 404, "no analysis has run yet"
    finally:
        await client.delete(f"/api/clipper/projects/{pid}")
