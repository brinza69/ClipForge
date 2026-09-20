"""Tests for dynamic_face_envelope.

PRP-required scenarios:
- Tall/narrow box uses height without changing observed width or game windows.
- Square boxes retain scale.
- No changed election with a competing face.
- Anisotropic proxy scaling.
- Local half-open interval / terminal sample.
- Exact-clock terminal sample: float-subtraction trap avoided by absolute t.
- Missing evidence.
- Fixed anchor excluded from envelope widening.
- Delivered windows contain proposals at image edges.
- Impossible union fit.
- Effects cannot undo widening.
- Geometry-only moving-target checks are in test_dynamic_face_envelope_geometry.py.
  The real MP4 test lives in test_clipper_framing_contract.py.
"""
import pytest

from services.clipper.dynamic_face_envelope import (
    compute_framing_span,
    elected_spans,
    local_proposals,
    widen_for_envelope,
)
from services.clipper.dynamic_cameras import camera_rects, CAMERAS


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _track(t, x, y, w, h, clip_start=0.0):
    """Minimal face_track entry with absolute t."""
    return {"t": t + clip_start, "boxes": [[x, y, w, h]]}


def _elected(t, cx, cy, w):
    """Clip-relative (t, cx, cy, w) matching _face_samples output (sx=sy=1)."""
    return (float(t), float(cx), float(cy), float(w))


# ---------------------------------------------------------------------------
# elected_spans: height recovery
# ---------------------------------------------------------------------------

class TestElectedSpans:
    def test_square_box_returns_equal_w_h(self):
        track = [_track(1.0, 10, 20, 92, 92)]
        # sx=sy=1: src_cx=(10+46)*1=56, src_cy=(20+46)*1=66, src_w=92
        elected = [_elected(1.0, 56.0, 66.0, 92.0)]
        spans = elected_spans(track, elected, sx=1.0, sy=1.0, clip_start=0.0)
        assert len(spans) == 1
        abs_t, cx, cy, w, h = spans[0]
        assert abs_t == pytest.approx(1.0)
        assert w == pytest.approx(92.0)
        assert h == pytest.approx(92.0)

    def test_tall_box_recovers_height(self):
        track = [_track(1.0, 0, 0, 64, 84)]
        elected = [_elected(1.0, 32.0, 42.0, 64.0)]
        spans = elected_spans(track, elected, sx=1.0, sy=1.0, clip_start=0.0)
        assert len(spans) == 1
        _, _, _, w, h = spans[0]
        assert w == pytest.approx(64.0)
        assert h == pytest.approx(84.0)

    def test_abs_t_stored_not_clip_relative(self):
        clip_start = 5.0
        track = [_track(1.0, 0, 0, 64, 84, clip_start=clip_start)]
        # clip_t=1.0, abs_t=6.0; elected clip-relative t=1.0
        elected = [_elected(1.0, 32.0, 42.0, 64.0)]
        spans = elected_spans(track, elected, sx=1.0, sy=1.0, clip_start=clip_start)
        assert len(spans) == 1
        abs_t = spans[0][0]
        assert abs_t == pytest.approx(clip_start + 1.0)  # absolute, not clip-relative

    def test_anisotropic_proxy_scaling(self):
        # proxy box at (10, 5, 16, 21); sx=4, sy=4
        track = [_track(0.5, 10, 5, 16, 21)]
        # _face_samples: cx=(10+8)*4=72, cy=(5+10.5)*4=62, w=64
        elected = [_elected(0.5, 72.0, 62.0, 64.0)]
        spans = elected_spans(track, elected, sx=4.0, sy=4.0, clip_start=0.0)
        assert len(spans) == 1
        _, _, _, w, h = spans[0]
        assert w == pytest.approx(64.0)   # 16 * sx=4
        assert h == pytest.approx(84.0)   # 21 * sy=4

    def test_clip_start_offset(self):
        clip_start = 10.0
        track = [_track(0.0, 0, 0, 50, 70, clip_start=clip_start)]
        elected = [_elected(0.0, 25.0, 35.0, 50.0)]  # clip-relative t=0
        spans = elected_spans(track, elected, sx=1.0, sy=1.0, clip_start=clip_start)
        assert len(spans) == 1
        _, _, _, w, h = spans[0]
        assert h == pytest.approx(70.0)

    def test_no_match_drops_elected_sample(self):
        # No track entry matching this elected sample â†’ empty result, no square fallback
        track = [_track(5.0, 0, 0, 64, 84)]
        elected = [_elected(99.0, 32.0, 42.0, 64.0)]  # different t
        spans = elected_spans(track, elected, sx=1.0, sy=1.0, clip_start=0.0)
        assert spans == []

    def test_empty_track_drops_all_elected(self):
        spans = elected_spans([], [_elected(1.0, 50.0, 50.0, 64.0)],
                              sx=1.0, sy=1.0, clip_start=0.0)
        assert spans == []

    def test_empty_elected_returns_empty(self):
        track = [_track(1.0, 0, 0, 64, 84)]
        spans = elected_spans(track, [], sx=1.0, sy=1.0, clip_start=0.0)
        assert spans == []

    def test_other_face_different_centre_not_matched(self):
        # Same timestamp and width but different centre â€” must NOT match
        track = [_track(1.0, 100, 100, 64, 200)]  # cx=(100+32)=132, cy=132
        elected = [_elected(1.0, 32.0, 42.0, 64.0)]   # different centre
        spans = elected_spans(track, elected, sx=1.0, sy=1.0, clip_start=0.0)
        assert spans == []

    def test_nearby_time_not_matched(self):
        # 1ms offset must not supply height (no tolerance)
        track = [_track(1.001, 0, 0, 64, 84)]
        elected = [_elected(1.0, 32.0, 42.0, 64.0)]
        spans = elected_spans(track, elected, sx=1.0, sy=1.0, clip_start=0.0)
        assert spans == []

    def test_same_timestamp_two_entries_only_matching_one_used(self):
        # Two track entries at same time; only the one whose largest-box
        # computes to the elected (cx, cy, w) contributes height.
        track = [
            {"t": 1.0, "boxes": [[20, 30, 20, 20]]},   # cx=30, cy=40, w=20
            {"t": 1.0, "boxes": [[200, 100, 20, 100]]}, # cx=210, cy=150 â€” no match
        ]
        elected = [_elected(1.0, 30.0, 40.0, 20.0)]
        spans = elected_spans(track, elected, sx=1.0, sy=1.0, clip_start=0.0)
        assert len(spans) == 1
        _, _, _, w, h = spans[0]
        assert h == pytest.approx(20.0)  # from the matching entry


# ---------------------------------------------------------------------------
# compute_framing_span
# ---------------------------------------------------------------------------

class TestComputeFramingSpan:
    def test_square_boxes_retain_scale(self):
        # abs_t does not matter for span computation
        spans = [(6.0, 50.0, 60.0, 92.0, 92.0), (7.0, 50.0, 60.0, 92.0, 92.0)]
        span, basis = compute_framing_span(spans, face_w=92.0)
        assert span == pytest.approx(92.0)
        assert basis == "elected_observation_envelope"

    def test_tall_box_uses_height(self):
        spans = [(6.0, 32.0, 42.0, 64.0, 84.0)]
        span, _ = compute_framing_span(spans, face_w=64.0)
        assert span == pytest.approx(84.0)

    def test_floor_by_face_w(self):
        spans = [(6.0, 10.0, 10.0, 20.0, 20.0)]
        span, _ = compute_framing_span(spans, face_w=92.0)
        assert span == pytest.approx(92.0)

    def test_missing_evidence_returns_face_w(self):
        span, basis = compute_framing_span([], face_w=92.0)
        assert span == pytest.approx(92.0)
        assert basis == "no_elected_observations"

    def test_median_not_max(self):
        spans = [
            (1.0, 0.0, 0.0, 60.0, 60.0),   # extent 60
            (2.0, 0.0, 0.0, 80.0, 80.0),   # extent 80
            (3.0, 0.0, 0.0, 200.0, 200.0), # extent 200 â€” outlier ignored
        ]
        span, _ = compute_framing_span(spans, face_w=1.0)
        assert span == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# local_proposals: half-open interval, absolute-t clock
# ---------------------------------------------------------------------------

class TestLocalProposals:
    SRC_W, SRC_H = 1920, 1080
    MULT, HEADROOM = CAMERAS["face"]  # 3.8, 0.44
    CLIP_START = 0.0
    # Large base whose delivered footprint contains the test obs centre (300, 200)
    BASE = {"x": 50, "y": 0, "w": 500, "h": 800}

    def _spans(self, abs_times):
        # Spans with absolute t; use clip_start=0 so abs_t == clip_t
        return [(t, 300.0, 200.0, 84.0, 84.0) for t in abs_times]

    def test_includes_t0(self):
        spans = self._spans([0.0])
        props, _sc = local_proposals(spans, 0.0, 2.0, self.MULT, self.HEADROOM,
                                     84.0, self.SRC_W, self.SRC_H, self.CLIP_START,
                                     self.BASE)
        assert len(props) == 1

    def test_excludes_t1(self):
        spans = self._spans([2.0])
        props, _sc = local_proposals(spans, 0.0, 2.0, self.MULT, self.HEADROOM,
                                     84.0, self.SRC_W, self.SRC_H, self.CLIP_START,
                                     self.BASE)
        assert len(props) == 0

    def test_terminal_sample_included(self):
        # Sample just before t1 must be included (abs_t < abs_t1)
        eps = 1e-4
        spans = self._spans([2.0 - eps])
        props, _sc = local_proposals(spans, 0.0, 2.0, self.MULT, self.HEADROOM,
                                     84.0, self.SRC_W, self.SRC_H, self.CLIP_START,
                                     self.BASE)
        assert len(props) == 1

    def test_exact_clock_terminal_sample_uses_absolute_t(self):
        """Float-subtraction trap: (0.47 + 2) - 0.47 = 1.9999...998.

        A sample whose absolute t equals clip_start + 2.0 belongs to the
        NEXT shot (t0=2.0). Comparing in absolute time avoids the error.
        """
        clip_start = 0.47
        abs_t_terminal = clip_start + 2.0   # exact absolute time from track
        spans = [(abs_t_terminal, 300.0, 200.0, 84.0, 84.0)]

        # Previous shot ends at clip-relative 2.0 â†’ abs 2.47
        prev, _ = local_proposals(spans, 0.0, 2.0, self.MULT, self.HEADROOM,
                                  84.0, self.SRC_W, self.SRC_H, clip_start,
                                  self.BASE)
        assert len(prev) == 0, "terminal sample must not be stolen by previous shot"

        # Next shot starts at clip-relative 2.0 â†’ abs 2.47
        nxt, _ = local_proposals(spans, 2.0, 3.0, self.MULT, self.HEADROOM,
                                 84.0, self.SRC_W, self.SRC_H, clip_start,
                                 self.BASE)
        assert len(nxt) == 1, "terminal sample must be in its own shot"

    def test_obs_span_floored_by_framing_span(self):
        spans = [(1.0, 300.0, 200.0, 40.0, 40.0)]
        props, _sc = local_proposals(spans, 0.0, 2.0, self.MULT, self.HEADROOM,
                                     84.0, self.SRC_W, self.SRC_H, self.CLIP_START,
                                     self.BASE)
        assert len(props) == 1
        expected_h = int(84.0 * self.MULT) // 2 * 2
        assert props[0]["h"] == pytest.approx(expected_h, abs=4)

    def test_empty_spans_returns_empty(self):
        props, _sc = local_proposals([], 0.0, 2.0, self.MULT, self.HEADROOM,
                                     84.0, self.SRC_W, self.SRC_H, self.CLIP_START,
                                     self.BASE)
        assert props == []

    def test_game_mult_not_used_here(self):
        # obs at (960, 540); use a base whose footprint contains that centre
        base = {"x": 660, "y": 0, "w": 600, "h": 1080}
        spans = [(1.0, 960.0, 540.0, 92.0, 92.0)]
        props, _sc = local_proposals(spans, 0.0, 2.0, 2.5, 0.41,
                                     92.0, self.SRC_W, self.SRC_H, self.CLIP_START,
                                     base)
        expected_h = int(92 * 2.5) // 2 * 2
        assert props[0]["h"] == pytest.approx(expected_h, abs=4)


# ---------------------------------------------------------------------------
# widen_for_envelope
# ---------------------------------------------------------------------------

class TestWidenForEnvelope:
    SRC_W, SRC_H = 1920, 1080

    def _base(self, x=400, y=200, w=200, h=600):
        return {"x": x, "y": y, "w": w, "h": h}

    def _prop(self, x, y, w, h):
        return {"x": x, "y": y, "w": w, "h": h}

    def test_no_proposals_returns_base_unchanged(self):
        base = self._base()
        rect, fit, reason = widen_for_envelope(base, [], self.SRC_W, self.SRC_H)
        assert rect == base
        assert fit is False
        assert reason is None

    def test_proposal_inside_base_no_change(self):
        base = self._base(x=100, y=100, w=400, h=800)
        prop = self._prop(x=200, y=200, w=100, h=400)
        rect, fit, reason = widen_for_envelope(base, [prop], self.SRC_W, self.SRC_H)
        assert reason is None
        assert fit is False

    def test_widened_rect_contains_proposal(self):
        # Two rects at the same horizontal centre; the proposal is taller,
        # so it extends above the base in delivered space â€” widening is needed
        # and the union height is bounded (both delivered widths are w*9/16).
        from services.clipper.dynamic_face_envelope import _footprint
        base = {"x": 400, "y": 200, "w": 200, "h": 400}
        prop = {"x": 400, "y": 50, "w": 300, "h": 650}
        rect, fit, reason = widen_for_envelope(base, [prop], self.SRC_W, self.SRC_H)
        # Widening must have been attempted (reason set)
        assert reason is not None
        # Delivered footprint of result must contain delivered footprints of base and prop
        r_fp = _footprint(rect, self.SRC_W, self.SRC_H)
        b_fp = _footprint(base, self.SRC_W, self.SRC_H)
        p_fp = _footprint(prop, self.SRC_W, self.SRC_H)
        if r_fp and b_fp:
            assert r_fp[0] <= b_fp[0] and r_fp[1] <= b_fp[1]
            assert r_fp[2] >= b_fp[2] and r_fp[3] >= b_fp[3]
        if r_fp and p_fp and reason == "widened_for_local_envelope":
            assert r_fp[0] <= p_fp[0] and r_fp[1] <= p_fp[1]
            assert r_fp[2] >= p_fp[2] and r_fp[3] >= p_fp[3]

    def test_impossible_union_returns_base_with_full_fit(self):
        # Proposals at opposite extremes â€” union too wide for 9:16
        base = self._base(x=860, y=0, w=200, h=1080)
        prop_l = self._prop(x=0, y=0, w=100, h=1080)
        prop_r = self._prop(x=1820, y=0, w=100, h=1080)
        rect, fit, reason = widen_for_envelope(base, [prop_l, prop_r],
                                               self.SRC_W, self.SRC_H)
        assert fit is True
        assert reason is not None
        assert rect == base

    def test_even_dimensions(self):
        base = self._base(x=400, y=100, w=200, h=600)
        prop = self._prop(x=100, y=100, w=100, h=600)
        rect, _, reason = widen_for_envelope(base, [prop], self.SRC_W, self.SRC_H)
        if reason == "widened_for_local_envelope":
            assert rect["w"] % 2 == 0
            assert rect["h"] % 2 == 0

    def test_never_shrinks_base(self):
        base = self._base(x=400, y=100, w=300, h=900)
        prop = self._prop(x=500, y=200, w=50, h=400)
        rect, _, _ = widen_for_envelope(base, [prop], self.SRC_W, self.SRC_H)
        assert rect["x"] <= base["x"]
        assert rect["y"] <= base["y"]
        assert rect["x"] + rect["w"] >= base["x"] + base["w"]
        assert rect["y"] + rect["h"] >= base["y"] + base["h"]


# ---------------------------------------------------------------------------
# camera_rects: framing_span wires through; game windows unaffected
# ---------------------------------------------------------------------------

class TestCameraRectsFramingSpan:
    def test_square_boxes_no_change(self):
        from services.clipper.dynamic_cameras import DEFAULT_STYLE
        face = {"cx": 200.0, "cy": 150.0, "w": 92.0, "n": 5.0}
        legacy = camera_rects(face, DEFAULT_STYLE, 1920, 1080)
        with_span = camera_rects(face, DEFAULT_STYLE, 1920, 1080, framing_span=92.0)
        for cam in ("face", "face_medium", "face_tight"):
            assert legacy[cam] == with_span[cam]

    def test_tall_box_enlarges_face_cameras_only(self):
        from services.clipper.dynamic_cameras import DEFAULT_STYLE
        face = {"cx": 300.0, "cy": 200.0, "w": 64.0, "n": 5.0}
        narrow = camera_rects(face, DEFAULT_STYLE, 1920, 1080)
        wider = camera_rects(face, DEFAULT_STYLE, 1920, 1080, framing_span=84.0)
        for cam in ("face", "face_medium", "face_tight"):
            assert wider[cam]["h"] >= narrow[cam]["h"]
        for cam in ("game", "game_tight"):
            assert narrow[cam] == wider[cam]

    def test_framing_span_none_equals_no_arg(self):
        from services.clipper.dynamic_cameras import DEFAULT_STYLE
        face = {"cx": 300.0, "cy": 200.0, "w": 80.0, "n": 3.0}
        r1 = camera_rects(face, DEFAULT_STYLE, 1920, 1080)
        r2 = camera_rects(face, DEFAULT_STYLE, 1920, 1080, framing_span=None)
        assert r1 == r2


# ---------------------------------------------------------------------------
# effects cannot undo widening (integration)
# ---------------------------------------------------------------------------

class TestEffectsCannotUndoWidening:
    def _plan(self):
        from services.clipper.dynamic_edit import plan_dynamic_edit
        # YuNet-like: 64-wide, 84-tall proxy boxes; sx=sy=3 â†’ 192Ã—252 source
        face_track = [
            {"t": 0.5, "boxes": [[60, 60, 64, 84]]},
            {"t": 1.5, "boxes": [[60, 60, 64, 84]]},
        ]
        cand = {"start": 0.0, "end": 3.0, "words": []}
        signals = {
            "audio": {"rms": [0.3] * 12, "hop_s": 0.25},
            "motion": {"motion": [0.1] * 6, "hop_s": 0.5},
            "proxy_width": 480, "proxy_height": 270,
        }
        return plan_dynamic_edit(
            cand, signals, face_track,
            src_w=1440, src_h=810,
            proxy_w=480, proxy_h=270,
            no_second_camera=True,
        )

    def test_plan_runs_without_error(self):
        result = self._plan()
        assert "shots" in result

    def test_subject_records_framing_span(self):
        result = self._plan()
        subj = result["subject"]
        assert "framing_span" in subj
        assert "framing_span_basis" in subj
        assert "elected_proposals" in subj

    def test_tall_span_larger_than_face_w(self):
        result = self._plan()
        assert result["subject"]["framing_span"] >= result["subject"]["face"]["w"]

    def test_widened_shots_effects_frozen(self):
        result = self._plan()
        for shot in result["shots"]:
            if "envelope" in shot.get("framing_adjustment", ""):
                assert shot["move"] == "hold"
                assert shot["snap"] is False
                assert shot["shake"] == 0.0
