"""Parallel pipeline failure isolation tests."""

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.asyncio


class _Queue:
    def __init__(self):
        self.messages = []

    def is_cancelled(self, _job_id):
        return False

    async def update_progress(self, _job_id, _progress, message):
        self.messages.append(message)


class _Job:
    metadata_json = None


class _Session:
    def __init__(self, job):
        self.job = job

    async def get(self, _model, _job_id):
        return self.job

    async def commit(self):
        return None


class _SessionContext:
    def __init__(self, job):
        self.session = _Session(job)

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


async def test_one_failed_variant_does_not_discard_successful_variants(tmp_path, monkeypatch):
    import services.caption_overlays as caption_overlays
    import services.preflight as preflight
    import services.speed_match as speed_match
    import services.transcript_cleaner as transcript_cleaner
    import workers.parallel_pipeline as pipeline

    media_dir = tmp_path / "media"
    monkeypatch.setattr(
        type(pipeline.settings), "media_dir",
        property(lambda _settings: media_dir),
    )

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    job = _Job()
    queue = _Queue()

    async def fake_download(*_args, **_kwargs):
        return source

    async def fake_transcribe(*_args, **_kwargs):
        return {"full_text": "hello world", "segments": []}

    async def fake_erase(_source, erased, *_args, **_kwargs):
        erased.write_bytes(b"erased")

    async def fake_clean(*_args, **_kwargs):
        return "clean hello world"

    async def fake_voice(_text, vdir, cfg, *_args, **_kwargs):
        if cfg.get("tts_voice_id") == "bad":
            raise RuntimeError("voice provider unavailable")
        voice = vdir / "voice.wav"
        voice.write_bytes(b"voice")
        return voice

    async def fake_caption(_erased, _voice, _spoken, _cfg, output, *_args, **_kwargs):
        output.write_bytes(b"x" * 2048)
        return {"duration": 2.0}

    async def fake_descriptions(*_args, **_kwargs):
        return {"original_translated": "", "ai_generated": "description"}

    async def fake_preflight(*_args, **_kwargs):
        return None

    monkeypatch.setattr(pipeline, "_stage_download", fake_download)
    monkeypatch.setattr(pipeline, "_stage_transcribe", fake_transcribe)
    monkeypatch.setattr(pipeline, "_stage_erase", fake_erase)
    monkeypatch.setattr(pipeline, "synth_voice_from_text", fake_voice)
    monkeypatch.setattr(pipeline, "_stage_match_and_caption", fake_caption)
    monkeypatch.setattr(pipeline, "_stage_descriptions", fake_descriptions)
    monkeypatch.setattr(caption_overlays, "probe_video_dims", lambda _path: (1080, 1920))
    monkeypatch.setattr(speed_match, "probe_duration", lambda _path: 60.0)
    monkeypatch.setattr(transcript_cleaner, "clean_transcript", fake_clean)
    monkeypatch.setattr(preflight, "preflight_check", fake_preflight)
    monkeypatch.setattr(
        pipeline, "async_session", lambda: _SessionContext(job)
    )

    metadata = {
        "title": "demo",
        "erase_zone": {"x": 0, "y": 0, "w": 100, "h": 100},
        "transcript_engine": "ollama",
        "variants": [
            {"name": "good", "tts_voice_id": "good", "tts_engine": "xtts", "tts_language": "en"},
            {"name": "bad", "tts_voice_id": "bad", "tts_engine": "xtts", "tts_language": "en"},
        ],
    }

    await pipeline.handle_parallel_pipeline(
        "job-1", "project-1", None, metadata, queue,
    )

    stored = json.loads(job.metadata_json)
    assert [result["name"] for result in stored["results"]] == ["good"]
    assert stored["variant_failures"] == [{
        "index": 1,
        "name": "bad",
        "label": "bad",
        "status": "failed",
        "error": "voice provider unavailable",
    }]
    assert any("bad: failed" in message for message in queue.messages)
    assert Path(stored["results"][0]["final_path"]).is_file()
