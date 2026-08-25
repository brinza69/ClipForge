"""The disk tool's safety properties, which are the only interesting part.

It removes gigabytes of a user's downloaded video. What matters is not that it
frees space — that is arithmetic — but that it cannot take a deliverable with
it, and cannot leave a project in a state that is neither usable nor deleted.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prune_clipper.py"
_spec = importlib.util.spec_from_file_location("prune_clipper", _SCRIPT)
prune = importlib.util.module_from_spec(_spec)
sys.modules["prune_clipper"] = prune
_spec.loader.exec_module(prune)


def test_the_product_is_never_prunable():
    """Exports are the only thing in a project that cannot be recomputed from
    something else on disk."""
    assert "exports" in prune.KEEP_ALWAYS
    assert "exports" not in prune.PRUNABLE
    assert not set(prune.KEEP_ALWAYS) & set(prune.PRUNABLE)


def test_analysis_is_kept_because_it_is_expensive_and_small():
    """Megabytes against the source's gigabytes, and hours of GPU behind it."""
    assert "analysis" in prune.KEEP_ALWAYS


def test_a_dry_run_deletes_nothing(tmp_path, monkeypatch, capsys):
    project = tmp_path / "abc123456789"
    for sub in ("source", "proxy", "exports"):
        (project / sub).mkdir(parents=True)
        (project / sub / "f.bin").write_bytes(b"x" * 1024)
    monkeypatch.setattr(prune, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["prune", "--sources"])

    prune.main()

    assert (project / "source" / "f.bin").exists()
    assert "nothing was deleted" in capsys.readouterr().out


def test_it_removes_the_source_and_leaves_the_exports(tmp_path, monkeypatch):
    project = tmp_path / "abc123456789"
    for sub in ("source", "exports", "analysis"):
        (project / sub).mkdir(parents=True)
        (project / sub / "f.bin").write_bytes(b"x" * 1024)
    monkeypatch.setattr(prune, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["prune", "--sources", "--apply"])

    prune.main()

    assert not (project / "source").exists()
    assert (project / "exports" / "f.bin").exists()
    assert (project / "analysis" / "f.bin").exists()


def test_a_proxy_is_never_removed_together_with_its_source(tmp_path, monkeypatch):
    """A proxy is rebuildable FROM the source. Removing both leaves a project
    that can neither export nor be re-scored, which is deletion — and deletion
    belongs to the API, not to a disk-space script."""
    project = tmp_path / "abc123456789"
    for sub in ("source", "proxy"):
        (project / sub).mkdir(parents=True)
        (project / sub / "f.bin").write_bytes(b"x" * 1024)
    monkeypatch.setattr(prune, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["prune", "--sources", "--proxies", "--apply"])

    prune.main()

    assert not (project / "source").exists()
    assert (project / "proxy" / "f.bin").exists(), "the proxy became unrebuildable"


def test_a_proxy_is_kept_when_the_source_is_already_gone(tmp_path, monkeypatch):
    project = tmp_path / "abc123456789"
    (project / "proxy").mkdir(parents=True)
    (project / "proxy" / "f.bin").write_bytes(b"x" * 1024)
    monkeypatch.setattr(prune, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["prune", "--proxies", "--apply"])

    prune.main()

    assert (project / "proxy" / "f.bin").exists()


def test_keep_recent_protects_the_newest(tmp_path, monkeypatch):
    import os
    import time

    for i, name in enumerate(("old000000000", "new000000000")):
        p = tmp_path / name / "source"
        p.mkdir(parents=True)
        (p / "f.bin").write_bytes(b"x" * 1024)
        os.utime(tmp_path / name, (time.time() + i * 100,) * 2)
    monkeypatch.setattr(prune, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["prune", "--sources", "--keep-recent", "1", "--apply"])

    prune.main()

    assert (tmp_path / "new000000000" / "source").exists()
    assert not (tmp_path / "old000000000" / "source").exists()


# ── duplicate copies ─────────────────────────────────────────────────────────
#
# Measured on this rig: 48 GB of byte-identical duplication, from two accidents
# nobody chose. `create_project` MOVES a staged upload into the project's source
# dir and `_ingest_local` then COPIED it to source.mp4, so every project held
# its source twice; and `_uploads/batch/` keeps the download that was copied in,
# so a batch-made project has a third. The first is fixed at the source now.


def _project(tmp_path, name="abc123456789", *, extra_name="original.mp4",
             same=True):
    src = tmp_path / name / "source"
    src.mkdir(parents=True)
    body = b"video-bytes" * 500
    (src / "source.mp4").write_bytes(body)
    (src / extra_name).write_bytes(body if same else body + b"different")
    return tmp_path / name


def test_a_duplicate_is_removed_and_the_canonical_copy_kept(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(prune, "DATA", tmp_path)

    freed = prune.prune_duplicates(apply=True)

    assert (project / "source" / "source.mp4").exists()
    assert not (project / "source" / "original.mp4").exists()
    assert freed > 0


def test_a_file_that_only_looks_like_a_duplicate_is_kept(tmp_path, monkeypatch):
    """Same directory, different bytes. Deleting on a name or a size would lose
    a file; the signature covers the head, the tail and the exact length."""
    project = _project(tmp_path, same=False)
    monkeypatch.setattr(prune, "DATA", tmp_path)

    prune.prune_duplicates(apply=True)

    assert (project / "source" / "original.mp4").exists()


def test_a_dry_run_removes_no_duplicates(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(prune, "DATA", tmp_path)

    prune.prune_duplicates(apply=False)

    assert (project / "source" / "original.mp4").exists()


def test_the_staging_copy_goes_too(tmp_path, monkeypatch):
    project = _project(tmp_path)
    batch = tmp_path / "_uploads" / "batch"
    batch.mkdir(parents=True)
    (batch / "dl.mp4").write_bytes((project / "source" / "source.mp4").read_bytes())
    monkeypatch.setattr(prune, "DATA", tmp_path)

    prune.prune_duplicates(apply=True)

    assert not (batch / "dl.mp4").exists()
    assert (project / "source" / "source.mp4").exists()


def test_a_project_with_no_canonical_source_is_left_alone(tmp_path, monkeypatch):
    """Nothing to compare against means nothing is provably redundant."""
    src = tmp_path / "abc123456789" / "source"
    src.mkdir(parents=True)
    (src / "a.mp4").write_bytes(b"x" * 100)
    (src / "b.mp4").write_bytes(b"x" * 100)
    monkeypatch.setattr(prune, "DATA", tmp_path)

    prune.prune_duplicates(apply=True)

    assert len(list(src.iterdir())) == 2
