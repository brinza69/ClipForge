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


def _has(entries, name: str, kind: str) -> bool:
    """Is this input missing for this REASON. The kind is the contract — the
    audit used to sort these by grepping the wording, and five separately
    corrupt inputs got through because their producers phrased it differently."""
    return any(e["input"] == name and e["kind"] == kind for e in entries)


WORDS = [{"word": "Hello", "start": 100.0, "end": 100.4},
         {"word": "there.", "start": 100.4, "end": 101.0}]


# --- matching a clip to its candidate ----------------------------------------


def test_a_candidate_has_to_match_on_BOTH_edges(tmp_path):
    """R5a moved 261 window ends by a median of 0.10s, so a match on one edge
    is the exact shape a stale attachment would take."""
    root = _project(tmp_path)
    path = _sidecar(root)
    _candidates(root, [{"start": 100.0, "end": 119.5, "words": WORDS}])
    got = pcorp.assemble(path, words=WORDS)
    assert got["inputs"]["boundary_view"] is None
    assert _has(got["missing"], "candidate", pcorp.ABSENT)

    _candidates(root, [{"start": 100.0, "end": 120.0, "words": WORDS}])
    got = pcorp.assemble(path, words=WORDS)
    assert got["inputs"]["boundary_view"] is not None
    assert not [m for m in got["missing"] if m["input"].startswith("candidate")]


# --- the boundary is measured against the WHOLE transcript -------------------


def test_the_boundary_is_not_measured_against_the_candidates_own_words(tmp_path):
    """`cand["words"]` is `_neighbourhood`'s `inside` list, which drops a word
    straddling either edge — and a straddling word is exactly what
    `start_inside_word` and `end_inside_word` look for. Fed its own list, the
    check searched the one collection built by removing what it searches for,
    and every window came back clean on the defect R5a spent a batch removing
    from 261 of them.

    The same window, the two word lists, the two answers.
    """
    from services.clipper.boundary_completion import boundary_view

    # "there." runs from 119.8 to 120.4 — the cut at 120.0 lands inside it.
    whole = [{"word": "Hello", "start": 100.0, "end": 100.4},
             {"word": "there.", "start": 119.8, "end": 120.4}]
    inside_only = [w for w in whole if w["end"] <= 120.0]
    cand = {"start": 100.0, "end": 120.0}

    blind = boundary_view(cand, inside_only, max_s=20.0)
    seeing = boundary_view(cand, whole, max_s=20.0)
    assert "end_inside_word" not in blind["defects"], "the defect it cannot see"
    assert "end_inside_word" in seeing["defects"]

    root = _project(tmp_path)
    path = _sidecar(root)
    _candidates(root, [{"start": 100.0, "end": 120.0, "words": inside_only}])
    got = pcorp.preflight_for(path, words=whole)
    assert got["checks"][pf.BOUNDARY]["state"] == pf.FAIL
    assert "end_inside_word" in got["checks"][pf.BOUNDARY]["why"]


def test_no_transcript_means_no_boundary_verdict(tmp_path):
    """The words live in the database and this module does not touch it, so a
    caller who supplies none gets `unavailable` rather than a verdict built
    from whatever happened to be lying next to the candidate."""
    root = _project(tmp_path)
    path = _sidecar(root)
    _candidates(root, [{"start": 100.0, "end": 120.0, "words": WORDS}])
    got = pcorp.preflight_for(path)
    assert _has(got["missing_inputs"], "transcript_words", pcorp.ABSENT)
    assert got["checks"][pf.BOUNDARY]["state"] == pf.UNAVAILABLE


def test_missing_candidates_are_named_rather_than_shrugged_at(tmp_path):
    root = _project(tmp_path)
    path = _sidecar(root)
    assert _has(pcorp.assemble(path)["missing"], "candidates", pcorp.ABSENT)

    (root / "analysis" / "candidates.json").write_text("{not json",
                                                       encoding="utf-8")
    assert _has(pcorp.assemble(path)["missing"], "candidates", pcorp.UNREADABLE)

    (root / "analysis" / "candidates.json").write_text("{}", encoding="utf-8")
    assert _has(pcorp.assemble(path)["missing"], "candidates", pcorp.REFUSED)


def test_a_sidecar_with_no_window_cannot_be_matched(tmp_path):
    root = _project(tmp_path)
    path = _sidecar(root, source_end=None)
    _candidates(root, [{"start": 100.0, "end": 120.0, "words": WORDS}])
    assert _has(pcorp.assemble(path)["missing"], "source_window", pcorp.ABSENT)


# --- nothing is substituted ---------------------------------------------------


def test_the_two_ocr_signals_are_absent_until_they_are_paid_for(tmp_path):
    """The source-caption verdict costs about five seconds per project and the
    chrome verdict several per frame. Neither is invented."""
    root = _project(tmp_path)
    path = _sidecar(root)
    got = pcorp.assemble(path)
    assert got["inputs"]["source_captions"] is None
    assert got["inputs"]["chrome"] is None
    assert _has(got["missing"], "source_captions", pcorp.ABSENT)
    assert _has(got["missing"], "chrome", pcorp.ABSENT)


def test_a_supplied_source_verdict_reaches_the_checks(tmp_path):
    """`present` is the state that rejects: 15 of 15 go ghost exports shipped
    with two caption systems, and nothing a correction can move.

    IT TAKES BOTH LAYERS. With no `.ass` beside the render, ClipForge burned
    nothing, so a source that carries captions is ONE layer — and the assembler
    is what knows which. It reads the `.ass` through `caption_corpus.measure`,
    which is why this file has to exist for the reject to be about the export.
    """
    from services.clipper import source_captions as scap

    root = _project(tmp_path)
    path = _sidecar(root)
    _candidates(root, [{"start": 100.0, "end": 120.0, "words": WORDS}])

    alone = pcorp.preflight_for(path, source_captions={"state": scap.PRESENT})
    assert alone["checks"][pf.CAPTIONS]["state"] != pf.FAIL, "one layer, not two"

    path.with_suffix(".ass").write_text(
        "[Events]\nDialogue: 0,0:00:00.00,0:00:02.00,D,,0,0,0,,"
        "{\\pos(540,1440)}hello\n", encoding="utf-8")
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
    assert _has(got["missing_inputs"], "sidecar", pcorp.UNREADABLE)
    assert got["verdict"] == pf.UNDECIDED
    assert set(got["checks"]) == set(pf.CHECKS)


def test_a_sub_record_that_is_not_a_record_does_not_kill_the_run(tmp_path):
    """`(sidecar.get("caption_plan") or {}).get("style")` returns the LIST for
    `caption_plan: [1]`, and `.get` on a list raises — out of a loop over the
    corpus, so the audit reported on the clips before the corrupt one and never
    said it had stopped. `caption_corpus.measure` refuses this shape by name; a
    refusal one function upstream does not protect a read two functions along.
    """
    root = _project(tmp_path)
    path = _sidecar(root, caption_plan=[1])
    got = pcorp.preflight_for(path)
    assert got["clip"] == "a", "it gets a row"
    assert got["verdict"] == pf.UNDECIDED
    assert _has(got["missing_inputs"], "caption_plan", pcorp.REFUSED)
    assert any(pcorp.is_integrity_failure(m) for m in got["missing_inputs"])


def test_the_report_carries_which_inputs_were_missing_beside_the_verdict(tmp_path):
    """`unavailable` says a check could not be made; `missing_inputs` says why,
    and the two are different halves of the same sentence."""
    root = _project(tmp_path)
    path = _sidecar(root)
    got = pcorp.preflight_for(path)
    assert got["verdict"] == pf.UNDECIDED
    assert got["missing_inputs"], "the reasons travel with the verdict"
    assert got["project"] == "p" and got["clip"] == "a"


# --- absent, refused and unreadable are three things -------------------------
#
# The audit used to sort them by looking for substrings in the reason text, and
# five separately corrupt inputs walked through with exit 0 because their
# producers phrased the refusal differently. The producer knows which happened;
# these pin each one to its kind.


def test_a_style_that_is_a_list_is_refused_not_absent(tmp_path):
    """It reached `caption_contrast` as "not a dict", came back
    `no_caption_style`, and was filed as an ABSENCE — so a corrupt record read
    as an export that simply has no captions."""
    root = _project(tmp_path)
    path = _sidecar(root, caption_plan={"y_pct": 0.75, "style": [1]})
    got = pcorp.assemble(path)
    assert _has(got["missing"], "caption_style", pcorp.REFUSED)
    assert any(pcorp.is_integrity_failure(m) for m in got["missing"])


def test_a_style_that_is_simply_absent_is_not_an_integrity_failure(tmp_path):
    """An export with no caption plan is an honest absence, and a corpus of them
    is a finding rather than a broken run."""
    root = _project(tmp_path)
    path = _sidecar(root, caption_plan=None)
    got = pcorp.assemble(path)
    assert not any(pcorp.is_integrity_failure(m) for m in got["missing"])


def test_a_nan_outline_width_is_refused_and_a_missing_one_is_absent(tmp_path):
    """`caption_contrast` files both under one `refused` list because for ITS
    purpose both mean no verdict. A NaN width is not a missing one."""
    root = _project(tmp_path)
    style = {"text_color": "#FFFFFF", "outline_color": "#000000",
             "highlight_color": "#FFFFFF"}
    gone = pcorp.assemble(_sidecar(root, clip="a",
                                   caption_plan={"style": style}))
    assert _has(gone["missing"], "contrast", pcorp.ABSENT)
    assert not any(pcorp.is_integrity_failure(m) for m in gone["missing"])

    bad = pcorp.assemble(_sidecar(
        root, clip="b",
        caption_plan={"style": {**style, "outline_color": "not a colour",
                                "outline_width": 5}}))
    assert _has(bad["missing"], "contrast", pcorp.REFUSED)


def test_a_sidecar_that_names_another_clip_is_refused(tmp_path):
    """`p/exports/a.json` declaring `clip_id: b` used to pass without comment,
    and every figure in the run would then be about a clip the row does not
    name. R0's gate already refuses that population."""
    root = _project(tmp_path)
    path = _sidecar(root, clip="a")
    path.write_text(path.read_text(encoding="utf-8").replace('"a"', '"b"', 1),
                    encoding="utf-8")
    got = pcorp.assemble(path)
    assert _has(got["missing"], "sidecar", pcorp.REFUSED)
    assert any("clip_id" in str(m["detail"]) for m in got["missing"])


def test_a_sidecar_that_names_another_project_is_refused(tmp_path):
    root = _project(tmp_path)
    path = _sidecar(root, project_id="somewhere_else")
    got = pcorp.assemble(path)
    assert any("project_id" in str(m["detail"]) for m in got["missing"])


def test_a_sidecar_with_no_render_beside_it_is_named(tmp_path):
    """A plan with no mp4 is a clip nothing can be said about. R0 calls it an
    orphan and exits 2; this audit must not report on a weaker population."""
    root = _project(tmp_path)
    got = pcorp.assemble(_sidecar(root))
    assert _has(got["missing"], "export", pcorp.ABSENT)


def test_every_missing_entry_carries_one_of_the_three_kinds(tmp_path):
    root = _project(tmp_path)
    got = pcorp.assemble(_sidecar(root))
    assert got["missing"]
    for entry in got["missing"]:
        assert set(entry) == {"input", "kind", "detail"}
        assert entry["kind"] in pcorp.MISSING_KINDS
