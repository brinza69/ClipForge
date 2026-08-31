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


def _sidecar(tmp_path: Path, project: str, clip: str, body: dict,
             render: bool = True) -> Path:
    exports = tmp_path / "clipper" / project / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / f"{clip}.json").write_text(json.dumps(body), encoding="utf-8")
    if render:
        (exports / f"{clip}.mp4").write_bytes(_render_bytes())
    return exports / f"{clip}.json"


def _ok(**over) -> dict:
    """A sidecar today's rule reproduces: bottom preset, nothing in the way."""
    body = {
        "caption_plan": {"y_pct": 0.75, "style": {"position": "bottom"}},
        "dynamic_plan": {"shots": [{"index": 0, "composition": "crop"}],
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


def test_the_position_checked_is_the_one_the_style_asked_for(tmp_path,
                                                             monkeypatch):
    """Trying all four presets and accepting any match let a position produced
    by accident from a preset nobody chose count as an explanation. Six clips in
    `2d3375ee3420` sit at 0.51 — the `center` preset — with a style that says
    `bottom`, and the lax check called all six explained."""
    _sidecar(tmp_path, "p", "a", _ok(
        caption_plan={"y_pct": 0.51, "style": {"position": "bottom"}}))
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, [])
    assert out["position_no_preset_reproduces"] == 1
    assert code == 1

    _sidecar(tmp_path, "q", "a", _ok(
        caption_plan={"y_pct": 0.51, "style": {"position": "center"}}))
    code, out = _run(module, ["--project", "q"])
    assert out["position_no_preset_reproduces"] == 0 and code == 0
