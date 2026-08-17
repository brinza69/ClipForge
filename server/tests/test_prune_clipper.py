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
