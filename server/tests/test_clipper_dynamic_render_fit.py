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


def test_a_fit_shot_keeps_the_whole_frame():
    """`_size` would have forced 3840x2160 back to 1214x2160 — a 9:16 crop of
    the frame, which is the very thing `fit` exists not to be."""
    out = dr._size_timeline(_shot(composition="fit"), dr.DEFAULT_STYLE
                            if hasattr(dr, "DEFAULT_STYLE") else {},
                            3840, 2160)
    assert out == [(0.0, 3840, 2160)]


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
