"""Batch R6's placement audit: what reaches the exit code.

THE RULE THIS FILE EXISTS FOR is the one in `CLAUDE.md`: any diagnostic that
would INVALIDATE the conclusion has to reach the exit code, and needs a test
through `main()` that demonstrates the non-zero exit. Computing it, printing it
and not wiring it up is its own failure mode, and a quieter one than a missing
denominator.

The audit shipped without these, and four separate defects were in it that a
test through `main()` would have caught on the first run: a requested project
that vanished from the denominator, a sub-record that raised instead of being
refused, refusals computed and left out of the verdict, and an unreadable
keep-out printed and wired to nothing.

`scripts/` is not a package, so the module is loaded by path — the same way
`test_clipper_boundary_audit.py` does it, and through `monkeypatch` rather than
`os.environ`, because the script reads `CLIPFORGE_DATA_DIR` at import time and
setting it for good sent the job queue's own tests to a directory that
disappeared with the temp path.
"""

from __future__ import annotations

import json
from pathlib import Path


def _audit(monkeypatch, data_dir: Path):
    import importlib.util

    monkeypatch.setenv("CLIPFORGE_DATA_DIR", str(data_dir))
    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "audit_caption_placement.py")
    spec = importlib.util.spec_from_file_location("audit_caption_placement",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: A REAL 1080x1920 MP4 beside every sidecar, because the audit measures the
#: rendered height rather than assuming it — and a test that let it assume would
#: be testing the fallback in every case and the measured path in none.
_RENDER: list[bytes] = []


def _render_bytes() -> bytes:
    import subprocess
    import tempfile

    if _RENDER:
        return _RENDER[0]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "r.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=1080x1920:d=0.1:r=10",
             "-pix_fmt", "yuv420p", str(out)],
            check=True, capture_output=True)
        _RENDER.append(out.read_bytes())
    return _RENDER[0]


def _analysis(tmp_path: Path, project: str, *, samples=None,
              proxy=(480, 270)) -> None:
    """The face inputs the audit reads. Written by default, because a project
    without them is a REFUSAL — "the inputs could not be read" is not "no
    faces" — and a fixture that always triggers it would test only that."""
    d = tmp_path / "clipper" / project / "analysis"
    d.mkdir(parents=True, exist_ok=True)
    (d / "signals.json").write_text(
        json.dumps({"proxy_width": proxy[0], "proxy_height": proxy[1]}),
        encoding="utf-8")
    (d / "faces.json").write_text(
        json.dumps({"samples": samples if samples is not None else []}),
        encoding="utf-8")


def _sidecar(tmp_path: Path, project: str, clip: str, body: dict,
             render: bool = True, analysis: bool = True) -> Path:
    exports = tmp_path / "clipper" / project / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / f"{clip}.json").write_text(json.dumps(body), encoding="utf-8")
    if render:
        (exports / f"{clip}.mp4").write_bytes(_render_bytes())
    if analysis:
        _analysis(tmp_path, project)
    return exports / f"{clip}.json"


def _ok(**over) -> dict:
    """A sidecar today's rule reproduces: bottom preset, nothing in the way."""
    body = {
        "source_start": 0.0,
        "caption_plan": {"y_pct": 0.75, "style": {"position": "bottom"}},
        # The ANCHOR decides the delivered window, not the rectangle: the crop
        # is centred on it and clamped into the frame.
        "dynamic_plan": {"shots": [{"index": 0, "composition": "crop",
                                    "t0": 0.0, "t1": 2.0,
                                    "anchor": [304, 540], "shake": 0.0,
                                    "rect": {"x": 0, "y": 0,
                                             "w": 608, "h": 1080}}],
                         "src_w": 1920, "src_h": 1080},
        "layout_plan": {"safe_zones": {"top": 200, "keep_out": []}},
    }
    body.update(over)
    return body


def _run(module, argv: list[str]) -> tuple[int, dict]:
    """`main()` with a patched argv, and the report it printed."""
    import io
    import sys
    from contextlib import redirect_stdout

    old = sys.argv
    sys.argv = ["audit_caption_placement.py", "--json", *argv]
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            code = module.main()
    finally:
        sys.argv = old
    return code, json.loads(buffer.getvalue())


# --- the empty corpus --------------------------------------------------------


def test_an_assumed_output_height_fails_the_run(tmp_path, monkeypatch):
    """A silent 1920 is a second source of truth about output geometry, and an
    early return on a missing render was worse in the other direction: it took
    the whole row down, so a corpus with no renders reported nothing about
    caption positions either — none of which depend on the render."""
    _sidecar(tmp_path, "p", "a", _ok(), render=False)
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["output_geometry_assumed"] == 1
    assert out["with_a_caption_position"] == 1, "the rest of the row survived"
    assert code == 1


def test_an_empty_corpus_is_not_a_pass(tmp_path, monkeypatch):
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["sidecars"] == 0
    assert code == 1


def test_json_returns_the_run_s_exit_code(tmp_path, monkeypatch):
    """`--json` returning 0 unconditionally is on the list in `CLAUDE.md`
    because it happened."""
    _sidecar(tmp_path, "p", "a", _ok())
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["failures"] == [] and code == 0

    # A sidecar with no caption plan is UNAVAILABLE, not a failure: an export
    # can genuinely carry no captions. It is counted so that "99 of 99
    # explained" is never read as a statement about 101 renders.
    _sidecar(tmp_path, "p", "b", {"not": "a caption plan"})
    code, out = _run(module, [])
    assert out["without_a_caption_position"] == 1
    assert out["read"] == 2 and out["with_a_caption_position"] == 1
    # It also cannot say where in the source it came from, so its face column
    # is unavailable — counted under its own name, and not the same fact as
    # analysis files nobody could read.
    assert out["clips_with_no_source_start"] == 1
    assert out["clips_with_unreadable_face_inputs"] == 0
    assert code == 0

    # ...while one that cannot be read is.
    _sidecar(tmp_path, "p", "c", _ok(dynamic_plan=[1]))
    code, out = _run(module, [])
    assert code == 1


# --- a request that vanishes -------------------------------------------------


def test_a_project_asked_for_and_not_found_fails_the_run(tmp_path, monkeypatch):
    """Filtering silently made `--project real --project typo` identical to
    `--project real`, exit 0. The request disappeared from the denominator and
    the run read as a pass over whatever happened to be there."""
    _sidecar(tmp_path, "real", "a", _ok())
    module = _audit(monkeypatch, tmp_path)

    code, out = _run(module, ["--project", "real"])
    assert code == 0 and out["sidecars"] == 1

    code, out = _run(module, ["--project", "real", "--project", "typo"])
    assert out["sidecars"] == 1, "the same corpus"
    assert out["projects_not_found"] == ["typo"]
    assert code == 1, "and NOT the same verdict"


# --- a sub-record that is not a record ---------------------------------------


def test_a_sub_record_that_is_not_a_record_is_refused_not_raised(tmp_path,
                                                                 monkeypatch):
    """`side.get("dynamic_plan") or {}` returns the list itself for `[1]`, and
    `.get` on a list raises — out of a loop over the corpus, so the audit
    reports on the part before the crash and never says it crashed."""
    module = _audit(monkeypatch, tmp_path)
    for key in ("dynamic_plan", "caption_plan", "layout_plan"):
        _sidecar(tmp_path, "p", key, _ok(**{key: [1]}))
        code, out = _run(module, ["--project", "p"])
        assert code == 1, key
        assert any(f"{key}_not_a_record" in line
                   for line in out["refusals"]), (key, out["refusals"])


def test_a_sidecar_that_is_not_a_record_gets_a_row(tmp_path, monkeypatch):
    path = _sidecar(tmp_path, "p", "a", _ok())
    path.write_text("[1, 2, 3]", encoding="utf-8")
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["refused"] == 1 and out["read"] == 0
    assert code == 1


def test_an_unreadable_sidecar_never_just_vanishes(tmp_path, monkeypatch):
    path = _sidecar(tmp_path, "p", "a", _ok())
    path.write_text("{not json", encoding="utf-8")
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["sidecars"] == 1, "it is still in the denominator"
    assert out["refused"] == 1 and code == 1


# --- diagnostics that must reach the exit code -------------------------------


def test_a_refused_shot_fails_the_run(tmp_path, monkeypatch):
    """A shot the placement report could not measure is not a clean one, and
    the refusal was being computed and dropped."""
    _sidecar(tmp_path, "p", "a", _ok(dynamic_plan={
        "shots": [{"index": 0, "composition": "wobble"}],
        "src_w": 1920, "src_h": 1080}))
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["placement_refusals"] == 1
    assert code == 1


def test_an_unreadable_keep_out_fails_the_run(tmp_path, monkeypatch):
    """It was printed and wired to nothing. A rectangle the placement rule threw
    away without a word invalidates the explanation exactly as a non-finite one
    does."""
    _sidecar(tmp_path, "p", "a", _ok(layout_plan={
        "safe_zones": {"top": 200, "keep_out": [{"x": 0, "y": 100}]}}))
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["with_an_unreadable_keep_out"] == 1
    assert code == 1


def test_a_non_finite_keep_out_fails_the_run(tmp_path, monkeypatch):
    _sidecar(tmp_path, "p", "a", _ok(layout_plan={
        "safe_zones": {"keep_out": [{"x": 0, "y": float("nan"),
                                     "w": 1080, "h": float("nan")}]}}))
    module = _audit(monkeypatch, tmp_path)
    # `json.dumps` writes NaN, and `json.loads` reads it back, so the file
    # round-trips the value the placement rule actually sees.
    code, out = _run(module, [])
    assert out["with_a_non_finite_keep_out"] == 1
    assert code == 1


# --- the position it asked for -----------------------------------------------


def test_no_position_can_be_checked_against_todays_rule(tmp_path, monkeypatch):
    """`clipper_captions` re-places the caption at RENDER time with the stored
    keep-outs PLUS `panels_to_keep_out(panels, shots)`, and `panels` is not on
    the sidecar. So the set the position was resolved against no longer exists,
    and running the rule on the smaller stored set answers a different question.

    Two earlier versions answered it anyway: comparing the stored `y_pct` —
    which is the PRESET, not the burned position — gave "8 of 99 today's rule
    would not produce", and comparing the burned position against the same
    incomplete keep-outs gave 54. Neither was a fact about the rule.

    NOT A FAILURE, because it is a property of the stored format rather than of
    any clip, and a permanently red gate would bury the findings that are about
    clips."""
    _sidecar(tmp_path, "p", "a", _ok(
        caption_plan={"y_pct": 0.51, "style": {"position": "bottom"}}))
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["position_not_reproducible"] == 1
    assert "keep_out" in out["why_not_reproducible"]
    assert code == 0, "printed, not failed"


def test_a_moving_crop_is_counted_and_does_not_fail_the_run(tmp_path,
                                                            monkeypatch):
    """PRINTED AND NOT WIRED, deliberately: nothing in this report depends on
    it. It is the precondition for the evidence mapper that does not exist yet,
    and the day it stops being zero, that mapper cannot be built on
    `shot["rect"]`.

    `move: push` is a LABEL. The shot stands still unless `push_amount` is above
    zero, which it is in none of the 89 stored styles — so reading the label
    instead of the size timeline says "95% of shots move", the opposite of the
    truth."""
    labelled = {"index": 0, "composition": "crop", "move": "push",
                "t0": 0.0, "t1": 4.0, "rect": {"x": 0, "y": 0,
                                               "w": 810, "h": 1440}}
    _sidecar(tmp_path, "still", "a", _ok(dynamic_plan={
        "shots": [labelled], "src_w": 2560, "src_h": 1440,
        "style": {"push_amount": 0.0}}))
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, ["--project", "still"])
    assert out["shots_with_a_multi_point_size_timeline"] == 0, "the label is not the motion"
    assert code == 0

    _sidecar(tmp_path, "moving", "a", _ok(dynamic_plan={
        "shots": [labelled], "src_w": 2560, "src_h": 1440,
        "style": {"push_amount": 0.1, "push_hz": 10.0}}))
    code, out = _run(module, ["--project", "moving"])
    assert out["shots_with_a_multi_point_size_timeline"] == 1
    assert code == 0, "it gates the mapper, not this report"


# --- the face signal ---------------------------------------------------------


def test_unreadable_face_inputs_fail_the_run(tmp_path, monkeypatch):
    """"The inputs could not be read" is not "no faces", and it is the one that
    voids the face column."""
    _sidecar(tmp_path, "p", "a", _ok(), analysis=False)
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["clips_with_unreadable_face_inputs"] == 1
    assert code == 1


def test_a_shot_with_no_sample_in_its_window_is_unavailable_not_clean(
        tmp_path, monkeypatch):
    """The detector samples about every two seconds and a shot is typically one
    to four, so most shots contain no sample at all. Reporting an empty list
    there would say "we looked at this shot and there was no face"."""
    _sidecar(tmp_path, "p", "a", _ok())
    _analysis(tmp_path, "p", samples=[{"t": 99.0, "boxes": [[0, 0, 40, 40]]}])
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["shots_with_no_face_sample"] == 1
    assert out["shots_with_mapped_faces"] == 0
    assert out["caption_on_a_face"] == 0
    assert out["worst_case_fully_measured"] == 0, "nothing was established"
    assert code == 0, "an unsampled shot is not a failure"


def test_a_face_over_the_caption_is_reported(tmp_path, monkeypatch):
    """End to end: a detector box in proxy pixels, through the renderer's chain,
    landing on the burned caption band."""
    # The crop is x 0..608 of a 1920x1080 source; the caption sits at 0.75 of
    # the output, so a proxy box low in the left third covers it.
    _sidecar(tmp_path, "p", "a", _ok())
    _analysis(tmp_path, "p",
              samples=[{"t": 1.0, "boxes": [[10, 190, 140, 70]]}])
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["shots_with_mapped_faces"] == 1
    assert out["caption_on_a_face"] == 1
    # ...and it is still a floor, because the UI and text signals are never
    # measured, so no clip's worst case is established.
    assert out["worst_case_fully_measured"] == 0
    assert code == 0


def test_a_sample_on_a_cut_belongs_to_the_shot_that_begins(tmp_path,
                                                           monkeypatch):
    """A closed window counted it for the shot that ended AND the shot that
    began — 20 of them in the corpus. The frame at that timestamp is the one the
    new shot shows."""
    two = _ok(dynamic_plan={
        "shots": [{"index": 0, "composition": "crop", "t0": 0.0, "t1": 2.0,
                   "anchor": [304, 540], "shake": 0.0,
                   "rect": {"x": 0, "y": 0, "w": 608, "h": 1080}},
                  {"index": 1, "composition": "crop", "t0": 2.0, "t1": 4.0,
                   "anchor": [304, 540], "shake": 0.0,
                   "rect": {"x": 0, "y": 0, "w": 608, "h": 1080}}],
        "src_w": 1920, "src_h": 1080})
    _sidecar(tmp_path, "p", "a", two)
    _analysis(tmp_path, "p", samples=[{"t": 2.0, "boxes": [[10, 190, 140, 70]]}])
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    # Exactly one of the two shots gets it, and it is the second.
    assert out["shots_with_mapped_faces"] == 1
    assert out["shots_with_no_face_sample"] == 1


def test_the_last_shot_keeps_its_endpoint(tmp_path, monkeypatch):
    """Nothing follows it to take the sample, so a half-open window there would
    drop a real measurement."""
    _sidecar(tmp_path, "p", "a", _ok())
    _analysis(tmp_path, "p", samples=[{"t": 2.0, "boxes": [[10, 190, 140, 70]]}])
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["shots_with_mapped_faces"] == 1, "t == t1 on the final shot"


def test_a_shot_with_two_samples_answers_at_any_point(tmp_path, monkeypatch):
    """Every sample's boxes go into one list, so the answer is "was the caption
    over a face at ANY point in this shot", not "throughout it". The
    conservative direction for a warning and the wrong one for a claim of
    cleanliness, so the count is reported."""
    _sidecar(tmp_path, "p", "a", _ok())
    _analysis(tmp_path, "p", samples=[
        {"t": 0.5, "boxes": [[10, 190, 140, 70]]},
        {"t": 1.5, "boxes": [[10, 10, 20, 20]]}])
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["shots_with_more_than_one_sample"] == 1
    assert out["caption_on_a_face"] == 1, "the low box counts, at some point"


def test_the_delivered_position_is_read_from_the_ass_not_the_plan(tmp_path,
                                                                  monkeypatch):
    """`caption_plan.y_pct` is the preset the plan asked for; the `.ass` carries
    what `resolve_position` settled on and what libass burned. They differ on 46
    of the 99 stored clips, by as much as 933 pixels."""
    from services.clipper.caption_corpus import _burned_y_pct

    path = _sidecar(tmp_path, "p", "a", _ok())
    ass = path.with_suffix(".ass")
    assert _burned_y_pct(ass, 1920) == (None, "no_ass")

    head = "[Events]\n"
    line = ("Dialogue: 0,0:00:0{a}.00,0:00:0{b}.00,ovl0,,0,0,0,,"
            "{{\\an5\\pos(540,{y})}}TEXT\n")
    ass.write_text(head + line.format(a=0, b=1, y=960), encoding="utf-8")
    assert _burned_y_pct(ass, 1920) == (0.5, "ass")

    # Two positions in one file is refused rather than averaged: "the caption is
    # at 1141" would then be a sentence about no particular moment.
    ass.write_text(head + line.format(a=0, b=1, y=960)
                   + line.format(a=1, b=2, y=500), encoding="utf-8")
    got, source = _burned_y_pct(ass, 1920)
    assert got is None and source == "more_than_one_position_in_the_ass"

    # And an `.ass` with no position at all is its own answer, not a fallback
    # that quietly borrows the plan's number.
    ass.write_text(head, encoding="utf-8")
    assert _burned_y_pct(ass, 1920) == (None, "no_position_in_the_ass")


def test_a_position_that_came_from_the_plan_says_so(tmp_path, monkeypatch):
    """A number resting on the plan must not be readable as one resting on the
    render — 46 of 99 clips have them disagree."""
    _sidecar(tmp_path, "p", "a", _ok())
    module = _audit(monkeypatch, tmp_path)
    _code, out = _run(module, [])
    assert out["with_a_caption_position"] == 1
    assert out["position_not_reproducible"] == 1
