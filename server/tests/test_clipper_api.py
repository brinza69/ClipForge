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


def _stage(clipper_tmp, name: str):
    """Put a file where POST /upload would have put it.

    These tests used to write into the artifact root and post the ABSOLUTE
    path, which is exactly what `create_project` stopped accepting on
    2026-08-17: it took any path the client named and `.replace()`d it into
    the project, a file-move primitive handed out for free. The client
    supplies a NAME now and the server looks it up in its own staging dir,
    so staging here is what models the real two-call flow.
    """
    staging = clipper_tmp / "_uploads"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / name
    path.write_bytes(bytes(2048))   # never decoded: analysis is not started
    return path


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
    staged = _stage(clipper_tmp, "staged.mp4")

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


async def test_the_reasoning_mode_survives_create_patch_and_reaches_the_worker(
    client, clipper_tmp
):
    """The gate for Batch 1, end to end and through the real DB.

    Its predecessors — `llm_select` and `reasoning_version` — never appeared in
    `_default_settings()`, so `_normalise_settings` dropped both on every write.
    The story engine was unreachable from the API and nothing said so: the
    project was created, the run proceeded, and it quietly ran legacy.
    """
    from workers.clipper_build import _reasoning_mode

    staged = _stage(clipper_tmp, "reasoning.mp4")
    created = await client.post(
        "/api/clipper/projects",
        json={
            "source_kind": "upload",
            "upload_path": str(staged),
            "rights_confirmed": True,
            "settings": {"clip_count": 3, "reasoning_mode": "story_v1"},
        },
    )
    assert created.status_code == 200, created.text
    pid = created.json()["id"]

    try:
        stored = created.json()["clipper_settings"]
        assert stored["reasoning_mode"] == "story_v1"
        # The worker reads the stored dict, not the request, so this is the
        # half that was actually broken.
        assert _reasoning_mode(stored) == "story_v1"

        patched = await client.patch(
            f"/api/clipper/projects/{pid}/settings",
            json={"settings": {"clip_count": 3, "reasoning_mode": "legacy"}},
        )
        assert patched.status_code == 200, patched.text
        after = patched.json()["project"]["clipper_settings"]
        assert after["reasoning_mode"] == "legacy"
        assert _reasoning_mode(after) == "legacy"

        # Not selectable yet, and refused rather than downgraded to shadow.
        refused = await client.patch(
            f"/api/clipper/projects/{pid}/settings",
            json={"settings": {"reasoning_mode": "story_v2"}},
        )
        assert refused.status_code == 400
        assert refused.json()["detail"]["error"] == "reasoning_mode_unavailable"

        # ...and the refusal changed nothing.
        detail = await client.get(f"/api/clipper/projects/{pid}")
        assert detail.json()["clipper_settings"]["reasoning_mode"] == "legacy"
    finally:
        await client.delete(f"/api/clipper/projects/{pid}")


async def test_content_type_override_triggers_a_rescore_when_there_is_analysis(
    client, clipper_tmp
):
    """The override picks the scoring profile AND the default layout, both of
    which are frozen onto the clips at score time. Without a re-score the
    control would visibly do nothing, so it must enqueue one — but only when
    there are cached candidates to re-score."""
    from services.clipper import storage

    staged = _stage(clipper_tmp, "override.mp4")
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
    staged = _stage(clipper_tmp, "staged2.mp4")

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
    staged = _stage(clipper_tmp, "staged3.mp4")
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


# ── the upload path is a name, not a location ────────────────────────────────
#
# `create_project` took `upload_path` as a PATH, checked only that it existed,
# and then `.replace()`d it into the project directory: a move of any file the
# server process could reach, with the client choosing which. There is no
# authentication anywhere in ClipForge, so this was not privilege escalation
# over an already-open perimeter — it was still a filesystem primitive given
# away, and the fix is a few lines.


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", [
    "F:/ClipForge/server/config.py",           # somewhere else entirely
    "../../../config.py",                      # traversal
    "..",                                      # degenerate
    "//server/share/movie.mp4",                # UNC
])
async def test_a_file_outside_staging_cannot_be_named_as_an_upload(
        client, clipper_tmp, attempt):
    r = await client.post(
        "/api/clipper/projects",
        json={"source_kind": "upload", "upload_path": attempt,
              "title": "nope", "rights_confirmed": True},
    )
    assert r.status_code == 400, r.text
    assert "upload" in r.text


@pytest.mark.asyncio
async def test_traversal_that_lands_on_a_real_staged_file_is_still_a_name(
        client, clipper_tmp):
    """The check is not "does this resolve inside staging" applied to what the
    client sent — it is "take the name, ignore the rest". A path dressed up to
    look like it escapes and come back still reduces to its basename."""
    staged = _stage(clipper_tmp, "real.mp4")
    r = await client.post(
        "/api/clipper/projects",
        json={"source_kind": "upload",
              "upload_path": f"/etc/passwd/../../{staged.name}",
              "title": "basename", "rights_confirmed": True},
    )
    assert r.status_code == 200, r.text
    assert not staged.exists(), "the staged file should have moved into the project"


@pytest.mark.asyncio
async def test_a_staged_file_with_a_disallowed_suffix_is_refused(client, clipper_tmp):
    """The allowlist is enforced on upload; enforcing it here too means a file
    that reached staging some other way cannot become a source."""
    staging = clipper_tmp / "_uploads"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "payload.exe").write_bytes(bytes(16))

    r = await client.post(
        "/api/clipper/projects",
        json={"source_kind": "upload", "upload_path": "payload.exe",
              "title": "exe", "rights_confirmed": True},
    )
    assert r.status_code == 400, r.text
    assert "unsupported_upload" in r.text


async def test_the_edit_mode_survives_a_real_round_trip(client, clipper_tmp):
    """Batch R2's gate, over HTTP and the DB rather than in memory.

    `_normalise_settings` returning the right dict proves the function; it does
    not prove the mode is stored, read back, or left alone by an unrelated
    PATCH — which is the failure the reasoning mode actually had.
    """
    from services.clipper import edit_profiles

    staged = _stage(clipper_tmp, "staged_edit_mode.mp4")
    created = await client.post(
        "/api/clipper/projects",
        json={
            "source_kind": "upload",
            "upload_path": str(staged),
            "rights_confirmed": True,
            "settings": {"edit_mode": edit_profiles.CONTENT_AWARE_SHADOW},
        },
    )
    assert created.status_code == 200
    pid = created.json()["id"]
    try:
        assert created.json()["clipper_settings"]["edit_mode"] == (
            edit_profiles.CONTENT_AWARE_SHADOW)

        fetched = await client.get(f"/api/clipper/projects/{pid}")
        assert fetched.json()["clipper_settings"]["edit_mode"] == (
            edit_profiles.CONTENT_AWARE_SHADOW), "the mode did not survive the DB"

        # An edit to something else must not move it. The BROWSER posts the
        # whole settings object, which is why this went unseen — but the API
        # contract allows a partial PATCH, and a key that is not merged over
        # what is stored is a key that quietly reverts.
        patched = await client.patch(
            f"/api/clipper/projects/{pid}/settings",
            json={"settings": {"clip_count": 6}},
        )
        assert patched.status_code == 200
        assert patched.json()["project"]["clipper_settings"]["edit_mode"] == (
            edit_profiles.CONTENT_AWARE_SHADOW)

        # And the refused mode is refused over HTTP too, without touching what
        # is stored.
        refused = await client.patch(
            f"/api/clipper/projects/{pid}/settings",
            json={"settings": {"edit_mode": edit_profiles.CONTENT_AWARE}},
        )
        assert refused.status_code == 400
        still = await client.get(f"/api/clipper/projects/{pid}")
        assert still.json()["clipper_settings"]["edit_mode"] == (
            edit_profiles.CONTENT_AWARE_SHADOW)
    finally:
        await client.delete(f"/api/clipper/projects/{pid}")
