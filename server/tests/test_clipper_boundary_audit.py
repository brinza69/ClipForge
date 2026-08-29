"""Batch R5's gate script: what it counts, and what it refuses to count.

Split from `test_clipper_boundary_completion.py` on the same seam
`test_clipper_export_audit.py` uses — that file is about the RULE, this one is
about the corpus: which records reach the report, which become findings, and
what the exit code says.

The case the whole file exists for is `test_a_record_missing_a_promised_key`.
`view.get("defects") or []` on a record that never carried the key reads as a
clean window, and a corpus of holes then aggregates into a pass. That is the
exact conclusion R0 spent a session learning to refuse, one artefact along.

`scripts/` is not a package, so the module is loaded by path.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.clipper import boundary_completion as bc


def _audit_module(monkeypatch, data_dir: Path):
    """Load the script by path, pointed at a throwaway data directory.

    Through `monkeypatch`, not `os.environ` directly: the script reads
    `CLIPFORGE_DATA_DIR` at import time, and setting it for good sent the job
    queue's own tests to a directory that disappeared with the temp path.
    """
    import importlib.util

    monkeypatch.setenv("CLIPFORGE_DATA_DIR", str(data_dir))
    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "audit_clipper_boundaries.py")
    spec = importlib.util.spec_from_file_location("audit_clipper_boundaries", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(tmp_path: Path, name: str, candidates: list[dict]) -> Path:
    analysis = tmp_path / "clipper" / name / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "candidates.json").write_text(json.dumps(candidates),
                                              encoding="utf-8")
    return tmp_path


WORDS = [{"word": "Hello", "start": 0.0, "end": 0.4},
         {"word": "there.", "start": 0.5, "end": 0.9},
         {"word": "Watch", "start": 2.0, "end": 2.3},
         {"word": "this.", "start": 2.4, "end": 2.8}]


def _scored(start: float, end: float) -> dict:
    cand = {"start": start, "end": end}
    cand["boundary_view"] = bc.boundary_view(cand, WORDS, max_s=30.0)
    return cand


# --- what reaches the report --------------------------------------------------


def test_a_clean_project_is_counted_and_passes(tmp_path, monkeypatch):
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [_scored(0.0, 1.4)]))
    row = audit._measure("p1")
    assert row["candidates"] == 1 and row["eligible"] == 1
    assert row["integrity"] == []
    assert row["source"] == "recorded"


def test_a_candidate_with_no_verdict_is_missing_never_clean(tmp_path, monkeypatch):
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [{"start": 0.0, "end": 1.4}]))
    row = audit._measure("p1")
    assert row["integrity"] == [audit.MISSING]
    assert row["eligible"] == row["ineligible"] == row["undecidable"] == 0


def test_a_record_missing_a_promised_key_is_malformed_not_defect_free(tmp_path, monkeypatch):
    """THE case. `view.get("defects") or []` on a record that never carried the
    key counts a hole as a clean window, and a corpus of holes aggregates into
    a pass."""
    cand = _scored(0.0, 1.4)
    cand["boundary_view"].pop("defects")
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [cand]))
    row = audit._measure("p1")
    assert row["integrity"] == [audit.MALFORMED]
    assert row["eligible"] == 0, "a malformed record is not an eligible window"


def test_a_defect_name_nobody_declared_is_malformed(tmp_path, monkeypatch):
    cand = _scored(0.0, 1.4)
    cand["boundary_view"]["defects"] = ["something_new"]
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [cand]))
    assert audit._measure("p1")["integrity"] == [audit.MALFORMED]


def test_an_eligible_that_is_not_a_verdict_is_malformed(tmp_path, monkeypatch):
    """True, False and None are the three answers. A string that happens to be
    truthy would be counted as eligible by anything reading it loosely."""
    cand = _scored(0.0, 1.4)
    cand["boundary_view"]["eligible"] = "yes"
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [cand]))
    assert audit._measure("p1")["integrity"] == [audit.MALFORMED]


# --- the two ways a project can carry nothing --------------------------------


def test_every_candidate_missing_is_a_rescore_not_a_bug(tmp_path, monkeypatch):
    """A project scored before R5 is a thing to do, not a bug to chase. A
    project where only SOME records are missing is the bug."""
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [{"start": 0.0, "end": 1.4},
                                                    {"start": 2.0, "end": 2.8}]))
    row = audit._measure("p1")
    assert row["integrity"].count(audit.MISSING) == row["candidates"]

    mixed = _project(tmp_path, "p2", [_scored(0.0, 1.4), {"start": 2.0, "end": 2.8}])
    other = _audit_module(monkeypatch, mixed)._measure("p2")
    assert other["integrity"].count(audit.MISSING) < other["candidates"]


def test_a_project_with_no_artefact_is_named_not_skipped(tmp_path, monkeypatch):
    (tmp_path / "clipper" / "empty").mkdir(parents=True)
    audit = _audit_module(monkeypatch, tmp_path)
    row = audit._measure("empty")
    assert row["candidates"] is None
    assert row["integrity"] == ["no_candidates_artefact"]


# --- what the numbers are for ------------------------------------------------


def test_the_refusal_reasons_are_counted_so_the_bound_costs_something_visible(
        tmp_path, monkeypatch):
    """The `next_start` bound is conservative rather than correct — the field is
    not a timeline. Counting what it refuses is how that shows up as a number
    instead of as an invisible policy."""
    cand = {"start": 2.0, "end": 2.35}
    cand["boundary_view"] = bc.boundary_view(cand, WORDS, max_s=30.0,
                                             next_start=2.6)
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [cand]))
    row = audit._measure("p1")
    assert row["refused"][bc.WOULD_OVERLAP_NEXT] == 1
    assert row["repairable"] == 0


def test_the_tail_distribution_is_collected_for_the_calibration(tmp_path, monkeypatch):
    """`TAIL_PAD_S = 0.40` is inherited from one source. These are the numbers
    it would be re-derived from."""
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1",
                                   [_scored(0.0, 1.4), _scored(0.0, 0.92)]))
    row = audit._measure("p1")
    assert sorted(row["tails"]) == [0.02, 0.5]
    assert audit._percentile(sorted(row["tails"]), 0.5) == 0.02


def test_the_technical_axis_is_counted_apart_from_eligibility(tmp_path, monkeypatch):
    """A defect a render pad fixes is not a reason to drop the moment, and
    whether it blocks publication is R7's question."""
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [_scored(0.0, 0.92)]))
    row = audit._measure("p1")
    assert row["technical"] == 1 and row["eligible"] == 1


def test_the_exit_code_reports_holes_not_defects(tmp_path, monkeypatch):
    """A window with a real problem is the finding. A window the audit could not
    check is a hole in the gate itself, and only that fails the run."""
    import sys

    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [_scored(0.2, 2.15)]))
    row = audit._measure("p1")
    assert row["ineligible"] == 1 and row["integrity"] == []

    argv = sys.argv
    try:
        sys.argv = ["audit", "p1"]
        assert audit.main() == 0, "a measured defect is a finding, not a failure"
        broken = _project(tmp_path, "p2", [{"start": 0.0, "end": 1.4}])
        sys.argv = ["audit", "p2"]
        assert _audit_module(monkeypatch, broken).main() == 2
    finally:
        sys.argv = argv
