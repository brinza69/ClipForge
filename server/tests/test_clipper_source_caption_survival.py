"""What the viewer receives of the SOURCE's own subtitle after the crop.

THE MEASUREMENT THAT SETTLED THE BATCH. D-sublot 2 was scoped as "keep the
source subtitle in frame", and on `pilotf81b` — the source the whole caption
policy came from — there is no framing that does it: the band is 1216 pixels
wide and every 9:16 window on a 2560x1440 source is 810. The tests below pin
that arithmetic, because the tempting version of this module reports a smaller
cut and calls it an improvement.
"""

from __future__ import annotations

from services.clipper import source_caption_survival as scs

SRC_W, SRC_H = 2560, 1440
#: go ghost's real band, from `source_captions.detect` on its proxy.
GO_GHOST = {"band": 9, "frames": 9, "widest": 0.45,
            "extent": {"x0": 0.25, "x1": 0.725, "y0": 0.9148, "y1": 0.9667}}


def _shot(index: int, x: int, w: int = 810, **kw) -> dict:
    got = {"index": index, "t0": float(index), "t1": index + 1.0,
           "rect": {"x": x, "y": 0, "w": w, "h": 1440},
           "anchor": [x + w / 2.0, 720], "composition": "crop",
           "move": "hold", "snap": False, "shake": 0.0}
    got.update(kw)
    return got


# --- the finding ------------------------------------------------------------


def test_go_ghosts_subtitle_does_not_fit_in_any_window_of_its_own_source():
    """1216 px of band into an 810 px window. `fits_at_all` is the difference
    between "move the crop" and "there is nowhere to move it to", and it is
    what makes this a policy question rather than a planner bug."""
    got = scs.survival(GO_GHOST, [_shot(0, 858)], {}, SRC_W, SRC_H)
    assert got["state"] == scs.CUT
    assert got["why"] == scs.IMPOSSIBLE
    assert got["band_w_px"] == 1216.0 and got["window_w_px"] == 810.0
    assert got["fits_at_all"] is False


def test_the_worst_shot_is_the_one_reported_not_the_average():
    """The face camera keeps two thirds of the band and the game camera keeps a
    tenth. An average over the clip would report a legible subtitle."""
    got = scs.survival(GO_GHOST, [_shot(0, 858), _shot(1, 1718)], {},
                       SRC_W, SRC_H)
    assert got["worst_shot"] == 1
    assert got["worst_visible"] < 0.2, got["worst_visible"]


def test_a_band_the_window_contains_is_kept():
    narrow = {"band": 9, "extent": {"x0": 0.4, "x1": 0.5,
                                    "y0": 0.9148, "y1": 0.9667}}
    got = scs.survival(narrow, [_shot(0, 1024)], {}, SRC_W, SRC_H)
    assert got["state"] == scs.KEPT
    assert got["worst_visible"] == 1.0 and got["fits_at_all"] is True


def test_a_fit_shot_keeps_the_whole_frame():
    """`fit` letterboxes the entire source, so nothing of it is cropped away —
    which is the one framing that could keep go ghost's subtitle, and the
    reason the finding is a choice rather than a dead end."""
    got = scs.survival(GO_GHOST, [_shot(0, 0, composition="fit")], {},
                       SRC_W, SRC_H)
    assert got["state"] == scs.KEPT and got["worst_visible"] == 1.0


# --- what it refuses to answer ----------------------------------------------


def test_a_band_no_box_landed_in_is_not_a_band_that_survives():
    """`extent: None` is "nothing was ever seen here". Read as a rectangle it
    is zero-sized, and a zero-sized rectangle is inside every crop — so the
    absence would come back as a clean keep on a source nobody measured."""
    got = scs.survival({"band": 9, "frames": 0, "extent": None},
                       [_shot(0, 858)], {}, SRC_W, SRC_H)
    assert got["state"] == scs.UNAVAILABLE and got["why"] == scs.NO_BAND


def test_a_malformed_extent_is_refused_rather_than_repaired():
    for bad in ({"x0": 0.5, "x1": 0.25, "y0": 0.1, "y1": 0.2},   # inverted
                {"x0": -0.1, "x1": 0.5, "y0": 0.1, "y1": 0.2},   # outside
                {"x0": "a", "x1": 0.5, "y0": 0.1, "y1": 0.2},
                {"x0": 0.1, "x1": 0.5, "y0": 0.1},               # short
                "0.1,0.5"):
        got = scs.survival({"extent": bad}, [_shot(0, 858)], {}, SRC_W, SRC_H)
        assert got["state"] == scs.UNAVAILABLE, repr(bad)
        assert got["why"] == scs.NO_BAND, repr(bad)


def test_no_shots_is_not_a_keep():
    for shots in (None, [], "abc", 7, [1, 2, 3]):
        got = scs.survival(GO_GHOST, shots, {}, SRC_W, SRC_H)
        assert got["state"] == scs.UNAVAILABLE, repr(shots)
        assert got["why"] == scs.NO_SHOTS, repr(shots)


def test_no_source_geometry_is_not_a_keep():
    for w, h in ((0, 1440), (2560, 0), (None, 1440), ("2560", None)):
        got = scs.survival(GO_GHOST, [_shot(0, 858)], {}, w, h)
        assert got["state"] == scs.UNAVAILABLE, (w, h)
        assert got["why"] == scs.NO_GEOMETRY, (w, h)


def test_a_shot_that_cannot_be_mapped_stops_a_keep_but_not_a_cut():
    """A refusal is neither a keep nor a cut. It may not silently drop out of a
    `kept`, and it may not silence a shortfall that WAS measured — the same
    order `publish_captions` owes."""
    narrow = {"band": 9, "extent": {"x0": 0.4, "x1": 0.5,
                                    "y0": 0.9148, "y1": 0.9667}}
    unreadable = _shot(1, 1024)
    unreadable.pop("t0")
    kept = scs.survival(narrow, [_shot(0, 1024), unreadable], {}, SRC_W, SRC_H)
    assert kept["state"] == scs.UNAVAILABLE and kept["refused"] == 1
    assert kept["measured"] == 1 and kept["worst_visible"] == 1.0

    cut = scs.survival(GO_GHOST, [_shot(0, 1718), unreadable], {},
                       SRC_W, SRC_H)
    assert cut["state"] == scs.CUT, "a measured cut is still a cut"
    assert cut["refused"] == 1


def test_every_shot_unreadable_is_unavailable_and_says_which_refusal():
    bad = _shot(0, 858)
    bad.pop("t1")
    got = scs.survival(GO_GHOST, [bad], {}, SRC_W, SRC_H)
    assert got["state"] == scs.UNAVAILABLE
    assert got["measured"] == 0 and got["refused"] == 1
    assert got["why"] and got["why"] in ",".join(got["refusals"])


def test_the_delivered_window_is_used_and_not_the_planners_rectangle():
    """837 of 1,965 stored `crop` shots have a delivered window that differs
    from `shot["rect"]`, because the window is centred on the CLAMPED ANCHOR.
    A shot whose anchor is nowhere near its rectangle proves which one is
    being read: the rect sits over the band, the anchor does not."""
    over_band = _shot(0, 858)
    over_band["anchor"] = [2400, 720]          # far right, away from the band
    got = scs.survival(GO_GHOST, [over_band], {}, SRC_W, SRC_H)
    assert got["worst_visible"] < 0.5, got["worst_visible"]


# --- the band is only asked for on a source that HAS captions ----------------


def _sidecar(shots) -> dict:
    return {"dynamic_plan": {"shots": shots, "style": {},
                             "src_w": SRC_W, "src_h": SRC_H}}


def test_an_absent_source_is_not_measured_against_its_best_band():
    """`classify` returns the best band it saw whatever the verdict, so an
    `absent` source still carries one. Measuring the crop against it would
    report "the crop cuts the source's subtitle" about a source with none."""
    from services.clipper import source_captions as scap

    for state in (scap.ABSENT, scap.UNKNOWN, None, "present "):
        got = scs.survival_for(_sidecar([_shot(0, 1718)]),
                               {"state": state, "band": GO_GHOST})
        assert got["state"] == scs.UNAVAILABLE, repr(state)
        assert got["why"] == scs.NOT_PRESENT, repr(state)


def test_a_present_source_is_measured_against_the_stored_plan():
    from services.clipper import source_captions as scap

    got = scs.survival_for(_sidecar([_shot(0, 1718)]),
                           {"state": scap.PRESENT, "band": GO_GHOST})
    assert got["state"] == scs.CUT and got["measured"] == 1


def test_a_plan_that_is_not_a_record_is_unavailable_not_kept():
    from services.clipper import source_captions as scap

    for sidecar in (None, {}, {"dynamic_plan": None}, {"dynamic_plan": []}, 7):
        got = scs.survival_for(sidecar, {"state": scap.PRESENT,
                                         "band": GO_GHOST})
        assert got["state"] == scs.UNAVAILABLE, repr(sidecar)


# --- and what the publish check does with it ---------------------------------


def _readable():
    from services.clipper import caption_contrast as cc

    return cc.verdict({"text_color": "#FFFFFF", "outline_color": "#000000",
                       "outline_width": 5, "highlight_color": "#FFFFFF"})


def _placed() -> dict:
    return {"placement_refused": None, "worst_share_complete": True,
            "on_face": False}


def test_a_cut_source_subtitle_is_a_measured_failure_not_an_open_question():
    """The whole point of the measurement: before it, every suppressed export
    came back `unavailable` — including the 15 that ship a subtitle sliced down
    the middle."""
    from services.clipper import publish_captions as pcap
    from services.clipper import publish_preflight as pf
    from services.clipper import source_captions as scap

    cut = scs.survival(GO_GHOST, [_shot(0, 1718)], {}, SRC_W, SRC_H)
    got = pcap.captions(_placed(), _readable(), {"state": scap.PRESENT},
                        own_layer=False, source_survival=cut)
    assert got["state"] == pf.FAIL
    assert got["why"] == pcap.SOURCE_CAPTION_CUT
    assert got["severity"] == pf.REVISABLE, "not a duplicate; a human can act"
    assert got["evidence"]["source_caption_survival"]["fits_at_all"] is False


def test_a_kept_source_subtitle_is_the_only_way_that_branch_passes():
    from services.clipper import publish_captions as pcap
    from services.clipper import publish_preflight as pf
    from services.clipper import source_captions as scap

    narrow = {"band": 9, "extent": {"x0": 0.4, "x1": 0.5,
                                    "y0": 0.9148, "y1": 0.9667}}
    kept = scs.survival(narrow, [_shot(0, 1024)], {}, SRC_W, SRC_H)
    assert kept["state"] == scs.KEPT
    got = pcap.captions(_placed(), _readable(), {"state": scap.PRESENT},
                        own_layer=False, source_survival=kept)
    assert got["state"] == pf.PASS


def test_no_survival_measurement_keeps_the_branch_unavailable():
    """The state before this module existed, and it must stay reachable: a
    caller that does not measure gets the open question, never a pass."""
    from services.clipper import publish_captions as pcap
    from services.clipper import publish_preflight as pf
    from services.clipper import source_captions as scap

    for supplied in (None, {}, {"state": "maybe"}, "kept", 7):
        got = pcap.captions(_placed(), _readable(), {"state": scap.PRESENT},
                            own_layer=False, source_survival=supplied)
        assert got["state"] == pf.UNAVAILABLE, repr(supplied)
        assert "unmeasured" in got["why"], repr(supplied)
