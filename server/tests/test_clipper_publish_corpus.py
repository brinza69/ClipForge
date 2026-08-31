"""Batch R7: assembling one clip's seven answers from what is on disk.

The assembler's whole job is to find each input or say which one it could not
find. These tests are about the second half: nothing is substituted, nothing is
defaulted, and a missing input becomes `unavailable` rather than a pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.clipper import publish_corpus as pcorp
from services.clipper import publish_preflight as pf


def _project(tmp_path: Path, name: str = "p") -> Path:
    d = tmp_path / "clipper" / name
    (d / "exports").mkdir(parents=True, exist_ok=True)
    (d / "analysis").mkdir(parents=True, exist_ok=True)
    return d


def _sidecar(root: Path, clip: str = "a", **over) -> Path:
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
    path = root / "exports" / f"{clip}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _candidates(root: Path, rows) -> None:
    (root / "analysis" / "candidates.json").write_text(
        json.dumps(rows), encoding="utf-8")


WORDS = [{"word": "Hello", "start": 100.0, "end": 100.4},
         {"word": "there.", "start": 100.4, "end": 101.0}]


# --- matching a clip to its candidate ----------------------------------------


def test_a_candidate_has_to_match_on_BOTH_edges(tmp_path):
    """R5a moved 261 window ends by a median of 0.10s, so a match on one edge
    is the exact shape a stale attachment would take."""
    root = _project(tmp_path)
    path = _sidecar(root)
    _candidates(root, [{"start": 100.0, "end": 119.5, "words": WORDS}])
    got = pcorp.assemble(path)
    assert got["inputs"]["boundary_view"] is None
    assert "no_candidate_matches_this_window_on_both_edges" in got["missing"]

    _candidates(root, [{"start": 100.0, "end": 120.0, "words": WORDS}])
    got = pcorp.assemble(path)
    assert got["inputs"]["boundary_view"] is not None
    assert not [m for m in got["missing"] if "candidate" in m]


def test_missing_candidates_are_named_rather_than_shrugged_at(tmp_path):
    root = _project(tmp_path)
    path = _sidecar(root)
    assert "no_candidates_on_disk" in pcorp.assemble(path)["missing"]

    (root / "analysis" / "candidates.json").write_text("{not json",
                                                       encoding="utf-8")
    assert "candidates_unreadable" in pcorp.assemble(path)["missing"]

    (root / "analysis" / "candidates.json").write_text("{}", encoding="utf-8")
    assert "candidates_not_a_list" in pcorp.assemble(path)["missing"]


def test_a_sidecar_with_no_window_cannot_be_matched(tmp_path):
    root = _project(tmp_path)
    path = _sidecar(root, source_end=None)
    _candidates(root, [{"start": 100.0, "end": 120.0, "words": WORDS}])
    assert "sidecar_has_no_source_window" in pcorp.assemble(path)["missing"]


# --- nothing is substituted ---------------------------------------------------


def test_the_two_ocr_signals_are_absent_until_they_are_paid_for(tmp_path):
    """The source-caption verdict costs about five seconds per project and the
    chrome verdict several per frame. Neither is invented."""
    root = _project(tmp_path)
    path = _sidecar(root)
    got = pcorp.assemble(path)
    assert got["inputs"]["source_captions"] is None
    assert got["inputs"]["chrome"] is None
    assert "no_source_caption_verdict" in got["missing"]
    assert "no_chrome_verdict" in got["missing"]


def test_a_supplied_source_verdict_reaches_the_checks(tmp_path):
    """`present` is the state that rejects: 15 of 15 go ghost exports shipped
    with two caption systems, and nothing a correction can move."""
    from services.clipper import source_captions as scap

    root = _project(tmp_path)
    path = _sidecar(root)
    _candidates(root, [{"start": 100.0, "end": 120.0, "words": WORDS}])
    got = pcorp.preflight_for(path, source_captions={"state": scap.PRESENT})
    assert got["checks"][pf.CAPTIONS]["state"] == pf.FAIL
    assert got["checks"][pf.CAPTIONS]["severity"] == pf.REJECTABLE
    assert got["verdict"] == pf.REJECT


def test_an_absent_source_verdict_does_not_make_the_captions_check_pass(tmp_path):
    """The other two halves — what the caption covers and whether the palette
    can be read — are still unmeasured, so the check stays unavailable."""
    from services.clipper import source_captions as scap

    root = _project(tmp_path)
    path = _sidecar(root)
    got = pcorp.preflight_for(path, source_captions={"state": scap.ABSENT})
    assert got["checks"][pf.CAPTIONS]["state"] == pf.UNAVAILABLE


# --- an unreadable sidecar ----------------------------------------------------


def test_an_unreadable_sidecar_is_named_and_stops_nothing_else(tmp_path):
    root = _project(tmp_path)
    path = root / "exports" / "a.json"
    path.write_text("{not json", encoding="utf-8")
    got = pcorp.preflight_for(path)
    assert any(m.startswith("sidecar_unreadable") for m in got["missing_inputs"])
    assert got["verdict"] == pf.UNDECIDED
    assert set(got["checks"]) == set(pf.CHECKS)


def test_the_report_carries_which_inputs_were_missing_beside_the_verdict(tmp_path):
    """`unavailable` says a check could not be made; `missing_inputs` says why,
    and the two are different halves of the same sentence."""
    root = _project(tmp_path)
    path = _sidecar(root)
    got = pcorp.preflight_for(path)
    assert got["verdict"] == pf.UNDECIDED
    assert got["missing_inputs"], "the reasons travel with the verdict"
    assert got["project"] == "p" and got["clip"] == "a"
