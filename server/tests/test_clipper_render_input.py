"""The render fingerprint: what it covers, and that it is rechecked.

Split from `test_clipper_edit_quality.py` at 500 lines, following the module
split. `render_input` is a different subject from the metrics: it is about what
an export was made FROM, and whether the digest beside a plan still describes
that plan.
"""

from __future__ import annotations

from services.clipper import edit_quality as eq
from tests.test_clipper_edit_quality import _crop_fit_fit_crop


# --- the fingerprint, rechecked rather than copied ---------------------------


def test_a_fingerprint_is_recomputed_not_believed():
    """Copying the stored digest made it decorative. An artifact whose plan was
    edited after the render carries a stale fingerprint and must NOT pass."""
    sidecar = _crop_fit_fit_crop()
    sidecar["input_fingerprint"] = eq.input_fingerprint(sidecar)
    assert eq.fingerprint_status(sidecar) == eq.FINGERPRINT_VALID

    sidecar["dynamic_plan"]["shots"][0]["composition"] = "fit"
    assert eq.fingerprint_status(sidecar) == eq.FINGERPRINT_MISMATCH


def test_an_unstamped_sidecar_has_no_fingerprint_verdict():
    assert eq.fingerprint_status(_crop_fit_fit_crop()) == eq.UNAVAILABLE


def test_the_fingerprint_ignores_everything_that_only_labels_the_result():
    """Scores, titles and ids identify the result, not the recipe. Including
    them would make every rescore look like a different edit."""
    sidecar = _crop_fit_fit_crop()
    before = eq.input_fingerprint(sidecar)
    sidecar.update({"title": "new", "overall_score": 91.2, "review": {"verdict": "OK"},
                    "clip_id": "other", "headline": "x"})
    assert eq.input_fingerprint(sidecar) == before


def test_the_fingerprint_survives_a_json_round_trip():
    """The export writes it from live objects; the audit rechecks it from the
    file. If those two disagree, every stamped export reads as `mismatch`."""
    import json

    sidecar = _crop_fit_fit_crop()
    sidecar["drop_spans"] = [(1.0, 1.4)]
    sidecar["caption_y"] = 0.62
    sidecar["input_fingerprint"] = eq.input_fingerprint(sidecar)
    reloaded = json.loads(json.dumps(sidecar, default=str))
    assert eq.fingerprint_status(reloaded) == eq.FINGERPRINT_VALID
