"""
Tests for the dynamic multi-cam edit (services/clipper/dynamic_edit.py and
dynamic_render.py).

Both modules are pure, so the invariants that actually matter are checkable
without ffmpeg, without media on disk and in milliseconds:

  * the grammar — adjacent shots never share a camera, shot lengths stay inside
    the configured band, and the edit never sits on one subject family for long,
  * the geometry — every rect is even, 9:16 and inside the frame, and the
    gameplay cameras are clear of the facecam,
  * the two rules that make the filtergraph legal at all — no commas anywhere in
    an emitted expression, and even dimensions everywhere.

The comma rule is the one worth a test rather than a comment: a stray
`clip(v,lo,hi)` would not raise, it would silently truncate the filtergraph and
render something plausible-looking and wrong.
"""

import pytest

from services.clipper import dynamic_edit, dynamic_render


SRC_W, SRC_H = 1920, 1080
PROXY_W, PROXY_H = 480, 270


def _words(start: float, end: float, step: float = 0.35):
    """Continuous speech across the window, punctuated every 8 words."""
    out, t, i = [], start, 0
    while t < end:
        word = "what" if i % 8 else "go!"
        out.append({"word": word, "start": t, "end": min(t + step * 0.8, end)})
        t += step
        i += 1
    return out


def _signals(duration: float = 40.0):
    hop = 0.25
    n = int(duration / hop) + 4
    return {
        "proxy_width": PROXY_W,
        "proxy_height": PROXY_H,
        "audio": {
            "hop_s": hop,
            # A sawtooth so the clip has a real loud/quiet spread to normalise.
            "rms": [0.2 + 0.8 * ((i % 20) / 20.0) for i in range(n)],
            "peaks": [3.0, 7.5, 12.25, 19.0, 24.5, 30.0],
        },
        "motion": {"hop_s": 0.5, "motion": [0.5] * (n // 2 + 4)},
        "scenes": [11.0, 23.0],
    }


def _faces(start: float, end: float, hop: float = 0.25):
    """A rock-steady facecam bottom-left, in PROXY pixels."""
    out, t = [], start
    while t < end:
        out.append({"t": t, "boxes": [[62, 172, 52, 52]]})
        t += hop
    return out


def _plan(start: float = 100.0, end: float = 133.0, **style):
    cand = {"start": start, "end": end, "words": _words(start, end)}
    return dynamic_edit.plan_dynamic_edit(
        cand, _signals(end - start), _faces(start, end),
        src_w=SRC_W, src_h=SRC_H, proxy_w=PROXY_W, proxy_h=PROXY_H,
        game_motion=[0.4 + 0.5 * ((i % 7) / 7.0) for i in range(200)],
        game_focus=[900.0 + 40 * (i % 5) for i in range(200)],
        game_motion_hop=0.25,
        style=style or None,
    )


# --------------------------------------------------------------------------
# Shot grammar
# --------------------------------------------------------------------------

def test_rung_steps_up_once_per_threshold_cleared():
    fam = ("wide", "medium", "tight")
    assert dynamic_edit._rung(fam, 0.10, [0.42, 0.72]) == "wide"
    assert dynamic_edit._rung(fam, 0.42, [0.42, 0.72]) == "medium"
    assert dynamic_edit._rung(fam, 0.71, [0.42, 0.72]) == "medium"
    assert dynamic_edit._rung(fam, 0.72, [0.42, 0.72]) == "tight"
    assert dynamic_edit._rung(fam, 1.00, [0.42, 0.72]) == "tight"


def test_rung_is_generic_over_family_length():
    # The game family kept two rungs and its original 0.55 boundary; the point of
    # _rung is that neither family needs the other's arity hard-coded.
    two = ("game", "game_tight")
    assert dynamic_edit._rung(two, 0.54, [0.55]) == "game"
    assert dynamic_edit._rung(two, 0.55, [0.55]) == "game_tight"
    # More thresholds than rungs must clamp, not raise.
    assert dynamic_edit._rung(two, 0.99, [0.42, 0.72]) == "game_tight"


def test_the_middle_face_rung_is_actually_reachable():
    # Adding a rung the planner never selects would be worse than not adding it.
    cams = {s["camera"] for s in _plan()["shots"]}
    assert "face_medium" in cams


def test_every_planned_camera_has_a_rect():
    plan = _plan()
    for shot in plan["shots"]:
        assert shot["camera"] in plan["cameras"]


def test_adjacent_shots_never_share_a_camera():
    # A cut onto the same rectangle is invisible: it reads as a stutter, not an
    # edit. This is the single invariant the whole style rests on.
    shots = _plan()["shots"]
    assert len(shots) > 5
    for a, b in zip(shots, shots[1:]):
        assert a["camera"] != b["camera"]


def test_adjacent_shots_never_share_a_rect_either():
    """The invariant above, checked on the geometry rather than the name.

    The name check cannot see the case that actually shipped: with
    `game_height_pct` and `game_zoom` both at 1.00 — the measured default since
    the framing A/B — `game` and `game_tight` are two names for one rectangle,
    so a g->G step passes the name check and cuts on nothing.
    """
    for style in ({}, {"game_height_pct": 1.0, "game_zoom": 1.0},
                  {"game_height_pct": 0.86, "game_zoom": 0.64}):
        shots = _plan(**style)["shots"]
        for a, b in zip(shots, shots[1:]):
            assert tuple(a["rect"][k] for k in ("x", "y", "w", "h")) != \
                   tuple(b["rect"][k] for k in ("x", "y", "w", "h")), \
                   f"a cut that changes nothing, at {b['t0']}s, style={style}"


def test_merging_a_dead_cut_keeps_the_timeline_whole():
    """Removing a cut must not remove any of the clip with it."""
    merged = dynamic_edit._merge_dead_cuts([
        {"index": 0, "t0": 0.0, "t1": 1.0, "rect": {"x": 0, "y": 0, "w": 10, "h": 20}},
        {"index": 1, "t0": 1.0, "t1": 2.5, "rect": {"x": 0, "y": 0, "w": 10, "h": 20}},
        {"index": 2, "t0": 2.5, "t1": 4.0, "rect": {"x": 8, "y": 0, "w": 10, "h": 20}},
    ])
    assert [s["index"] for s in merged] == [0, 1]
    assert merged[0]["t0"] == 0.0 and merged[0]["t1"] == 2.5
    assert merged[-1]["t1"] == 4.0


def test_shot_lengths_stay_inside_the_configured_band():
    style = {"min_shot_s": 0.6, "target_shot_s": 1.25, "max_shot_s": 2.4}
    shots = _plan(**style)["shots"]
    # The final shot absorbs the tail, so it may run past max_shot_s.
    for shot in shots[:-1]:
        assert shot["t1"] - shot["t0"] >= style["min_shot_s"] - 1e-6
        assert shot["t1"] - shot["t0"] <= style["max_shot_s"] + 1e-6


def test_the_edit_crosses_between_subjects():
    # Cutting between two framings of the same face is a zoom, not a multi-cam
    # switch; the reference edits keep crossing to the other subject.
    shots = _plan(max_same_family=2)["shots"]
    families = ["face" if s["camera"].startswith("face") else "game" for s in shots]
    run = longest = 1
    for a, b in zip(families, families[1:]):
        run = run + 1 if a == b else 1
        longest = max(longest, run)
    assert longest <= 2
    assert "game" in families and "face" in families


def test_the_clip_opens_on_a_face():
    assert _plan()["shots"][0]["camera"].startswith("face")


def test_shots_tile_the_window_without_gaps():
    plan = _plan(start=100.0, end=133.0)
    shots = plan["shots"]
    assert shots[0]["t0"] == 0.0
    assert shots[-1]["t1"] == pytest.approx(plan["duration"], abs=0.01)
    for a, b in zip(shots, shots[1:]):
        assert a["t1"] == b["t0"]


def test_a_blank_gameplay_band_is_never_cut_to_even_when_it_moves():
    # Motion alone says this band is alive; detail says there is nothing in it.
    # A shot may only cut to gameplay when BOTH agree.
    cand = {"start": 0.0, "end": 20.0, "words": _words(0.0, 20.0)}
    signals = _signals(20.0)
    moving = [0.9] * 200
    plan = dynamic_edit.plan_dynamic_edit(
        cand, signals, _faces(0.0, 20.0), src_w=SRC_W, src_h=SRC_H,
        proxy_w=PROXY_W, proxy_h=PROXY_H,
        game_motion=moving, game_focus=[900.0] * 200,
        game_detail=[1.0] * 200, game_motion_hop=0.25)
    assert all(s["camera"].startswith("face") for s in plan["shots"])


def test_detail_is_ignored_when_the_caller_does_not_measure_it():
    # The old motion-only behaviour has to survive a caller that never passes
    # game_detail, or every existing entry point silently changes.
    cand = {"start": 0.0, "end": 20.0, "words": _words(0.0, 20.0)}
    signals = _signals(20.0)
    kwargs = dict(src_w=SRC_W, src_h=SRC_H, proxy_w=PROXY_W, proxy_h=PROXY_H,
                  game_motion=[0.9] * 200, game_focus=[900.0] * 200,
                  game_motion_hop=0.25)
    without = dynamic_edit.plan_dynamic_edit(cand, signals, _faces(0.0, 20.0), **kwargs)
    rich = dynamic_edit.plan_dynamic_edit(cand, signals, _faces(0.0, 20.0),
                                          game_detail=[60.0] * 200, **kwargs)
    assert [s["camera"] for s in without["shots"]] == [s["camera"] for s in rich["shots"]]
    assert any(s["camera"].startswith("game") for s in without["shots"])


def test_a_dead_gameplay_region_is_never_cut_to():
    # Zero motion in the band means a loading screen or an empty sky; holding
    # on the face one shot too long beats cutting to nothing.
    cand = {"start": 0.0, "end": 30.0, "words": _words(0.0, 30.0)}
    plan = dynamic_edit.plan_dynamic_edit(
        cand, _signals(30.0), _faces(0.0, 30.0),
        src_w=SRC_W, src_h=SRC_H, proxy_w=PROXY_W, proxy_h=PROXY_H,
        game_motion=[0.0] * 200, game_focus=[-1.0] * 200, game_motion_hop=0.25)
    assert all(s["camera"].startswith("face") for s in plan["shots"])


def test_a_window_too_short_to_cut_still_produces_one_shot():
    cand = {"start": 5.0, "end": 6.2, "words": _words(5.0, 6.2)}
    plan = dynamic_edit.plan_dynamic_edit(
        cand, _signals(10.0), _faces(5.0, 6.2),
        src_w=SRC_W, src_h=SRC_H, proxy_w=PROXY_W, proxy_h=PROXY_H)
    assert len(plan["shots"]) == 1
    assert plan["shots"][0]["t1"] == pytest.approx(1.2, abs=0.01)


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def test_every_rect_is_even_nine_by_sixteen_and_in_frame():
    for shot in _plan()["shots"]:
        r = shot["rect"]
        assert r["w"] % 2 == 0 and r["h"] % 2 == 0
        assert r["x"] >= 0 and r["y"] >= 0
        assert r["x"] + r["w"] <= SRC_W and r["y"] + r["h"] <= SRC_H
        assert r["w"] / r["h"] == pytest.approx(9 / 16, abs=0.01)


def test_the_facecam_is_found_where_the_boxes_are():
    face = _plan()["subject"]["face"]
    # proxy (62..114, 172..224) centre (88, 198) -> source (352, 792)
    assert face["cx"] == pytest.approx(352, abs=8)
    assert face["cy"] == pytest.approx(792, abs=8)


def test_gameplay_cameras_are_clear_of_the_facecam():
    plan = _plan()
    face_right = plan["cameras"]["face"]["x"] + plan["cameras"]["face"]["w"]
    for name in ("game", "game_tight"):
        assert plan["cameras"][name]["x"] >= face_right - 2


def test_scattered_face_detections_do_not_drag_the_camera():
    # A Haar cascade fires on posters and avatars all over a stream frame. The
    # real facecam is the cluster; outliers must not move it.
    faces = _faces(0.0, 20.0)
    for i in range(0, len(faces), 6):
        faces[i] = {"t": faces[i]["t"], "boxes": [[400, 20, 40, 40]]}
    cand = {"start": 0.0, "end": 20.0, "words": _words(0.0, 20.0)}
    plan = dynamic_edit.plan_dynamic_edit(
        cand, _signals(20.0), faces, src_w=SRC_W, src_h=SRC_H,
        proxy_w=PROXY_W, proxy_h=PROXY_H)
    assert plan["subject"]["face"]["cy"] == pytest.approx(792, abs=40)


# --------------------------------------------------------------------------
# The sendcmd script
# --------------------------------------------------------------------------

def _script(**style):
    return dynamic_render.build_sendcmd(_plan(**style), SRC_W, SRC_H)


def test_no_emitted_expression_contains_a_comma():
    # A comma separates arguments in a sendcmd entry and filters in a
    # filtergraph. `clip(v,lo,hi)` would not raise — it would truncate the
    # graph and render something plausible and wrong.
    for line in _script().splitlines():
        for chunk in line.split("'")[1::2]:      # only the quoted expressions
            assert "," not in chunk, line


def test_size_commands_precede_position_commands():
    # crop re-clamps x/y against the CURRENT size every time it reconfigures.
    first = _script().splitlines()[0]
    assert first.index("crop w") < first.index("crop x")
    assert first.index("crop h") < first.index("crop y")


def test_every_commanded_dimension_is_even():
    for line in _script().splitlines():
        for key in ("crop w ", "crop h "):
            if key in line:
                value = line.split(key)[1].split(",")[0].split(";")[0].strip()
                assert int(value) % 2 == 0, line


def test_the_crop_is_static_inside_a_shot_by_default():
    # The measured references hold the crop perfectly still between cuts. A
    # default that quietly drifts would be a style choice smuggled in as code.
    plan = _plan()
    for shot in plan["shots"]:
        steps = dynamic_render._size_timeline(shot, plan["style"], SRC_W, SRC_H)
        assert len(steps) == 1, shot["camera"]


def test_a_push_emits_a_ramp_of_sizes_and_a_hold_does_not():
    plan = _plan(push_amount=0.07, snap_amount=0.055)
    style = plan["style"]
    pushes = [s for s in plan["shots"] if s["move"] == "push"]
    assert pushes, "the fixture should produce at least one push"
    steps = dynamic_render._size_timeline(pushes[0], style, SRC_W, SRC_H)
    assert len(steps) > 3
    assert steps[-1][2] < steps[0][2]

    held = dict(pushes[0], move="hold", snap=False)
    assert len(dynamic_render._size_timeline(held, style, SRC_W, SRC_H)) == 1


def test_position_expressions_re_centre_on_the_crop_size():
    # Written against out_w/out_h so a size command alone zooms toward the
    # anchor instead of toward the middle of the frame.
    shot = {"anchor": [900, 500], "shake": 0.0}
    x, y = dynamic_render._position_exprs(shot, (400, 711), SRC_W, SRC_H)
    assert x.endswith("-out_w/2") and y.endswith("-out_h/2")


def test_shake_uses_two_frequencies():
    # One sine reads as a pendulum, which looks mechanical rather than hand-held.
    shot = {"anchor": [900, 500], "shake": 6.0}
    x, y = dynamic_render._position_exprs(shot, (400, 711), SRC_W, SRC_H)
    assert "sin(" in x and "sin(" in y
    assert x.split("sin(")[1] != y.split("sin(")[1]


# --------------------------------------------------------------------------
# The filtergraph and the argv
# --------------------------------------------------------------------------

def _cmd(**kw):
    plan = _plan()
    params = dict(start=100.0, duration=plan["duration"],
                  src_w=SRC_W, src_h=SRC_H, fps=30, crf=18, preset="medium")
    params.update(kw)
    return dynamic_render.build_dynamic_cmd(
        "D:/media/vod.mp4", plan, "D:/work/clip.cmd.txt", None,
        "D:/out/clip.mp4", **params)


def test_one_encode_with_the_captions_inside_it():
    plan = _plan()
    graph, label = dynamic_render.build_dynamic_filtergraph(
        plan, "D:/work/c.txt", "D:/work/c.ass", src_w=SRC_W, src_h=SRC_H)
    assert graph.count("[0:v]") == 1
    assert "subtitles=" in graph
    assert label == "[vout]" and graph.endswith("[vout]")


def test_seek_comes_before_the_input():
    cmd = _cmd()
    assert cmd.index("-ss") < cmd.index("-i")


def test_audio_is_compressed_before_it_is_normalised():
    # LRA of 2.5-3.1 LU in the references is a compressor, not a normaliser.
    chain = _cmd()[_cmd().index("-af") + 1]
    assert chain.index("acompressor") < chain.index("loudnorm")


def test_the_delivered_rate_is_restored_after_loudnorm():
    """loudnorm resamples internally to 192 kHz and does not put it back, so
    the encoder negotiated the highest rate AAC accepts and 192 kbit/s paid for
    a band nobody can hear. Every clip shipped at 96 kHz until this was
    measured on a delivered file."""
    chain = _cmd()[_cmd().index("-af") + 1]
    assert f"aresample={dynamic_render.AUDIO_RATE}" in chain
    assert chain.index("loudnorm") < chain.index("aresample")


def test_the_limiter_is_the_ceiling_and_does_not_re_level():
    """loudnorm's TP is a target it aims at, not a ceiling it guarantees — it
    missed by 1.7 dB on an 18s clip and delivered +0.2 dBFS. And `alimiter`
    auto-levels its output back UP by default, which measured +0.07 dBFS and
    -11.8 LUFS: the default limiter undoes the normaliser in front of it."""
    chain = _cmd()[_cmd().index("-af") + 1]
    assert "alimiter" in chain
    assert chain.index("loudnorm") < chain.index("alimiter")
    assert "level=false" in chain, "alimiter auto-levels back to the ceiling"
    assert f"limit={dynamic_render.LIMITER_CEILING}" in chain


def test_hits_become_one_flash_term_each():
    plan = _plan()
    plan["hits"] = [1.0, 2.5, 4.0]
    flt = dynamic_render._eq_filter(plan)
    # One gaussian per hit, in each of brightness, saturation and contrast.
    assert flt.count("exp(") == 9
    assert "eval=frame" in flt and "," not in flt.split("brightness='")[1].split("'")[0]


def test_a_clip_with_no_hits_still_gets_the_base_grade():
    plan = _plan()
    plan["hits"] = []
    flt = dynamic_render._eq_filter(plan)
    assert "exp(" not in flt and "saturation=" in flt


# --------------------------------------------------------------------------
# Action band
# --------------------------------------------------------------------------
#
# Observed on a co-stream: two facecams, top-left and top-right. Taking the
# median of every detection lands between them, on the gameplay, so the band has
# to exclude each one separately. And with a stronger detector a Minecraft
# texture in mid-frame clustered as a third "face", shrinking the band to the
# bottom half — which is why persistence, not tightness, decides what is a
# facecam.

from services.clipper.dynamic_cameras import ACTION_BAND, action_band, face_clusters


def _track(boxes_per_sample):
    return [{"t": i * 0.25, "boxes": b} for i, b in enumerate(boxes_per_sample)]


def _steady(box, n=40, every=1):
    """A facecam: present across the whole window."""
    return [[box] if i % every == 0 else [] for i in range(n)]


def test_no_faces_falls_back_to_the_constant():
    assert action_band(_track([[]] * 40), 480, 270) == ACTION_BAND


def test_one_facecam_is_cut_out_of_the_band():
    band = action_band(_track(_steady([10, 10, 60, 60])), 480, 270)
    assert band != ACTION_BAND
    # The facecam spans x 0.02-0.15, y 0.04-0.26; the band must clear it.
    assert band[0] >= 0.14 or band[2] >= 0.25


def test_two_facecams_are_both_cut_out():
    left, right = [10, 10, 60, 60], [400, 10, 60, 60]
    track = [{"t": i * 0.25, "boxes": [left, right]} for i in range(40)]
    clusters = face_clusters(track, 480, 270)
    assert len(clusters) == 2, "the two facecams merged into one"
    band = action_band(track, 480, 270)
    for cx0, cx1, cy0, cy1 in clusters:
        assert not (cx0 < band[1] and cx1 > band[0]
                    and cy0 < band[3] and cy1 > band[2]), "a facecam is inside the band"


def test_a_face_that_flashes_past_is_not_a_facecam():
    # A mob texture the cascade likes for a moment. Enough hits to pass a count
    # threshold, nowhere near enough of the window to be an inset.
    track = _track([[[240, 120, 40, 40]] if 5 <= i <= 8 else [] for i in range(40)])
    assert face_clusters(track, 480, 270) == []


def test_a_facecam_seen_intermittently_still_counts():
    # Detection is unreliable on a turned head; what matters is that it keeps
    # coming back across the window, not that every sample finds it.
    track = _track(_steady([10, 10, 60, 60], n=40, every=4))
    assert len(face_clusters(track, 480, 270)) == 1


def test_an_open_menu_is_never_cut_to():
    # An inventory panel moves and is full of contrast, so motion and detail
    # both call it alive. Only the UI share rejects it.
    cand = {"start": 0.0, "end": 20.0, "words": _words(0.0, 20.0)}
    plan = dynamic_edit.plan_dynamic_edit(
        cand, _signals(20.0), _faces(0.0, 20.0), src_w=SRC_W, src_h=SRC_H,
        proxy_w=PROXY_W, proxy_h=PROXY_H,
        game_motion=[0.9] * 200, game_focus=[900.0] * 200,
        game_detail=[60.0] * 200, game_ui=[0.20] * 200, game_motion_hop=0.25)
    assert all(s["camera"].startswith("face") for s in plan["shots"])


def test_gameplay_below_the_ui_share_is_still_cut_to():
    cand = {"start": 0.0, "end": 20.0, "words": _words(0.0, 20.0)}
    plan = dynamic_edit.plan_dynamic_edit(
        cand, _signals(20.0), _faces(0.0, 20.0), src_w=SRC_W, src_h=SRC_H,
        proxy_w=PROXY_W, proxy_h=PROXY_H,
        game_motion=[0.9] * 200, game_focus=[900.0] * 200,
        game_detail=[60.0] * 200, game_ui=[0.01] * 200, game_motion_hop=0.25)
    assert any(s["camera"].startswith("game") for s in plan["shots"])


def test_the_ui_guard_is_off_when_the_caller_does_not_measure_it():
    cand = {"start": 0.0, "end": 20.0, "words": _words(0.0, 20.0)}
    kwargs = dict(src_w=SRC_W, src_h=SRC_H, proxy_w=PROXY_W, proxy_h=PROXY_H,
                  game_motion=[0.9] * 200, game_focus=[900.0] * 200,
                  game_detail=[60.0] * 200, game_motion_hop=0.25)
    plan = dynamic_edit.plan_dynamic_edit(cand, _signals(20.0), _faces(0.0, 20.0),
                                          **kwargs)
    assert any(s["camera"].startswith("game") for s in plan["shots"])
