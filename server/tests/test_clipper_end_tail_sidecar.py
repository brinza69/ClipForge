"""EN3T (codex-verdict-next-23 §3), EN3Tr (next-29 R1/R2): EN3's evidence reaches the export sidecar, bound
to the file actually rendered. Kept from the scoring on the clip's `reasoning`; `applies` only when the recorded
end is the executed window's end AND the probed file's; an edited end is never reported as applied; an invalid
record is its own state; no record is `absent`, never rebuilt from today's setting; outside the fingerprint.
The real-encoder witnesses are in test_clipper_end_tail_render.py.
"""
from __future__ import annotations

import types

import pytest

from services.clipper import output_identity, render_input
from services.clipper.end_tail import sidecar_block
from workers import clipper_render_output as output
from workers.clipper_finalize import _reasoning_of

MOVED = {"rule": "end_tail_v1", "target_s": 0.4, "end_in": 891.39, "vocal_end": 891.248, "next_speech": 894.0,
         "requested_end": 891.648, "state": "moved", "end_out": 891.648}
REFUSED = {"rule": "end_tail_v1", "target_s": 0.4, "end_in": 458.1, "vocal_end": 458.0, "next_speech": 458.36,
           "requested_end": 458.4, "state": "refused", "why": "enters_next_speech"}
AUDIO = {"duration_s": 73.448, "has_audio": True, "refused": None}


def _rec(ss, t):
    return {"window": {"ss": ss, "t": t}}


def _block(ev, clip_end=891.648, record=None, drop=(), fps=60, out=None, reasoning=None):
    return sidecar_block(reasoning if reasoning is not None else {"end_tail": ev}, clip_end,
                         record if record is not None else _rec(818.2, 73.448), list(drop), fps,
                         out if out is not None else AUDIO)


def test_the_scoring_keeps_the_tail_evidence_on_the_clip():
    assert _reasoning_of({"reasons": ["x"], "end_evidence": {"decision": "tail_extended", "tail": MOVED}})[
        "end_tail"] == MOVED
    assert "end_tail" not in (_reasoning_of({"reasons": ["x"], "end_evidence": {"decision": "x"}}) or {})
    assert "end_tail" not in (_reasoning_of({"reasons": ["x"], "end_evidence": "garbled"}) or {})


def test_a_moved_end_that_the_file_carries_applies_and_the_next_speech_is_not_an_event_of_the_file():
    got = _block(MOVED)
    assert got["binding"] == "applies" and got["recorded"] == MOVED and got["clock"] == "source_s"
    # 894.0 is 75.8 s into the clip; the file ends at 73.448, so it is context in `recorded`, not a time here.
    assert got["delivered_s"] == {"vocal_end": 73.048, "next_speech": None, "end": 73.448}
    assert got["executed"] == {"start_s": 818.2, "duration_s": 73.448}
    assert got["measured_duration_s"] == 73.448 and got["tolerance_s"] == pytest.approx(1 / 60 + 1024 / 48000
                                                                                         + 0.001, abs=1e-4)


def test_the_drop_spans_move_the_delivered_clock_and_a_dropped_time_has_none():
    drop = [[15.85, 17.5], [24.6, 25.5], [36.35, 37.75]]
    t = round(73.448 - 3.95, 3)
    got = _block(MOVED, record=_rec(818.2, t), drop=drop, out={**AUDIO, "duration_s": t})
    assert got["binding"] == "applies"
    assert got["delivered_s"]["end"] == t and got["delivered_s"]["vocal_end"] == round(73.048 - 3.95, 3)
    inside = _block({**MOVED, "vocal_end": 818.2 + 16.0}, record=_rec(818.2, 73.448 - 1.65),
                    drop=[[15.85, 17.5]], out={**AUDIO, "duration_s": 73.448 - 1.65})
    assert inside["binding"] == "applies" and inside["delivered_s"]["vocal_end"] is None


@pytest.mark.parametrize("vocal_end", [818.0, 891.7])
def test_a_time_before_or_after_the_delivered_window_is_none(vocal_end):
    assert _block({**MOVED, "vocal_end": vocal_end})["delivered_s"]["vocal_end"] is None


def test_a_refused_extension_applies_at_the_end_it_kept():
    got = _block(REFUSED, clip_end=458.1, record=_rec(416.78, 41.32), out={**AUDIO, "duration_s": 41.333})
    assert got["binding"] == "applies" and got["recorded"]["state"] == "refused"
    assert got["delivered_s"] == {"vocal_end": 41.22, "next_speech": None, "end": 41.32}


@pytest.mark.parametrize("end", [891.39, 890.0, 892.0])
def test_an_end_edited_since_the_scoring_is_never_reported_as_applied(end):
    got = _block(MOVED, clip_end=end, record=_rec(818.2, round(end - 818.2, 3)),
                 out={**AUDIO, "duration_s": round(end - 818.2, 3)})
    assert got == {"binding": "end_changed_since_scoring", "recorded": MOVED, "clock": "source_s"}


@pytest.mark.parametrize("reasoning", [None, {}, {"end_tail": None}, {"end_tail": "moved"}, "garbled"])
def test_no_record_is_absent(reasoning):
    assert sidecar_block(reasoning, 10.0, _rec(0.0, 10.0), [], 30, AUDIO) == {"binding": "absent",
                                                                              "recorded": None}


@pytest.mark.parametrize("change,why", [
    ({"end_out": True}, "recorded_end_not_a_finite_number"),
    ({"end_out": float("nan")}, "recorded_end_not_a_finite_number"),
    ({"end_out": float("inf")}, "recorded_end_not_a_finite_number"),
    ({"end_out": float("-inf")}, "recorded_end_not_a_finite_number"),
    ({"end_out": None}, "recorded_end_not_a_finite_number"),
    ({"vocal_end": float("nan")}, "recorded_time_not_a_finite_number"),
    ({"next_speech": float("inf")}, "recorded_time_not_a_finite_number"),
    ({"next_speech": False}, "recorded_time_not_a_finite_number"),
    ({"rule": "end_tail_v0"}, "unknown_rule"),
    ({"state": "extended"}, "unknown_state"),
    ({"state": None}, "unknown_state"),
])
def test_an_invalid_record_is_its_own_state_even_when_a_number_matches(change, why):
    # Every one of these records carries an end that matches the file (891.648 or end_in 891.39 is never used).
    got = _block({**MOVED, **change})
    assert got == {"binding": "invalid_record", "why": why, "recorded": {**MOVED, **change}}


def test_codex_case_a_plan_of_another_length_is_not_corroborated():
    """next29-check/probe_tail.py: the clip asks for 2 s, the executed plan for 1 s, the file is 1 s."""
    ev = dict(rule="end_tail_v1", state="moved", target_s=.4, end_in=1.95, vocal_end=1.85, next_speech=2.7,
              requested_end=2.25, end_out=2.25)
    got = sidecar_block({"end_tail": ev}, 2.25, _rec(0.25, 1.0), [], 24,
                        {"duration_s": 1.0, "has_audio": False, "refused": None})
    assert got["binding"] == "not_corroborated" and got["why"] == "executed_window_ends_elsewhere"
    assert got["recorded"] == ev and "delivered_s" not in got
    nan = sidecar_block({"end_tail": {**ev, "end_out": float("nan")}}, 2.25, _rec(0.25, 2.0), [], 24,
                        {"duration_s": 2.0, "has_audio": False, "refused": None})
    assert nan["binding"] == "invalid_record"


@pytest.mark.parametrize("record,drop", [
    (None, []), ({}, []), ({"window": None}, []), (_rec(None, 73.448), []), (_rec(818.2, float("nan")), []),
    (_rec(818.2, True), []), (_rec(818.2, 73.448), [[1.0, float("nan")]]), (_rec(818.2, 73.448), [[1.0]]),
])
def test_an_unreadable_executed_window_is_not_corroborated(record, drop):
    got = sidecar_block({"end_tail": MOVED}, 891.648, record, drop, 60, AUDIO)
    assert got["binding"] == "not_corroborated" and got["why"] == "executed_window_unavailable"


@pytest.mark.parametrize("out,fps", [
    ({"schema": output_identity.SCHEMA, "refused": output_identity.NO_FILE}, 60), (None, 60),
    ({**AUDIO, "duration_s": None}, 60), ({**AUDIO, "duration_s": float("nan")}, 60),
    (AUDIO, 0), (AUDIO, None), (AUDIO, True),
])
def test_an_unavailable_output_probe_is_not_corroborated(out, fps):
    got = sidecar_block({"end_tail": MOVED}, 891.648, _rec(818.2, 73.448), [], fps, out)
    assert got["binding"] == "not_corroborated" and got["why"] == "output_probe_unavailable"
    assert "delivered_s" not in got and got["recorded"] == MOVED


@pytest.mark.parametrize("delta,audio,binding", [
    (0.038, True, "applies"), (-0.038, True, "applies"), (0.041, True, "not_corroborated"),
    (-0.041, True, "not_corroborated"), (0.017, False, "applies"), (0.019, False, "not_corroborated"),
])
def test_the_output_tolerance_is_a_frame_plus_an_audio_frame(delta, audio, binding):
    # 60 fps: 1/60 + 1024/48000 + 0.001 = 0.039 with audio, 0.0177 without.
    got = _block(MOVED, out={"duration_s": round(73.448 + delta, 3), "has_audio": audio, "refused": None})
    assert got["binding"] == binding
    if binding != "applies":
        assert got["why"] == "output_duration_differs"


def test_a_provenance_is_copied_beside_the_record_and_the_binding_is_still_computed_here():
    prov = {"from": "frozen_sidecar", "sidecar_sha256": "ab"}
    assert _block(MOVED, reasoning={"end_tail": MOVED, "end_tail_provenance": prov})["provenance"] == prov
    edited = _block(MOVED, clip_end=890.0, reasoning={"end_tail": MOVED, "end_tail_provenance": prov})
    assert edited["binding"] == "end_changed_since_scoring" and edited["provenance"] == prov
    bad = _block(MOVED, reasoning={"end_tail": {**MOVED, "rule": None}, "end_tail_provenance": prov})
    assert bad["binding"] == "invalid_record" and bad["provenance"] == prov
    assert "provenance" not in _block(MOVED)
    assert "provenance" not in _block(MOVED, reasoning={"end_tail": MOVED, "end_tail_provenance": "x"})


NO_FIELD = object()


def _write(tmp_path, monkeypatch, reasoning):
    monkeypatch.setattr(output_identity, "probe", lambda out: {"sha256": "x", "duration_s": 73.448,
                                                               "has_audio": True, "refused": None})
    src = tmp_path / "src.mp4"
    src.write_bytes(b"src")
    clip = types.SimpleNamespace(
        id="en3tc", selection_run_id=None, start_time=818.2, end_time=891.648, duration=73.448, title="t",
        headline_text=None, transcript_text="", overall_score=0.0, sub_scores=None, score_reason=None,
        caption_plan=None, content_type="gaming", ranker_version=None, reasoning=reasoning)
    if reasoning is NO_FIELD:
        del clip.reasoning
    project = types.SimpleNamespace(id="en3tp", source_url=None, analysis_version="v")
    decision = {"plan": {}, "drop": [], "caption_y": None, "edit_profile": None, "creator_view": None,
                "regime_view": None, "rhythm_view": None, "caption_policy": {"action": "burn"},
                "layout_policy": {}}
    return output._write_sidecar(clip, project, decision, tmp_path / "out.mp4", src=str(src), dyn=None,
                                 render={"fps": 60}, result={"render_record": _rec(818.2, 73.448)},
                                 review_result=None)


def test_the_sidecar_carries_the_block_and_the_fingerprint_ignores_it(tmp_path, monkeypatch):
    body = _write(tmp_path, monkeypatch, {"end_tail": MOVED})
    assert body["end_tail"]["binding"] == "applies"
    assert (tmp_path / "out.json").is_file()
    without = {k: v for k, v in body.items() if k != "end_tail"}
    schema = body["fingerprint_schema"]
    assert render_input.input_fingerprint(without, schema=schema) == body["input_fingerprint"]
    assert _write(tmp_path, monkeypatch, None)["input_fingerprint"] == body["input_fingerprint"]
    assert _write(tmp_path, monkeypatch, None)["end_tail"] == {"binding": "absent", "recorded": None}


def test_a_clip_with_no_reasoning_field_is_absent(tmp_path, monkeypatch):
    assert _write(tmp_path, monkeypatch, NO_FIELD)["end_tail"] == {"binding": "absent", "recorded": None}
