"""The `fit` composition — the whole frame, letterboxed.

It exists because of one measured result. The blind review's clearest finding
was that a 9:16 window pointed at a sequence with no subject produces a wall of
texture: on the Jensen interview an "8 million pixels" slide came out an
unreadable strip of grid, and the reviewer said it plainly — "when something
other than their faces is on screen it looks bad because it's cropped".

Every test here guards the trap that made this more than a filtergraph edit:
`_size` forces 9:16 on anything handed to it, so a full-frame rect comes back
out as a 9:16 crop and the letterbox silently never happens.
"""

from __future__ import annotations

from services.clipper import dynamic_render as dr


def _shot(**kw):
    base = {"t0": 0.0, "t1": 2.0, "rect": {"x": 100, "y": 0, "w": 1214, "h": 2160},
            "anchor": [1700.0, 1080.0], "move": "hold"}
    base.update(kw)
    return base


def test_a_fit_shot_takes_the_whole_9_16_canvas():
    """`_size` would have forced the full frame back to 1214x2160 — a 9:16 crop
    of it, which is the thing `fit` exists not to be.

    The canvas, not the source: the graph letterboxes the source onto a 9:16
    canvas up front, so the full-frame crop is the canvas. See
    `test_clipper_render_geometry`, which checks the pixels this produces.
    """
    from services.clipper.dynamic_geometry import canvas_size

    cw, ch, _ = canvas_size(3840, 2160)
    out = dr._size_timeline(_shot(composition="fit"), {}, 3840, 2160)
    assert out == [(0.0, cw, ch)]


def test_a_crop_shot_is_untouched_by_the_new_path():
    style = {"snap_s": 0.0, "snap_amount": 0.0, "push_amount": 0.0, "push_hz": 10.0}
    out = dr._size_timeline(_shot(), style, 3840, 2160)
    assert out and all(w * 16 <= h * 9 + 8 for _, w, h in out), out
    assert all(w <= 3840 and h <= 2160 for _, w, h in out)


def test_a_fit_shot_neither_snaps_nor_pushes():
    """A shot that exists because there is nothing to point at has nothing to
    move toward, and a push on a letterboxed frame reads as a glitch."""
    style = {"snap_s": 0.25, "snap_amount": 0.06, "push_amount": 0.05, "push_hz": 10.0}
    out = dr._size_timeline(_shot(composition="fit", snap=True, move="push"),
                            style, 3840, 2160)
    assert len(out) == 1, f"fit emitted {len(out)} control points"


def test_a_fit_shot_sits_at_the_origin_and_does_not_shake():
    x, y = dr._position_exprs(_shot(composition="fit", shake=4.0),
                              (3840, 2160), 3840, 2160)
    assert (x, y) == ("0", "0")


def test_an_ordinary_shot_still_tracks_its_anchor():
    x, y = dr._position_exprs(_shot(), (1214, 2160), 3840, 2160)
    assert "out_w/2" in x and "out_h/2" in y


def test_an_unknown_composition_is_treated_as_a_crop():
    """Fails safe. An artefact written by a newer planner must not silently
    letterbox every shot on an older renderer."""
    assert dr.composition_of({"composition": "cinematic"}) == "crop"
    assert dr.composition_of({}) == "crop"
    assert dr.composition_of({"composition": "fit"}) == "fit"


# ── The transition, which is the thing the isolated tests do not prove ───────


def _plan(*compositions: str) -> dict:
    """A shot list alternating compositions, one second each."""
    return {"shots": [
        {"t0": float(i), "t1": float(i + 1), "composition": c,
         "rect": ({"x": 0, "y": 0, "w": 3840, "h": 2160} if c == "fit"
                  else {"x": 1300, "y": 0, "w": 1214, "h": 2160}),
         "anchor": [1907.0, 1080.0], "move": "hold", "camera": "face"}
        for i, c in enumerate(compositions)], "style": {}, "hits": []}


def test_a_mixed_plan_schedules_both_geometries():
    """PSNR on a clip of ONLY `crop` shots proves the old path did not regress.
    It says nothing about the new one. This is the case that does: one plan, one
    filtergraph, three shots that change composition twice.
    """
    script = dr.build_sendcmd(_plan("crop", "fit", "crop"), 3840, 2160)

    widths = [int(w) for w in __import__("re").findall(r"crop w (\d+)", script)]
    assert 3840 in widths, "the fit shot never asked for the whole frame"
    assert any(w != 3840 for w in widths), "the crop shots were flattened too"


def test_the_fit_shot_is_scheduled_at_its_own_start():
    """A composition change that lands late shows the previous shot's framing
    over the new shot's content — the cut and the reframe must be the same
    instant."""
    import re

    script = dr.build_sendcmd(_plan("crop", "fit"), 3840, 2160)
    at = [float(m) for m in re.findall(r"^([\d.]+)\s", script, re.M)]
    full = [float(m.group(1)) for m in
            re.finditer(r"^([\d.]+).*crop w 3840", script, re.M)]
    assert full, "no command sets the full frame"
    assert min(full) == 1.0, f"fit scheduled at {min(full)}, shot starts at 1.0"
    assert at == sorted(at), "commands are not in time order"


# REMOVED: test_the_graph_letterboxes_rather_than_stretching.
#
# It asserted that the filtergraph TEXT contained `force_original_aspect_ratio`
# and `pad`. It did, and it passed — while every `fit` shot shipped stretched,
# because `scale` fixes its output size at configuration time and never
# recomputes it when `sendcmd` changes the crop. Four review sessions carried
# the same note on every source before anyone looked at the pixels.
#
# `test_clipper_render_geometry` replaces it by rendering a circle and measuring
# whether it is still round. Reading the graph cannot catch this class of defect
# and should not be trusted to.


def test_the_graph_starts_on_the_first_shots_own_rectangle():
    """`crop` is initialised from shot zero and only then driven by sendcmd. A
    plan that opens on `fit` must not open on a 9:16 window and jump."""
    from services.clipper.dynamic_geometry import canvas_size

    cw, ch, _ = canvas_size(3840, 2160)
    graph, _ = dr.build_dynamic_filtergraph(
        _plan("fit", "crop"), "cmd.txt", None, src_w=3840, src_h=2160)
    assert f"crop={cw}:{ch}:0:0" in graph, graph[:200]
