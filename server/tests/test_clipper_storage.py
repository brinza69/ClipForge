"""
Tests for the AI Stream Clipper on-disk artifact store.

Pure filesystem tests: no DB, no ffmpeg, no network. The clipper root is
monkeypatched onto tmp_path so nothing here can touch data/clipper.
"""

import json

import pytest


@pytest.fixture
def clipper(tmp_path, monkeypatch):
    """(storage_module, project_id) rooted in tmp_path."""
    from config import settings
    from services.clipper import storage

    monkeypatch.setattr(type(settings), "clipper_dir", property(lambda _self: tmp_path))
    return storage, "proj123"


def test_ensure_dirs_creates_the_whole_tree(clipper):
    storage, pid = clipper
    storage.ensure_dirs(pid)
    p = storage.paths(pid)
    for key in (
        "root",
        "source_dir",
        "proxy_dir",
        "audio_dir",
        "frames_dir",
        "thumbs_dir",
        "analysis_dir",
        "previews_dir",
        "exports_dir",
    ):
        assert p[key].is_dir(), f"{key} should exist after ensure_dirs"
    # Idempotent — every stage calls it on entry.
    storage.ensure_dirs(pid)


def test_paths_exposes_the_contract_keys(clipper):
    storage, pid = clipper
    p = storage.paths(pid)
    assert set(p) == {
        "root", "source_dir", "proxy_dir", "proxy", "audio_dir", "audio",
        "frames_dir", "thumbs_dir", "analysis_dir", "previews_dir", "exports_dir",
        "signals", "faces", "regions", "segments", "candidates", "meta",
    }
    assert p["proxy"].name == "proxy.mp4"
    assert p["audio"].name == "speech.wav"
    for name in storage.ARTIFACT_NAMES:
        assert p[name] == p["analysis_dir"] / f"{name}.json"


def test_artifact_round_trip_is_atomic(clipper):
    storage, pid = clipper
    data = {"audio_rms": [0.1, 0.2], "peaks": [3.5], "scenes": []}
    path = storage.write_artifact(pid, "signals", data)

    assert path == storage.paths(pid)["signals"]
    assert storage.read_artifact(pid, "signals") == data
    assert json.loads(path.read_text(encoding="utf-8")) == data
    # The temp file must not survive the replace.
    assert [f.name for f in path.parent.iterdir()] == ["signals.json"]

    storage.write_artifact(pid, "signals", {"audio_rms": []})
    assert storage.read_artifact(pid, "signals") == {"audio_rms": []}


def test_artifact_accepts_a_list_payload(clipper):
    storage, pid = clipper
    storage.write_artifact(pid, "candidates", [{"start": 0.0, "end": 30.0}])
    assert storage.read_artifact(pid, "candidates") == [{"start": 0.0, "end": 30.0}]


def test_missing_artifact_reads_as_none(clipper):
    storage, pid = clipper
    assert storage.read_artifact(pid, "faces") is None
    assert storage.artifact_exists(pid, "faces") is False
    storage.write_artifact(pid, "faces", [])
    assert storage.artifact_exists(pid, "faces") is True


def test_unknown_artifact_name_is_rejected(clipper):
    storage, pid = clipper
    assert "ranker" not in storage.ARTIFACT_NAMES
    with pytest.raises(ValueError):
        storage.write_artifact(pid, "ranker", {})
    with pytest.raises(ValueError):
        storage.read_artifact(pid, "ranker")
    with pytest.raises(ValueError):
        storage.read_artifact(pid, "../../../etc/passwd")
    # The existence probe stays exception-free.
    assert storage.artifact_exists(pid, "ranker") is False


def test_corrupt_artifact_reads_as_none(clipper):
    storage, pid = clipper
    storage.ensure_dirs(pid)
    storage.paths(pid)["segments"].write_text("{ not json", encoding="utf-8")
    assert storage.read_artifact(pid, "segments") is None
    # A truncated artifact must stay recoverable by re-running the pass.
    storage.write_artifact(pid, "segments", [{"start": 0.0}])
    assert storage.read_artifact(pid, "segments") == [{"start": 0.0}]


def test_safe_join_rejects_traversal(clipper):
    storage, pid = clipper
    inside = storage.safe_join(pid, "frames", "f_0001.jpg")
    assert inside.parent == storage.paths(pid)["frames_dir"].resolve()

    for bad in ("../../etc/passwd", "..", "../secrets.json"):
        with pytest.raises(ValueError):
            storage.safe_join(pid, bad)
    with pytest.raises(ValueError):
        storage.safe_join(pid, "frames", "..", "..", "..", "escaped.txt")
    with pytest.raises(ValueError):
        storage.safe_join(pid, "C:\\Windows\\system32\\drivers\\etc\\hosts")


def test_project_id_with_path_structure_is_rejected(clipper):
    storage, _pid = clipper
    for bad in ("../other", "a/b", "a\\b", "..", ""):
        with pytest.raises(ValueError):
            storage.project_dir(bad)


def test_preview_and_export_paths_are_clip_scoped(clipper):
    storage, pid = clipper
    p = storage.paths(pid)
    assert storage.preview_path(pid, "clip7") == (p["previews_dir"] / "clip7.mp4").resolve()
    assert storage.export_path(pid, "clip7") == (p["exports_dir"] / "clip7.mp4").resolve()
    for bad in ("../../evil", "a/b", ".."):
        with pytest.raises(ValueError):
            storage.preview_path(pid, bad)
        with pytest.raises(ValueError):
            storage.export_path(pid, bad)


def test_dir_size_and_delete(clipper):
    storage, pid = clipper
    assert storage.dir_size_bytes(pid) == 0

    storage.ensure_dirs(pid)
    storage.write_artifact(pid, "meta", {"analysis_version": "1"})
    storage.paths(pid)["proxy"].write_bytes(b"\x00" * 2048)
    assert storage.dir_size_bytes(pid) > 2048

    storage.delete_project(pid)
    assert not storage.project_dir(pid).exists()
    assert storage.dir_size_bytes(pid) == 0
    # Deleting a project that was never created is a no-op, not an error.
    storage.delete_project(pid)
