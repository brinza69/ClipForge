"""Batch R6's chrome gate: what reaches the exit code.

Same rule as the other two audits and in `CLAUDE.md` for the same reason: a
diagnostic that would invalidate the conclusion has to reach the exit code, and
needs a test through `main()`.

The case this file is built around is `unavailable`. An export the recogniser
could not decide is not an export without chrome, and a run that treats it as
one is a pass over a hole.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.clipper import source_chrome as sc


def _gate(monkeypatch, data_dir: Path):
    import importlib.util

    monkeypatch.setenv("CLIPFORGE_DATA_DIR", str(data_dir))
    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "detect_source_chrome.py")
    spec = importlib.util.spec_from_file_location("detect_source_chrome", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _export(tmp_path: Path, project: str, clip: str) -> None:
    d = tmp_path / "clipper" / project / "exports"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{clip}.mp4").write_bytes(b"not really a video")


def _run(module, argv, verdicts):
    """`main()` with the detector stubbed to return the given states in turn."""
    import io
    import sys
    from contextlib import redirect_stdout

    seen = iter(verdicts)

    def fake(video, every_s=sc.EVERY_S, reader=None):
        state = next(seen)
        return {"schema": "source_chrome_v1", "state": state,
                "why_unavailable": (sc.DETECTOR_FAILED
                                    if state == sc.UNAVAILABLE else None),
                "frames_analysed": 10, "frames_sampled": 10,
                "frames_with_a_control": 3 if state == sc.DETECTED else 0,
                "hits": ([{"at": 1.0, "text": "SHARE", "conf": 0.9, "y": 0.9}]
                         if state == sc.DETECTED else []),
                "frames_min": sc.FRAMES_MIN, "conf_min": sc.CONF_MIN,
                "every_s": every_s, "measured_at_every_s": sc.EVERY_S,
                "calibrated": False, "applied": False}

    module.sc = type("S", (), {**{k: getattr(sc, k) for k in dir(sc)
                                  if not k.startswith("__")},
                               "detect": staticmethod(fake),
                               "_reader": staticmethod(lambda: object())})
    old = sys.argv
    sys.argv = ["detect_source_chrome.py", "--json", *argv]
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            code = module.main()
    finally:
        sys.argv = old
    return code, json.loads(buffer.getvalue())


def test_an_empty_corpus_is_not_a_pass(tmp_path, monkeypatch):
    module = _gate(monkeypatch, tmp_path)
    code, out = _run(module, ["--all"], [])
    assert out["exports"] == 0 and code == 1


def test_an_undecided_export_fails_the_run(tmp_path, monkeypatch):
    """It is not an export without chrome. A run that treats it as one is a
    pass over a hole."""
    _export(tmp_path, "p", "a")
    _export(tmp_path, "p", "b")
    module = _gate(monkeypatch, tmp_path)
    code, out = _run(module, ["--all"], [sc.NOT_DETECTED, sc.UNAVAILABLE])
    assert out["states"][sc.UNAVAILABLE] == 1
    assert code == 1


def test_a_decided_corpus_passes(tmp_path, monkeypatch):
    _export(tmp_path, "p", "a")
    _export(tmp_path, "p", "b")
    module = _gate(monkeypatch, tmp_path)
    code, out = _run(module, ["--all"], [sc.DETECTED, sc.NOT_DETECTED])
    assert out["failures"] == [] and code == 0
    assert out["states"] == {sc.DETECTED: 1, sc.NOT_DETECTED: 1,
                             sc.UNAVAILABLE: 0}


def test_expect_turns_it_into_a_gate(tmp_path, monkeypatch):
    """Without it the script only reports, because a detector graded by whoever
    tuned it is not being graded."""
    _export(tmp_path, "p", "a")
    module = _gate(monkeypatch, tmp_path)
    code, out = _run(module, ["--all"], [sc.NOT_DETECTED])
    assert code == 0, "no expectation, no gate"

    code, out = _run(module, ["--all", "--expect", "detected"],
                     [sc.NOT_DETECTED])
    assert code == 1
    assert any("expected detected" in line for line in out["failures"])


def test_a_project_asked_for_and_not_found_fails_the_run(tmp_path, monkeypatch):
    _export(tmp_path, "real", "a")
    module = _gate(monkeypatch, tmp_path)
    code, out = _run(module, ["real"], [sc.NOT_DETECTED])
    assert code == 0

    code, out = _run(module, ["real", "typo"], [sc.NOT_DETECTED])
    assert out["projects_not_found"] == ["typo"]
    assert out["exports"] == 1, "the same corpus"
    assert code == 1, "and not the same verdict"


def test_json_returns_the_run_s_exit_code(tmp_path, monkeypatch):
    _export(tmp_path, "p", "a")
    module = _gate(monkeypatch, tmp_path)
    code, _out = _run(module, ["--all"], [sc.UNAVAILABLE])
    assert code == 1
