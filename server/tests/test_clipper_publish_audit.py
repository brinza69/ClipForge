"""Batch R7's preflight audit: what reaches the exit code, and where the cache lives.

THE RULE THIS FILE EXISTS FOR is `CLAUDE.md`'s: any diagnostic that would
INVALIDATE the conclusion has to reach the exit code, and needs a test through
`main()` that demonstrates the non-zero exit. R7's audit shipped without one,
and two defects were in it that a run through `main()` would have caught:

- a corrupt sub-record raised out of the loop, so the run reported on the clips
  before it, died on the traceback, and never said which clip it stopped at;
- the OCR cache was written into `exports/` as `<clip>.chrome.json`, a
  directory in which SIX places in this repo read `*.json` as a clip's sidecar.
  The single file the interrupted run left behind was already enough to make
  `audit_clipper_exports` report `orphan_sidecar` and exit 2 — a cache that
  broke the baseline gate it was collected for.

`scripts/` is not a package, so the module is loaded by path, and through
`monkeypatch` rather than `os.environ`: the script reads `CLIPFORGE_DATA_DIR` at
import time, and setting it for good sends the job queue's own tests to a
directory that disappears with the temp path.
"""

from __future__ import annotations

import json
from pathlib import Path


def _audit(monkeypatch, data_dir: Path):
    import importlib.util

    monkeypatch.setenv("CLIPFORGE_DATA_DIR", str(data_dir))
    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "audit_publish_preflight.py")
    spec = importlib.util.spec_from_file_location("audit_publish_preflight",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The transcript lives in the database and this is a temp corpus with no
    # rows in it. Loading it would work and return nothing; stubbing keeps the
    # test about the audit rather than about sqlite.
    monkeypatch.setattr(module, "_words", lambda _project: None)
    return module


def _run(module, argv: list[str] | None = None) -> tuple[int, dict]:
    """`main()` with a patched argv, and the report it printed."""
    import io
    import sys
    from contextlib import redirect_stdout

    old = sys.argv
    sys.argv = ["audit_publish_preflight.py", "--json", *(argv or [])]
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            code = module.main()
    finally:
        sys.argv = old
    return code, json.loads(buffer.getvalue())


def _sidecar(root: Path, clip: str, **over) -> Path:
    body = {
        "clip_id": clip, "duration": 20.0,
        "source_start": 100.0, "source_end": 120.0,
        "caption_plan": {"y_pct": 0.75,
                         "style": {"text_color": "#FFFFFF",
                                   "outline_color": "#000000",
                                   "outline_width": 5,
                                   "highlight_color": "#FFFFFF",
                                   "position": "bottom"}},
        "dynamic_plan": {"shots": [{"index": 0, "composition": "crop",
                                    "t0": 0.0, "t1": 20.0,
                                    "anchor": [304, 540], "shake": 0.0,
                                    "rect": {"x": 0, "y": 0,
                                             "w": 608, "h": 1080}}],
                         "src_w": 1920, "src_h": 1080},
        "layout_plan": {"safe_zones": {"top": 200, "keep_out": []}},
    }
    body.update(over)
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    path = exports / f"{clip}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# --- every clip gets a row, and the corrupt one fails the run ----------------


def test_a_corrupt_sub_record_gets_a_row_and_a_non_zero_exit(tmp_path,
                                                             monkeypatch):
    """A valid clip, a corrupt one, then another valid one. All three have to
    be read, and the run has to fail — naming the clip it could not measure."""
    root = tmp_path / "clipper" / "p"
    _sidecar(root, "a")
    _sidecar(root, "b", caption_plan=[1])
    _sidecar(root, "c")

    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module)

    assert out["clips"] == 3, "the corrupt one stays in the denominator"
    assert code != 0, "a corpus it could not fully read is not a clean run"
    assert any("p/b" in line for line in out["failures"])


def test_a_sidecar_nobody_can_parse_fails_the_run(tmp_path, monkeypatch):
    root = tmp_path / "clipper" / "p"
    _sidecar(root, "a")
    (root / "exports" / "b.json").write_text("{not json", encoding="utf-8")

    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module)
    assert code != 0 and out["clips"] == 2
    assert any("p/b" in line for line in out["failures"])


def test_an_empty_corpus_is_not_a_pass(tmp_path, monkeypatch):
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module)
    assert code != 0 and out["clips"] == 0


# --- UNDECIDED is a verdict, not a failure -----------------------------------


def test_a_corpus_of_undecided_clips_still_exits_zero(tmp_path, monkeypatch):
    """THE DESCRIPTIVE CONTRACT. Most of the seven have no input for most
    clips; that is the finding, and a permanently red gate would bury it. The
    publish gate is the other contract and nothing is wired to it."""
    root = tmp_path / "clipper" / "p"
    _sidecar(root, "a")

    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module)
    assert code == 0
    assert out["verdicts"] == {"UNDECIDED": 1}
    assert out["integrity"] is True


# --- the OCR cache does not live in the sidecar namespace --------------------


def test_the_chrome_cache_is_not_written_where_sidecars_are_read(tmp_path):
    """`exports/*.json` is a clip's sidecar in six places in this repo. The
    cache used to land there as `<clip>.chrome.json`, and one such file was
    already enough to make the R0 gate report an orphan sidecar and exit 2."""
    import importlib.util

    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "audit_publish_preflight.py")
    spec = importlib.util.spec_from_file_location("audit_pp_cache", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    sidecar = tmp_path / "clipper" / "p" / "exports" / "a.json"
    cache = module._cache_path(sidecar)
    assert cache.parent != sidecar.parent
    assert not str(cache).startswith(str(sidecar.parent))
    assert cache.name == "a.json"


def test_a_cached_verdict_does_not_survive_a_re_render(tmp_path):
    """It was accepted on `state` and cadence alone, so a cache outlived the
    very export it described — and the OCR that produced it cost minutes, which
    is exactly why nobody would notice it was stale."""
    import importlib.util

    from services.clipper import source_chrome as sc

    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "audit_publish_preflight.py")
    spec = importlib.util.spec_from_file_location("audit_pp_identity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"one render")
    good = {"schema": "source_chrome_v1", "state": sc.NOT_DETECTED,
            "measured_with": module._config(),
            "measured_on": module._identity(mp4)}
    assert module._usable(good, mp4)

    mp4.write_bytes(b"a different render entirely")
    assert not module._usable(good, mp4), "a new file is not the measured one"


def test_a_cache_measured_at_another_configuration_is_not_reused(tmp_path):
    """`FRAMES_MIN` counts frames and a frame is a different amount of video at
    every cadence, so the whole configuration travels with the verdict."""
    import importlib.util

    from services.clipper import source_chrome as sc

    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "audit_publish_preflight.py")
    spec = importlib.util.spec_from_file_location("audit_pp_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"one render")
    base = {"schema": "source_chrome_v1", "state": sc.NOT_DETECTED,
            "measured_with": module._config(),
            "measured_on": module._identity(mp4)}
    for key, value in (("schema", "source_chrome_v0"), ("state", "probably")):
        assert not module._usable({**base, key: value}, mp4), key
    assert not module._usable({k: v for k, v in base.items()
                               if k != "measured_on"}, mp4)

    # EVERY numeric constant, enumerated from the module rather than listed by
    # hand: a hand-kept list goes stale exactly once and silently, which is what
    # `SAMPLES_MIN` moving from 6 to 7 did — every cached verdict stayed valid
    # while the classifier had started refusing the same six frames.
    assert module._config(), "the configuration is not empty"
    for name in module._config():
        spoiled = {**base["measured_with"], name: 999}
        assert not module._usable({**base, "measured_with": spoiled}, mp4), name
