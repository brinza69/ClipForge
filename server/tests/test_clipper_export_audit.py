"""The audit script: what it counts as an export, and when it fails.

Split from `test_clipper_edit_quality.py` at 500 lines. Different subject: these
are about the CORPUS — which files on disk make it into the report and which are
findings — while the other file is about the metrics themselves.

`scripts/` is not a package, so the module is loaded by path.
"""

from __future__ import annotations

from pathlib import Path

from services.clipper import edit_quality as eq
from tests.test_clipper_edit_quality import _crop_fit_fit_crop, _sidecar


def _write(exports, stem: str, sidecar: dict) -> None:
    """A sidecar under the name its clip actually has, in the project it is
    actually in. The audit refuses a plan whose `clip_id` or `project_id`
    disagrees with where it was found, so a fixture that ignored either would be
    testing a path the script rejects."""
    import json

    (exports / f"{stem}.json").write_text(
        json.dumps({**sidecar, "clip_id": stem, "project_id": "p1"}),
        encoding="utf-8")


# --- the audit script's view of a project ------------------------------------


def _audit_module():
    """Load `scripts/audit_clipper_exports.py` by path — `scripts/` is not a
    package, and the enumeration below is the part of it that decides what the
    corpus even is."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "audit_clipper_exports.py"
    spec = importlib.util.spec_from_file_location("audit_clipper_exports", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_mp4_without_a_sidecar_does_not_vanish_from_the_corpus(tmp_path, monkeypatch):
    """Globbing `*.json` was the first version: a rendered clip whose sidecar was
    never written disappeared entirely and the audit reported a smaller, cleaner
    project. All three ways an export can be incomplete are counted."""
    import json

    audit = _audit_module()
    exports = tmp_path / "p1" / "exports"
    exports.mkdir(parents=True)
    (exports / "complete.mp4").write_bytes(b"x")
    _write(exports, "complete", _crop_fit_fit_crop())
    (exports / "no_sidecar.mp4").write_bytes(b"x")
    _write(exports, "orphan", _sidecar(None))
    (exports / "broken.mp4").write_bytes(b"x")
    (exports / "broken.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(audit, "DATA", tmp_path)

    parsed, problems = audit._exports("p1")
    # Only the complete pair is measured; the other three are findings.
    assert [stem for stem, _ in parsed] == ["complete"]
    assert problems["missing_sidecar"] == ["p1/no_sidecar.mp4"]
    assert [Path(p).stem for p in problems["orphan_sidecar"]] == ["orphan"]
    assert [Path(p).stem for p in problems["unreadable_sidecar"]] == ["broken"]


def test_a_project_with_no_exports_directory_is_empty_not_an_error(tmp_path, monkeypatch):
    audit = _audit_module()
    (tmp_path / "p1").mkdir()
    monkeypatch.setattr(audit, "DATA", tmp_path)
    parsed, problems = audit._exports("p1")
    assert parsed == []
    assert not any(problems.values())


def test_an_orphan_sidecar_is_a_finding_not_a_clip(tmp_path, monkeypatch):
    """A plan whose mp4 is gone describes a video that does not exist. The first
    fix for the missing-sidecar bug went too far the other way and measured it,
    putting a file nobody can watch into the baseline."""
    import json

    audit = _audit_module()
    exports = tmp_path / "p1" / "exports"
    exports.mkdir(parents=True)
    (exports / "complete.mp4").write_bytes(b"x")
    _write(exports, "complete", _crop_fit_fit_crop())
    _write(exports, "orphan", _sidecar(None))
    monkeypatch.setattr(audit, "DATA", tmp_path)

    parsed, problems = audit._exports("p1")
    assert [stem for stem, _ in parsed] == ["complete"]
    assert [Path(p).stem for p in problems["orphan_sidecar"]] == ["orphan"]


def test_a_project_of_nothing_but_unstamped_mp4s_is_still_audited(tmp_path, monkeypatch):
    """Keying `--all` on `*.json` excluded a project whose every clip was
    rendered and never stamped — the corpus with the worst problem was the one
    that disappeared."""
    audit = _audit_module()
    exports = tmp_path / "p1" / "exports"
    exports.mkdir(parents=True)
    (exports / "a.mp4").write_bytes(b"x")
    monkeypatch.setattr(audit, "DATA", tmp_path)

    assert audit._projects([], True) == ["p1"]
    parsed, problems = audit._exports("p1")
    assert parsed == []
    assert problems["missing_sidecar"] == ["p1/a.mp4"]


def test_the_gate_exits_non_zero_on_a_structural_problem(tmp_path, monkeypatch, capsys):
    """A script called a gate that always exits 0 is a report: whatever runs it
    sees success while the output says otherwise."""
    import json
    import sys

    audit = _audit_module()
    exports = tmp_path / "p1" / "exports"
    exports.mkdir(parents=True)
    (exports / "a.mp4").write_bytes(b"x")
    _write(exports, "a", _crop_fit_fit_crop())
    monkeypatch.setattr(audit, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["audit", "p1"])
    assert audit.main() == 0

    # An mp4 whose sidecar was never written.
    (exports / "b.mp4").write_bytes(b"x")
    assert audit.main() == 2
    assert "integrity_ok: False" in capsys.readouterr().out


def test_a_stamped_sidecar_that_no_longer_matches_its_plan_fails_the_gate(
        tmp_path, monkeypatch):
    """`unavailable` is allowed — the pilot exports predate the key. A digest
    that disagrees with the plan beside it is not."""
    import json
    import sys

    audit = _audit_module()
    exports = tmp_path / "p1" / "exports"
    exports.mkdir(parents=True)
    sidecar = _crop_fit_fit_crop()
    sidecar["input_fingerprint"] = eq.input_fingerprint(sidecar)
    sidecar["dynamic_plan"]["shots"][0]["composition"] = "fit"
    (exports / "a.mp4").write_bytes(b"x")
    _write(exports, "a", sidecar)
    monkeypatch.setattr(audit, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["audit", "p1"])
    assert audit.main() == 2


def _gate(tmp_path, monkeypatch):
    """A project with one clean export, and the audit pointed at it."""
    import sys

    audit = _audit_module()
    exports = tmp_path / "p1" / "exports"
    exports.mkdir(parents=True)
    (exports / "a.mp4").write_bytes(b"x")
    _write(exports, "a", _crop_fit_fit_crop())
    monkeypatch.setattr(audit, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["audit", "p1"])
    assert audit.main() == 0, "the clean corpus must pass, or nothing below means anything"
    return audit, exports


def test_a_sidecar_that_is_not_a_sidecar_is_a_finding_not_a_crash(tmp_path, monkeypatch):
    """A bare `[]` reached `clip_report` and took the whole run down with a
    TypeError — exit 1, a stack trace, and no report. A corrupt file must be
    reported BY the audit, not able to end it."""
    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text("[]", encoding="utf-8")
    assert audit.main() == 2


def test_a_sidecar_that_names_another_clip_is_refused(tmp_path, monkeypatch):
    """It describes a video that is not there. Measuring it put another clip's
    shots into this project and still reported `integrity_ok: True`."""
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(
        json.dumps({**_crop_fit_fit_crop(), "clip_id": "SOMEONE_ELSE"}), encoding="utf-8")
    assert audit.main() == 2


def test_a_plan_with_unreadable_times_fails_the_gate(tmp_path, monkeypatch):
    """The shot count still included it, its length vanished from the minimum,
    and its two boundaries became `gap`s — a corrupt plan read as a clean edit
    with two jump cuts, and the gate passed it."""
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(json.dumps({
        "clip_id": "b", "project_id": "p1", "duration": 10.0, "dynamic_plan": {"shots": [
            {"t0": 0.0, "t1": 2.0, "composition": "fit"},
            {"t0": "oops", "t1": None, "composition": "fit"},
            {"t0": 4.0, "t1": 6.0, "composition": "fit"}]}}), encoding="utf-8")
    assert audit.main() == 2


def test_a_malformed_plan_fails_the_gate(tmp_path, monkeypatch):
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(json.dumps({
        "clip_id": "b", "duration": 10.0,
        "dynamic_plan": {"shots": "nope"}}), encoding="utf-8")
    assert audit.main() == 2


def test_a_sidecar_that_names_no_clip_at_all_is_refused(tmp_path, monkeypatch):
    """Gating the identity check on `isinstance(str)` meant a plan with no
    identity was measured as though it belonged to the file beside it — the same
    unproven leap the check exists to refuse, made silently."""
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(
        json.dumps({**_crop_fit_fit_crop(), "clip_id": None}), encoding="utf-8")
    assert audit.main() == 2


def test_a_corrupt_trim_claim_fails_the_gate(tmp_path, monkeypatch):
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(
        json.dumps({**_crop_fit_fit_crop(), "clip_id": "b",
                    "drop_spans": [[1.0]]}), encoding="utf-8")
    assert audit.main() == 2


def test_shots_that_do_not_join_fail_the_gate(tmp_path, monkeypatch):
    """A gap is a structurally invalid edit and used to pass, because it is a
    metric rather than a defect. An UNDECIDABLE boundary still passes: a plan
    written before `composition` existed is old, not corrupt."""
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(json.dumps({
        "clip_id": "b", "project_id": "p1", "duration": 10.0, "dynamic_plan": {"shots": [
            {"t0": 0.0, "t1": 2.0, "composition": "fit"},
            {"t0": 3.5, "t1": 6.0, "composition": "fit"}]}}), encoding="utf-8")
    assert audit.main() == 2


def test_a_plan_too_old_to_judge_still_passes_the_gate(tmp_path, monkeypatch):
    """The corpus is full of them. `unavailable` is the honest reading of an old
    plan, not a reason to fail a run."""
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(json.dumps({
        "clip_id": "b", "project_id": "p1", "duration": 10.0,
        "dynamic_plan": {"shots": [
            {"t0": 0.0, "t1": 5.0}, {"t0": 5.0, "t1": 10.0}]}}), encoding="utf-8")
    assert audit.main() == 0


def test_a_composition_nobody_ever_wrote_fails_the_gate(tmp_path, monkeypatch):
    """An old plan has no `composition` and is undecidable — age is not
    corruption. A plan that NAMES a composition nobody ever wrote is the other
    thing, and treating the two the same let an explicitly invalid plan pass."""
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(json.dumps({
        "clip_id": "b", "project_id": "p1", "duration": 6.0,
        "dynamic_plan": {"shots": [
            {"t0": 0.0, "t1": 3.0, "composition": "fit"},
            {"t0": 3.0, "t1": 6.0, "composition": "banana"}]}}), encoding="utf-8")
    assert audit.main() == 2


def test_a_sidecar_from_another_project_fails_the_gate(tmp_path, monkeypatch):
    """A plan copied in from another project says so in its `project_id`, and
    measuring it puts another source's edit into this one's baseline."""
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(json.dumps({
        **_crop_fit_fit_crop(), "clip_id": "b",
        "project_id": "somewhere_else"}), encoding="utf-8")
    assert audit.main() == 2


def test_shot_times_outside_the_clip_fail_the_gate(tmp_path, monkeypatch):
    """A 900-second shot inside a ten-second export read as a clean edit: the
    numbers were floats, so nothing looked at them again."""
    import json

    audit, exports = _gate(tmp_path, monkeypatch)
    (exports / "b.mp4").write_bytes(b"x")
    (exports / "b.json").write_text(json.dumps({
        "clip_id": "b", "project_id": "p1", "duration": 10.0,
        "dynamic_plan": {"shots": [{"t0": 0.0, "t1": 900.0, "composition": "fit"}]}},
    ), encoding="utf-8")
    assert audit.main() == 2


def test_a_present_but_impossible_composition_fails_the_gate(tmp_path, monkeypatch):
    """`null` and `7` are not "no composition"; they are a plan claiming
    something that cannot be true."""
    import json

    for value in (None, 7):
        audit, exports = _gate(tmp_path / str(value), monkeypatch)
        (exports / "b.mp4").write_bytes(b"x")
        (exports / "b.json").write_text(json.dumps({
            "clip_id": "b", "project_id": "p1", "duration": 6.0,
            "dynamic_plan": {"shots": [
                {"t0": 0.0, "t1": 3.0, "composition": "fit"},
                {"t0": 3.0, "t1": 6.0, "composition": value}]}}), encoding="utf-8")
        assert audit.main() == 2, value


def test_an_impossible_duration_fails_the_gate(tmp_path, monkeypatch):
    import json

    for value in (0, float("inf")):
        audit, exports = _gate(tmp_path / str(value), monkeypatch)
        (exports / "b.mp4").write_bytes(b"x")
        (exports / "b.json").write_text(json.dumps({
            "clip_id": "b", "project_id": "p1", "duration": value,
            "dynamic_plan": {"shots": [
                {"t0": 0.0, "t1": 3.0, "composition": "fit"}]}}), encoding="utf-8")
        assert audit.main() == 2, value


def test_a_valid_trim_is_measured_and_declares_its_source_jump(
        tmp_path, monkeypatch, capsys):
    """R8 is not complete if the unit helper works but the corpus gate still
    refuses the sidecar or drops the new boundary from its aggregate."""
    import json
    import sys

    audit = _audit_module()
    exports = tmp_path / "p1" / "exports"
    exports.mkdir(parents=True)
    (exports / "a.mp4").write_bytes(b"x")
    _write(exports, "a", _sidecar([
        {"t0": 0.0, "t1": 10.0, "composition": "fit"}],
        duration=10.0, drop_spans=[(4.0, 6.0)]))
    monkeypatch.setattr(audit, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["audit", "p1", "--json"])

    assert audit.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["integrity_ok"] is True
    assert report["macro"]["shots"] == 2
    assert report["macro"]["trim_jumps"] == 1
