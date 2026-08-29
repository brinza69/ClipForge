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


def test_blocking_has_exactly_one_correct_value(tmp_path, monkeypatch):
    """`blocking` is DERIVED — the found defects that are in `BLOCKING` — so a
    subset check let both halves through: an OMITTED blocker, which reads as an
    eligible window, and a TECHNICAL defect promoted into it, which refuses a
    window for something a render pad fixes."""
    claimed = _scored(0.0, 1.4)
    claimed["boundary_view"]["blocking"] = [bc.END_IN_WORD]
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [claimed]))
    assert audit._measure("p1")["integrity"] == [audit.MALFORMED], "claimed"

    omitted = _scored(0.2, 2.15)
    assert omitted["boundary_view"]["blocking"], "the fixture must have one"
    omitted["boundary_view"]["blocking"] = []
    audit = _audit_module(monkeypatch, _project(tmp_path, "p2", [omitted]))
    assert audit._measure("p2")["integrity"] == [audit.MALFORMED], "omitted"

    promoted = _scored(0.0, 0.92)
    assert bc.CLIPPED_RELEASE in promoted["boundary_view"]["defects"]
    promoted["boundary_view"]["blocking"] = [bc.CLIPPED_RELEASE]
    audit = _audit_module(monkeypatch, _project(tmp_path, "p3", [promoted]))
    assert audit._measure("p3")["integrity"] == [audit.MALFORMED], "promoted"


def test_a_measurement_that_is_not_finite_is_malformed(tmp_path, monkeypatch):
    """NaN and infinity survive every arithmetic check and poison a median. The
    tail distribution is what `TAIL_PAD_S` would be calibrated from."""
    for value in (float("nan"), float("inf"), float("-inf")):
        cand = _scored(0.0, 1.4)
        cand["boundary_view"]["measurements"]["tail_s"] = value
        audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [cand]))
        assert audit._measure("p1")["integrity"] == [audit.MALFORMED], repr(value)


def test_an_unknown_in_the_defect_list_is_malformed(tmp_path, monkeypatch):
    """Each list against its OWN vocabulary. Checking the union let an unknown
    appear as a defect — the one distinction the batch is built on."""
    cand = _scored(0.0, 1.4)
    cand["boundary_view"]["defects"] = [bc.NO_PUNCTUATION]
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [cand]))
    assert audit._measure("p1")["integrity"] == [audit.MALFORMED]


def test_a_measurement_that_is_not_a_number_is_malformed(tmp_path, monkeypatch):
    """A string sorts, compares and averages as if it meant something. The tail
    distribution is what `TAIL_PAD_S` would be calibrated from."""
    cand = _scored(0.0, 1.4)
    cand["boundary_view"]["measurements"]["tail_s"] = "0.5"
    audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [cand]))
    assert audit._measure("p1")["integrity"] == [audit.MALFORMED]


def test_a_repair_with_no_reason_for_refusing_is_malformed(tmp_path, monkeypatch):
    cand = _scored(2.0, 2.35)
    cand["boundary_view"]["repair"] = {"kind": None}
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


def test_an_integer_is_not_a_verdict(tmp_path, monkeypatch):
    """`x in (True, False, None)` is a trap: `1 == True` and `0 == False` in
    Python, so an integer sailed through the strict check and was counted as
    eligible. Identity and type, not equality."""
    for value in (1, 0, 1.0):
        cand = _scored(0.0, 1.4)
        cand["boundary_view"]["eligible"] = value
        audit = _audit_module(monkeypatch, _project(tmp_path, "p1", [cand]))
        row = audit._measure("p1")
        assert row["integrity"] == [audit.MALFORMED], f"{value!r} passed as a verdict"
        assert row["eligible"] == 0


def test_an_entry_that_is_not_a_record_is_counted_not_filtered(tmp_path,
                                                               monkeypatch):
    """Filtering junk out of the file dropped it before the denominator, so a
    file half full of it printed a verdict for the other half and the junk
    simply was not there. It occupies a slot; a slot nobody could read is a hole
    in the corpus, not one fewer candidate."""
    audit = _audit_module(monkeypatch, _project(
        tmp_path, "p1", [_scored(0.0, 1.4), "not a record", None]))
    row = audit._measure("p1")
    assert row["candidates"] == 3
    assert row["integrity"] == [audit.NOT_A_CANDIDATE] * 2
    assert row["eligible"] == 1


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


def test_an_unreadable_candidate_does_not_vanish_from_the_denominator(
        tmp_path, monkeypatch, capsys):
    """A record the audit could not read was skipped before it reached any of
    the three counts, so a project with half its records corrupt printed a
    verdict for the other half and the missing half simply was not there."""
    audit = _audit_module(monkeypatch, _project(
        tmp_path, "p1", [_scored(0.0, 1.4), {"start": 2.0, "end": 2.8}]))
    row = audit._measure("p1")
    judged = row["eligible"] + row["ineligible"] + row["undecidable"]
    assert judged == 1 and row["candidates"] == 2
    audit._report(row)
    printed = capsys.readouterr().out
    assert "1 of 2 judged" in printed
    assert "1 not measured at all" in printed


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
