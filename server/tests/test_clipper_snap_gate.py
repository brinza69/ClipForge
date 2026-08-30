"""Batch R5a's gate script, exercised through `main()` rather than read.

Its exit code has failed at four layers in a row, each time the same way: a
thing that could not be read disappears, and the result looks like a pass. It
happened in the boundary audit's verdict, in the denominator of a report I
summed by hand, in this script's own candidate filter, and in its `--json`
branch, which returned 0 unconditionally. Reading the branches is how each of
those survived, so these run the entry point and check what it returns.

The transcript comes from the database, which a unit test has no business
touching, so `_load` is replaced with a synthetic one. Everything else — the
artefact on disk, the snap, the gate — is the real code.

`scripts/` is not a package, so the module is loaded by path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Two clean sentences. A cut at 1.3 lands inside "three" and can be pushed out.
WORDS = [{"word": "one", "start": 0.0, "end": 0.4},
         {"word": "two", "start": 0.5, "end": 0.9},
         {"word": "three.", "start": 1.0, "end": 1.6},
         {"word": "four", "start": 2.0, "end": 2.4},
         {"word": "five.", "start": 2.5, "end": 3.2}]


def _module(monkeypatch, data_dir: Path):
    import importlib.util

    monkeypatch.setenv("CLIPFORGE_DATA_DIR", str(data_dir))
    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "measure_boundary_snap.py")
    spec = importlib.util.spec_from_file_location("measure_boundary_snap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def _load(_project_id: str):
        return ({"segments": [{"start": 0.0, "end": 3.2, "words": WORDS}]},
                0.2, 10.0, 3.2)

    monkeypatch.setattr(module, "_load", _load)
    return module


def _project(tmp_path: Path, name: str, candidates: list) -> Path:
    analysis = tmp_path / "clipper" / name / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "candidates.json").write_text(json.dumps(candidates),
                                              encoding="utf-8")
    return tmp_path


def _run(module, *argv: str) -> int:
    saved = sys.argv
    try:
        sys.argv = ["measure_boundary_snap", *argv]
        return module.main()
    finally:
        sys.argv = saved


CLEAN = [{"start": 0.0, "end": 1.6}]
TRUNCATED = [{"start": 0.0, "end": 1.3}]


def test_a_corpus_with_nothing_wrong_passes(tmp_path, monkeypatch):
    """The gate has to be capable of returning 0, or it is not a gate."""
    module = _module(monkeypatch, _project(tmp_path, "p", CLEAN))
    assert _run(module, "p") == 0


def test_a_truncation_the_snap_clears_still_passes(tmp_path, monkeypatch, capsys):
    """A window that ended inside a word and can be pushed out is the batch
    working, not the gate failing."""
    module = _module(monkeypatch, _project(tmp_path, "p", TRUNCATED))
    assert _run(module, "p") == 0
    assert "truncated    1 ->    0" in capsys.readouterr().out


def test_a_project_that_is_not_there_fails_the_run(tmp_path, monkeypatch, capsys):
    """THE case. Returning None for a missing artefact let `main` drop it with
    an `if r`, so the project left the run entirely: over six clones, one clone
    written wrong disappears and the other five come back green."""
    module = _module(monkeypatch, _project(tmp_path, "real", CLEAN))
    assert _run(module, "real", "missing") == 2
    printed = capsys.readouterr().out
    assert "no_candidates_artefact" in printed
    assert "1 project(s) could not be measured at all" in printed


def test_every_project_asked_for_comes_back_with_a_row(tmp_path, monkeypatch):
    """The count is part of the answer, or the report is about a corpus somebody
    else chose."""
    module = _module(monkeypatch, _project(tmp_path, "real", CLEAN))
    rows = [module._measure(name) for name in ("real", "missing", "gone")]
    assert [r["project"] for r in rows] == ["real", "missing", "gone"]


def test_json_mode_returns_the_same_code_as_the_report(tmp_path, monkeypatch):
    """`--json` returned 0 unconditionally: a machine-readable report of a
    corpus nobody could measure, exiting green. The code belongs to the run, not
    to how it is printed."""
    module = _module(monkeypatch, _project(tmp_path, "real", CLEAN))
    assert _run(module, "real", "missing") == 2
    assert _run(module, "real", "missing", "--json") == 2
    assert _run(module, "real") == 0
    assert _run(module, "real", "--json") == 0


def test_an_entry_that_is_not_a_record_fails_the_run(tmp_path, monkeypatch,
                                                     capsys):
    module = _module(monkeypatch, _project(tmp_path, "p", CLEAN + ["junk", None]))
    assert _run(module, "p") == 2
    assert "entries are not records" in capsys.readouterr().out
