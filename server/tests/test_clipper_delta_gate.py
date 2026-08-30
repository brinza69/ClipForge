"""Batch R5a's delta tool, exercised through `main()`.

It is a measurement instrument, and the plan has now found the same defect in
instruments eight times: something that could not be read disappears and the
result looks like a pass. This file runs the entry point and checks what it
returns, because every one of those eight was invisible in the branch.

The case worth naming is `test_a_shortlist_that_selects_nothing_is_a_refusal`.
The first version of the shortlist comparison recovered zero windows on both
sides — it looked for a tag on values that were never dicts — and zero against
zero compares equal, so the report said "membership same" about a selection it
had entirely failed to read.

`scripts/` is not a package, so the module is loaded by path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _module(monkeypatch, data_dir: Path):
    import importlib.util

    monkeypatch.setenv("CLIPFORGE_DATA_DIR", str(data_dir))
    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "measure_snap_board_delta.py")
    spec = importlib.util.spec_from_file_location("measure_snap_board_delta", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(tmp_path: Path, name: str, candidates) -> Path:
    analysis = tmp_path / "clipper" / name / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "candidates.json").write_text(json.dumps(candidates),
                                              encoding="utf-8")
    return tmp_path


def _run(module, *argv: str) -> int:
    saved = sys.argv
    try:
        sys.argv = ["measure_snap_board_delta", *argv]
        return module.main()
    finally:
        sys.argv = saved


def test_a_project_with_no_transcript_is_refused_not_skipped(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    """A project the tool cannot measure has to occupy a row and fail the run.
    The alternative is a corpus that quietly shrinks to whatever worked."""
    module = _module(monkeypatch, _project(tmp_path, "p", [{"start": 0.0,
                                                           "end": 10.0}]))
    assert _run(module, "p") == 2
    assert "no_transcript_or_project" in capsys.readouterr().out


def test_a_missing_artefact_is_refused(tmp_path, monkeypatch, capsys):
    (tmp_path / "clipper" / "gone").mkdir(parents=True)
    module = _module(monkeypatch, tmp_path)
    assert _run(module, "gone") == 2
    assert "no_candidates_artefact" in capsys.readouterr().out


def test_an_empty_corpus_is_not_a_pass(tmp_path, monkeypatch, capsys):
    (tmp_path / "clipper").mkdir(parents=True)
    module = _module(monkeypatch, tmp_path)
    assert _run(module, "--all") == 2
    assert "no projects with candidates" in capsys.readouterr().out


def test_json_and_text_return_the_same_code(tmp_path, monkeypatch):
    module = _module(monkeypatch, _project(tmp_path, "p", [{"start": 0.0,
                                                           "end": 10.0}]))
    assert _run(module, "p") == _run(module, "p", "--json") == 2


def test_a_shortlist_that_selects_nothing_is_a_refusal(monkeypatch, tmp_path):
    """THE case. Zero against zero compares equal, so a comparison that failed
    to read the selection at all reported the selection as unchanged."""
    module = _module(monkeypatch, tmp_path)
    monkeypatch.setattr(module.candidate_groups, "build_groups",
                        lambda *_a, **_k: [])
    monkeypatch.setattr(module.candidate_groups, "build_shortlist",
                        lambda *_a, **_k: {"selected": []})
    out = module._shortlist_delta([{"start": 0.0, "end": 1.0}],
                                  [{"start": 0.0, "end": 1.0}], 10.0)
    assert out.get("refused") == "ValueError"
    assert "membership_changed" not in out


def test_a_refused_shortlist_fails_the_run(tmp_path, monkeypatch, capsys):
    """Only PROJECT-level refusals reached the exit code, so a project whose
    judge shortlist could not be built at all came back green. Every way of not
    knowing has to fail the run."""
    module = _module(monkeypatch, _project(tmp_path, "p", [{"start": 0.0,
                                                           "end": 10.0}]))
    monkeypatch.setattr(module, "_measure", lambda name, *_a, **_k: {
        "project": name, "candidates": 1, "invalid": 0, "moved": 0,
        "contaminated": [], "scores_changed": 0, "changed_without_moving": [],
        "max_delta": 0.0, "mean_abs_delta": 0.0, "order_changed": False,
        "top_n": 80, "scorer": "heuristic_only", "windows": {},
        "shortlist": {"refused": "ValueError"}})
    assert _run(module, "p") == 2
    assert "could not be built" in capsys.readouterr().out


def test_an_incomplete_input_is_refused_not_defaulted(tmp_path, monkeypatch):
    """`read_artifact(...) or {}` and `duration or 0.0` turn a missing input
    into a measurement taken against nothing: every signal-derived feature
    reads zero and the report describes the absence rather than the source."""
    module = _module(monkeypatch, _project(tmp_path, "p", [{"start": 0.0,
                                                           "end": 10.0}]))

    class _P:
        duration = 100.0
        clipper_settings: dict = {}
        content_type_override = None
        content_type = "unknown"

    async def _load(_pid):
        return {"segments": [{"start": 0.0, "end": 1.0, "words": [
            {"word": "hi", "start": 0.0, "end": 0.4}]}]}, _P()

    monkeypatch.setattr(module, "_load", _load)
    monkeypatch.setattr(module.storage, "read_artifact",
                        lambda _pid, name: None)
    assert module._measure("p")["refused"] == "no_signals_artefact"

    monkeypatch.setattr(module.storage, "read_artifact",
                        lambda _pid, name: {"rms": [0.1]} if name == "signals" else None)
    _P.duration = 0.0
    assert module._measure("p")["refused"] == "no_duration_on_project"


def test_the_judge_sees_representatives_not_every_member(monkeypatch, tmp_path):
    """A group of seven cuts of one moment is asked about through the ones the
    shortlist puts forward. Counting every member measured a set the judge never
    sees — and made the answer look reassuring by covering the whole corpus.

    `build_groups` returns both lists AS indices into the input; the first
    version tagged the candidates and looked for the tag on dicts that were
    never dicts, which is how it recovered nothing at all."""
    module = _module(monkeypatch, tmp_path)
    monkeypatch.setattr(
        module.candidate_groups, "build_groups",
        lambda rows, **_k: [{"moment_id": "m",
                             "members": list(range(len(rows))),
                             "representatives": [0]}])
    monkeypatch.setattr(
        module.candidate_groups, "build_shortlist",
        lambda groups, **_k: {"selected": list(groups)})
    rows = [{"start": float(i), "end": float(i) + 1.0} for i in range(7)]
    out = module._shortlist_delta(rows, rows, 10.0)
    assert out["windows_before"] == out["windows_after"] == 1, "one of seven"
    assert out["membership_changed"] == []


def test_a_group_that_puts_nobody_forward_is_a_refusal(monkeypatch, tmp_path):
    """Selected groups with no representatives recover nothing, and nothing
    against nothing compares equal."""
    module = _module(monkeypatch, tmp_path)
    monkeypatch.setattr(
        module.candidate_groups, "build_groups",
        lambda rows, **_k: [{"moment_id": "m", "members": [0],
                             "representatives": []}])
    monkeypatch.setattr(
        module.candidate_groups, "build_shortlist",
        lambda groups, **_k: {"selected": list(groups)})
    rows = [{"start": 0.0, "end": 1.0}]
    assert module._shortlist_delta(rows, rows, 10.0).get("refused") == "ValueError"
