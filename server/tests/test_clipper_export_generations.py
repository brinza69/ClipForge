"""Preserving the generation a run is about to replace.

THE DEFECT. `replan_and_rerender._preserve` copied `exports/` to
`exports_pre_replan/` and, when that already existed, printed "already holds the
pre-replan record" and CONTINUED. Its comment argued the originals were safe
because `copytree` refuses an existing destination — true, and not the danger:

    run 1   exports/ = A  ->  exports_pre_replan/ = A, exports/ becomes B
    run 2   the backup exists, so nothing is preserved; exports/ becomes C
            and B is gone

`exports_pre_replan/` holds the generation before the FIRST run, never the one
the NEXT run replaces. It exists on all four pilots.
"""

from __future__ import annotations

import json

from services.clipper import export_generations as eg


def _project(tmp_path, *names: str):
    root = tmp_path / "proj"
    exports = root / "exports"
    exports.mkdir(parents=True)
    for i, name in enumerate(names or ("a.mp4", "a.json")):
        (exports / name).write_bytes(f"generation-1-{i}".encode())
    return root


# --- the defect, and that it is gone -----------------------------------------


def test_a_second_run_preserves_the_generation_it_is_about_to_replace(tmp_path):
    """The whole point. The first run's copy must not be mistaken for a record
    of the second run's originals."""
    root = _project(tmp_path)
    first = eg.preserve(root)
    assert first["destination"] and first["why"] is None

    # The run happens: `exports/` becomes a new generation.
    (root / "exports" / "a.mp4").write_bytes(b"generation-2")

    second = eg.preserve(root)
    assert second["why"] is None, "a second run must still preserve"
    assert second["destination"] != first["destination"]
    assert (root / first["destination"]).name != (root / second["destination"]).name

    from pathlib import Path

    assert Path(first["destination"], "a.mp4").read_bytes() == b"generation-1-0"
    assert Path(second["destination"], "a.mp4").read_bytes() == b"generation-2"


def test_nothing_is_ever_written_into_an_existing_directory(tmp_path):
    root = _project(tmp_path)
    (root / "exports_pre_replan").mkdir()
    (root / "exports_pre_replan" / "keep.txt").write_bytes(b"older")
    got = eg.preserve(root)
    assert got["why"] is None
    assert "pre_replan_02" in got["destination"]
    assert (root / "exports_pre_replan" / "keep.txt").read_bytes() == b"older"


def test_the_serial_sorts_in_the_order_the_runs_happened(tmp_path):
    root = _project(tmp_path)
    made = []
    for _ in range(3):
        got = eg.preserve(root)
        assert got["why"] is None
        made.append(got["destination"])
        (root / "exports" / "a.mp4").write_bytes(b"next")
    names = [m.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for m in made]
    assert names == sorted(names), names


def test_running_out_of_names_refuses_rather_than_overwriting(tmp_path):
    root = _project(tmp_path)
    (root / "exports_pre_replan").mkdir()
    (root / "exports_pre_replan_02").mkdir()
    got = eg.preserve(root, limit=2)
    assert got["destination"] is None and got["why"] == eg.NO_FREE_NAME


# --- what it will not call a generation --------------------------------------


def test_a_missing_exports_directory_is_not_preserved(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    got = eg.preserve(root)
    assert got["destination"] is None and got["why"] == eg.NO_SOURCE


def test_an_empty_exports_directory_is_not_a_generation(tmp_path):
    """Preserving it would create a directory that looks like a record and
    holds nothing, which is worse than having no record at all."""
    root = tmp_path / "proj"
    (root / "exports").mkdir(parents=True)
    got = eg.preserve(root)
    assert got["destination"] is None and got["why"] == eg.EMPTY_SOURCE
    assert not (root / "exports_pre_replan").exists()


# --- the inventory -----------------------------------------------------------


def test_the_inventory_travels_inside_the_copy(tmp_path):
    """Four directories of similar files are not distinguishable by looking at
    them, and an inventory kept elsewhere can drift from what it describes."""
    root = _project(tmp_path)
    got = eg.preserve(root)
    from pathlib import Path

    blob = json.loads(
        (Path(got["destination"]) / eg.INVENTORY_NAME).read_text(encoding="utf-8"))
    assert blob["preserved_from"].endswith("exports")
    assert {f["name"] for f in blob["files"]} == {"a.mp4", "a.json"}
    assert all(f["sha256"] and len(f["sha256"]) == 64 for f in blob["files"])
    assert blob["bytes"] == got["bytes"]


def test_the_hashes_are_of_the_files_that_are_there(tmp_path):
    import hashlib

    root = _project(tmp_path)
    got = eg.preserve(root)
    from pathlib import Path

    blob = json.loads(
        (Path(got["destination"]) / eg.INVENTORY_NAME).read_text(encoding="utf-8"))
    for row in blob["files"]:
        data = (Path(got["destination"]) / row["name"]).read_bytes()
        assert row["sha256"] == hashlib.sha256(data).hexdigest()


def test_an_unreadable_file_is_counted_and_not_skipped(tmp_path, monkeypatch):
    """A generation short of a file must not look complete."""
    root = _project(tmp_path)
    eg.preserve(root)
    from pathlib import Path

    real = Path.read_bytes

    def boom(self, *a, **k):
        if self.name == "a.mp4":
            raise OSError("nope")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", boom)
    got = eg.inventory(root / "exports")
    assert got["unreadable"] == 1
    assert any(f["sha256"] is None for f in got["files"])
    assert len(got["files"]) == 2, "listed, not dropped"


def test_the_existing_generations_are_reported_before_the_copy(tmp_path):
    root = _project(tmp_path)
    eg.preserve(root)
    got = eg.preserve(root)
    assert "exports_pre_replan" in got["existing"]
    assert "exports" not in got["existing"], "the live directory is not one"


# --- and both scripts use it -------------------------------------------------


def test_the_two_render_scripts_preserve_through_this_module():
    """One implementation, because the two had opposite bugs: one continued when
    the backup existed and lost the generation it was replacing, the other
    refused and could never preserve a second one."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "scripts"
    for name in ("replan_and_rerender.py", "rerender_pilots.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "export_generations as eg" in src, name
        assert "eg.preserve(" in src, name
        # And neither copies a directory itself any more.
        assert "shutil.copytree" not in src, name


def test_a_refusal_to_preserve_stops_the_run_in_both_scripts():
    """A run that proceeds without preserving has thrown away the generation it
    is about to replace, which is the whole defect."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "scripts"
    for name in ("replan_and_rerender.py", "rerender_pilots.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert 'got["why"]' in src, name
