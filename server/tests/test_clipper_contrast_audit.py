"""Batch R6's contrast gate: what reaches the exit code.

Same rule as `test_clipper_caption_audit.py`, and it is in `CLAUDE.md` for the
same reason: a diagnostic that would invalidate the conclusion has to reach the
exit code, and needs a test through `main()` that demonstrates the non-zero exit.

The case this file is built around is the empty population. This script's whole
method is a sweep over colour pairs, and a sweep over an empty list is silent
and green — it would print "every palette clears the bar" over no palettes at
all, which is the same shape as the empty corpus that passed in Batch R5.
"""

from __future__ import annotations

import json
from pathlib import Path

WHITE, BLACK = "#FFFFFF", "#000000"


def _audit(monkeypatch, data_dir: Path):
    import importlib.util

    monkeypatch.setenv("CLIPFORGE_DATA_DIR", str(data_dir))
    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "audit_caption_contrast.py")
    spec = importlib.util.spec_from_file_location("audit_caption_contrast",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sidecar(tmp_path: Path, clip: str, style) -> None:
    exports = tmp_path / "clipper" / "p" / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / f"{clip}.json").write_text(
        json.dumps({"caption_plan": {"style": style}}), encoding="utf-8")


def _run(module, presets: dict | None = None) -> tuple[int, dict]:
    import io
    import sys
    from contextlib import redirect_stdout

    if presets is not None:
        module.DEFAULT_PRESETS = presets
    old = sys.argv
    sys.argv = ["audit_caption_contrast.py", "--json"]
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            code = module.main()
    finally:
        sys.argv = old
    return code, json.loads(buffer.getvalue())


GOOD = {"text_color": WHITE, "highlight_color": WHITE, "outline_color": BLACK}


def test_an_empty_preset_list_is_not_a_pass(tmp_path, monkeypatch):
    """A sweep over nothing is silent and green."""
    _sidecar(tmp_path, "a", GOOD)
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, presets={})
    assert code == 1
    assert any("sweep over nothing" in line for line in out["failures"])


def test_an_empty_corpus_is_not_a_pass(tmp_path, monkeypatch):
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, presets={"a": {"name": "A", **GOOD}})
    assert code == 1
    assert any("sweep over nothing" in line for line in out["failures"])


def test_a_clean_population_passes(tmp_path, monkeypatch):
    _sidecar(tmp_path, "a", GOOD)
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, presets={"a": {"name": "A", **GOOD}})
    assert out["failures"] == [] and code == 0


def test_the_two_real_presets_fail_the_run(tmp_path, monkeypatch):
    """The finding, through `main()`: it is in the palette, and looking only at
    the body text would have called both clean."""
    _sidecar(tmp_path, "a", GOOD)
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, presets={
        "neon": {"name": "Neon Pop", "text_color": WHITE,
                 "highlight_color": "#FF3366", "outline_color": "#1A0033"},
        "viral": {"name": "Viral Gradient", "text_color": WHITE,
                  "highlight_color": "#FF6B35", "outline_color": BLACK}})
    assert code == 1
    assert len(out["failures"]) == 2
    assert all("highlight floor" in line for line in out["failures"])


def test_an_unreadable_colour_fails_the_run(tmp_path, monkeypatch):
    _sidecar(tmp_path, "a", {"text_color": "puce", "outline_color": BLACK})
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, presets={"a": {"name": "A", **GOOD}})
    assert code == 1
    assert any("colour_not_a_hex_triplet" in line for line in out["failures"])


def test_an_absent_style_is_counted_and_not_failed(tmp_path, monkeypatch):
    """An export can genuinely carry no captions, and a sidecar can have lost
    the style, and nothing here tells the two apart — so it is `unavailable`,
    exactly as `audit_caption_placement` treats the same fact. Failing would
    keep the gate permanently red for a legitimate state."""
    _sidecar(tmp_path, "a", GOOD)
    _sidecar(tmp_path, "b", None)
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, presets={"a": {"name": "A", **GOOD}})
    assert out["sidecars_read"] == 2
    assert any(r["refused"] == ["no_caption_style"]
               for r in out["distinct_palettes_on_disk"])
    assert out["failures"] == [] and code == 0


def test_an_unreadable_sidecar_fails_and_keeps_its_row(tmp_path, monkeypatch):
    _sidecar(tmp_path, "a", GOOD)
    (tmp_path / "clipper" / "p" / "exports" / "b.json").write_text(
        "{not json", encoding="utf-8")
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, presets={"a": {"name": "A", **GOOD}})
    assert out["sidecars_refused"], "it did not vanish"
    assert code == 1


def test_a_style_stored_as_an_unparsable_repr_is_refused(tmp_path, monkeypatch):
    """`caption_plan.style` is sometimes a repr rather than JSON, which the
    clipper has stored since it shipped. A repr that will not parse is a
    refusal, not an absent style."""
    _sidecar(tmp_path, "a", GOOD)
    _sidecar(tmp_path, "b", "{'text_color': ")
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, presets={"a": {"name": "A", **GOOD}})
    assert any("style_repr_unparsable" in line
               for line in out["sidecars_refused"])
    assert code == 1


def test_a_style_stored_as_a_parsable_repr_is_read(tmp_path, monkeypatch):
    _sidecar(tmp_path, "a", str(GOOD))
    module = _audit(monkeypatch, tmp_path)
    code, out = _run(module, presets={"a": {"name": "A", **GOOD}})
    assert out["distinct_palettes_on_disk"][0]["fill"]["floor"] == 4.61
    assert code == 0
