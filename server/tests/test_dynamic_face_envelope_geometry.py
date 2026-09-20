"""Delivered geometry and spatial-scope counterexamples; no video encode here."""
from services.clipper.dynamic_face_envelope import local_proposals, widen_for_envelope
from services.clipper.dynamic_cameras import CAMERAS


# ---------------------------------------------------------------------------
# geometry: moving target near both ends of a shot (delivered bounds only â€”
# this is NOT a real render test; the ffmpeg encode/decode test is in
# test_clipper_framing_contract.py::test_moving_target_survives_in_decoded_frames_at_both_ends)
# ---------------------------------------------------------------------------

class TestGeometryDeliveredBothEnds:
    """Verify proposals at t0 and t1-eps both land inside the widened rect's
    DELIVERED footprint (not just planned bounds). Geometry-only; no ffmpeg.
    """
    from services.clipper import evidence_map
    from services.clipper.dynamic_geometry import canvas_size

    def _delivered(self, rect, src_w, src_h):
        from services.clipper import evidence_map
        from services.clipper.dynamic_geometry import canvas_size
        shot = {"t0": 0.0, "t1": 1.0, "rect": rect, "move": "hold", "shake": 0.0,
                "anchor": [rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2],
                "composition": "crop"}
        result = evidence_map.crop_window(shot, src_w=src_w, src_h=src_h, style={})
        assert not isinstance(result, str)
        x, y, w, h = result
        off_y = canvas_size(src_w, src_h)[2]
        return float(x), float(y) - off_y, float(x + w), float(y + h) - off_y

    def test_both_ends_contained_in_delivered_bounds(self):
        """Observations at t0 and just before t1 must both be contained."""
        src_w, src_h = 1920, 1080
        mult, headroom = CAMERAS["face"]
        fspan = 84.0
        clip_start = 0.0
        t0, t1 = 1.0, 3.0
        eps = 1e-4

        # Two nearby observations so the union fits in a 9:16 portrait
        obs_start = (t0,       500.0, 300.0, 64.0, 84.0)
        obs_end   = (t1 - eps, 620.0, 320.0, 64.0, 84.0)
        obs_after = (t1,      1500.0, 600.0, 64.0, 84.0)  # excluded

        spans = [obs_start, obs_end, obs_after]
        # base footprint (anchorâ‰ˆ(500,400), h=600, w_cropâ‰ˆ337): xâˆˆ[331,669], yâˆˆ[100,700]
        # Contains (500, 300) âœ“ and (620, 320) âœ“; excludes (1500, 600) (obs_after) âœ“
        base = {"x": 400, "y": 100, "w": 200, "h": 600}
        props, _sc = local_proposals(spans, t0, t1, mult, headroom,
                                     fspan, src_w, src_h, clip_start, base)
        assert len(props) == 2

        rect, fit, reason = widen_for_envelope(base, props, src_w, src_h)
        assert reason == "widened_for_local_envelope"

        rect_d = self._delivered(rect, src_w, src_h)
        for p in props:
            p_d = self._delivered(p, src_w, src_h)
            assert rect_d[0] <= p_d[0], f"left: {rect_d[0]} > {p_d[0]}"
            assert rect_d[1] <= p_d[1], f"top: {rect_d[1]} > {p_d[1]}"
            assert rect_d[2] >= p_d[2], f"right: {rect_d[2]} < {p_d[2]}"
            assert rect_d[3] >= p_d[3], f"bottom: {rect_d[3]} < {p_d[3]}"


# ---------------------------------------------------------------------------
# centre-inside-base filter: counterexamples for the Minecraft wall case
# ---------------------------------------------------------------------------

class TestCentreInsideBaseFilter:
    """Observations whose original centre lies outside the base delivered footprint
    must be excluded â€” they are false candidates on textures that happen to fall
    in the dominant cluster but outside the camera already chosen.
    """
    SRC_W, SRC_H = 1920, 1080

    def test_wall_outside_face_base_excluded(self):
        # Proxy face [397,31,27,27] â†’ source cx=1642,cy=178; proxy wall [299,41,80,80]
        # â†’ source cx=1356,cy=324. Base centred on the real face. Delivered footprint
        # (anchorâ‰ˆ(1640,347), h=410, w_cropâ‰ˆ230): xâˆˆ[1525,1755] â€” does NOT include cx=1356.
        base = {"x": 1525, "y": 142, "w": 230, "h": 410}
        spans = [
            (1.0, 1642.0, 178.0, 108.0, 108.0),  # real face â€” inside footprint
            (1.0, 1356.0, 324.0, 320.0, 320.0),  # wall â€” outside footprint
        ]
        mult, headroom = CAMERAS["face"]
        props, scope = local_proposals(spans, 0.0, 2.0, mult, headroom, 108.0,
                                       self.SRC_W, self.SRC_H, 0.0, base)
        assert scope["seen"] == 2
        assert scope["included"] == 1
        assert scope["excluded"] == 1
        assert len(props) == 1

    def test_face_inside_base_included(self):
        base = {"x": 1525, "y": 142, "w": 230, "h": 410}
        spans = [(1.0, 1642.0, 178.0, 108.0, 108.0)]
        mult, headroom = CAMERAS["face"]
        props, scope = local_proposals(spans, 0.0, 2.0, mult, headroom, 108.0,
                                       self.SRC_W, self.SRC_H, 0.0, base)
        assert scope["included"] == 1
        assert scope["excluded"] == 0
        assert len(props) == 1

    def test_unavailable_base_footprint_is_strict_refusal(self, monkeypatch):
        # When _footprint(base) returns None, local_proposals must refuse to
        # propose anything â€” an unreadable camera is not permission to include
        # all observations; it is an unknown, and unknowns are not authorised.
        from services.clipper import dynamic_face_envelope as mod
        base = {"x": 1525, "y": 142, "w": 230, "h": 410}
        spans = [(1.0, 1642.0, 178.0, 108.0, 108.0)]
        mult, headroom = CAMERAS["face"]
        monkeypatch.setattr(mod, "_footprint", lambda r, w, h: None)
        props, scope = local_proposals(spans, 0.0, 2.0, mult, headroom, 108.0,
                                       self.SRC_W, self.SRC_H, 0.0, base)
        assert props == []
        assert scope["included"] == 0
        assert scope.get("unavailable", scope.get("excluded", 0)) >= 1
