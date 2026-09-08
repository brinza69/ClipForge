"""The agent's visually confirmed geometry for the WATCH phases.

    imported by scripts/build_phase_regions.py and verify_phase_regions.py

WHY THIS EXISTS. `dynamic_plan["subject"]["face"]` is a clip-wide average of the
speaker's face and has no authority over the frames where the subject is a watch.
No detector produces the watch, so §A's answer is an agent annotation, and
`build_phase_regions` refuses a phase without one rather than falling back.

HOW THESE WERE OBTAINED. `scripts/dump_source_transition.py --frames ... --grid
--scale 3.0 --cols 1 --rows 1` renders ONE frame per sheet at 1440x810 with a
labelled grid every 0.1 of width and height. Every box below was read off those
sheets against that grid.

ADDRESSED BY FRAME INDEX, NOT BY TIME. Seeking by time lands on whatever the
decoder gives: asking for 224.3 s returned f2243 where the construction sample
is f2244.

---------------------------------------------------------------------------
THE READING DEFECT THAT DESTROYED THE FIRST VERSION OF THIS FILE — the reason
one frame per sheet is not a preference.

The first version was read off sheets with TWO frames side by side (`--scale 2.0
--cols 2 --rows 1`). Each tile carries its own ruler, 0 to 1 across ITS OWN
width. Reading the LEFT tile that way is natural; reading the RIGHT tile is not,
and every right-tile frame came out displaced to the right by roughly 0.26-0.30
of the frame. Re-read at 3x, one frame per sheet:

    frame    part    recorded at 2x        actually (3x)     error
    f2253    face    0.60 - 0.78           0.42 - 0.60       +0.18
    f2275    face    0.68 - 0.85           0.375 - 0.545     +0.30
    f2296    face    0.66 - 0.80           0.36 - 0.545      +0.30
    f2338    watch   0.78 - 0.88           0.595 - 0.655     +0.19
    f2365    watch   0.51 - 0.72           0.235 - 0.435     +0.28
    f2384    watch   0.66 - 0.79           0.375 - 0.510     +0.28
    f2401    screen  0.735 - 0.86          0.475 - 0.665     +0.26

and every frame, left tile included, had its TOP read too low, because the top
of a black durag against a dark building is the hardest edge in the picture at
2x and the easiest at 3x:

    f2244    face    y0 0.27               y0 0.185          -0.085 (122 px)
    f2268    face    y0 0.28               y0 0.155          -0.125 (180 px)
    f2338    face    y0 0.22               y0 0.100          -0.120 (173 px)

WHAT THAT COST. The three watch regions of `b23c14c41495` were built from the
2x numbers, so all three were wrong, and the first hold-out run reported "the
watch-worn region clips the face on 9 of 9 unseen frames, by up to 115 source
px" as if it were a fact about the region. It was a fact about the two reading
passes. The rule the corpus already knew, in a new place: do not compare a
measurement against one taken with a different instrument. Both sets here are
now 3x, one frame per sheet, and a second instrument must re-measure BOTH.

THE MARGINS ARE APPROXIMATE AND SAY SO. Read off a 480x270 proxy magnified 3x;
nothing here is resolved better than about +/-0.01 of the frame. Where the thing
leaves the frame the box stops at the boundary — the part outside is in no
picture, so a region cannot be sized to hold it and guessing its extent would be
inventing geometry.

AND `not_visible` IS NOT `absent`. On three construction frames the watch is out
of frame or indistinguishable. Those contribute nothing to a union, which is
right, and they are listed so that "the agent did not annotate this" and "there
was nothing to annotate" stay apart.
"""

from __future__ import annotations

#: `{frame index: {what: (x0, x1, y0, y1)}}` in fractions of the frame.
#: `watch` is the device including its band; `screen` is the display alone,
#: which is what the last phase must keep complete; `hand` is the hand or
#: forearm presenting it; `face` is the speaker where he is visible.
WATCH: dict[int, dict[str, tuple[float, float, float, float]]] = {
    # --- worn and indicated ------------------------------------------------
    2244: {"face": (0.435, 0.600, 0.185, 0.620),
           "hand": (0.420, 0.720, 0.600, 1.000)},
    2253: {"face": (0.420, 0.600, 0.155, 0.600),
           "hand": (0.430, 0.640, 0.590, 1.000),
           "watch": (0.550, 0.630, 0.900, 1.000)},
    2268: {"face": (0.395, 0.575, 0.155, 0.620),
           "hand": (0.340, 0.500, 0.630, 1.000)},
    2275: {"face": (0.375, 0.545, 0.135, 0.580),
           "hand": (0.300, 0.620, 0.480, 1.000),
           "watch": (0.545, 0.625, 0.900, 1.000)},
    2289: {"face": (0.325, 0.500, 0.185, 0.620),
           "hand": (0.500, 1.000, 0.380, 1.000),
           "watch": (0.755, 0.870, 0.440, 0.650)},
    2296: {"face": (0.360, 0.545, 0.185, 0.620),
           "hand": (0.620, 1.000, 0.260, 0.800),
           "watch": (0.845, 1.000, 0.330, 0.600)},
    # --- removal and presentation -----------------------------------------
    2310: {"face": (0.420, 0.600, 0.145, 0.600),
           "hand": (0.510, 0.680, 0.570, 1.000),
           "watch": (0.605, 0.670, 0.870, 1.000)},
    2316: {"face": (0.420, 0.600, 0.155, 0.620),
           "hand": (0.630, 0.780, 0.700, 1.000),
           "watch": (0.665, 0.735, 0.720, 0.850)},
    2330: {"face": (0.415, 0.615, 0.155, 0.660)},
    2338: {"face": (0.395, 0.575, 0.140, 0.620),
           "hand": (0.500, 0.670, 0.900, 1.000),
           "watch": (0.595, 0.655, 0.900, 1.000)},
    2355: {"face": (0.440, 0.615, 0.135, 0.620),
           "hand": (0.220, 0.440, 0.420, 1.000),
           "watch": (0.310, 0.405, 0.400, 0.950),
           "screen": (0.335, 0.395, 0.415, 0.530)},
    2365: {"face": (0.435, 0.630, 0.135, 0.720),
           "hand": (0.055, 0.450, 0.080, 1.000),
           "watch": (0.235, 0.435, 0.115, 1.000),
           "screen": (0.260, 0.420, 0.135, 0.475)},
    2379: {"face": (0.420, 0.600, 0.130, 0.680),
           "hand": (0.185, 0.500, 0.110, 1.000),
           "watch": (0.335, 0.485, 0.130, 0.900),
           "screen": (0.355, 0.465, 0.190, 0.450)},
    2384: {"face": (0.450, 0.615, 0.115, 0.720),
           "hand": (0.185, 0.530, 0.115, 1.000),
           "watch": (0.375, 0.510, 0.115, 0.900),
           "screen": (0.395, 0.490, 0.190, 0.450)},
    # --- the screen shown, and its withdrawal ------------------------------
    2395: {"face": (0.580, 0.705, 0.190, 0.950),
           "hand": (0.185, 0.620, 0.090, 1.000),
           "watch": (0.395, 0.605, 0.100, 1.000),
           "screen": (0.415, 0.585, 0.185, 0.620)},
    2401: {"face": (0.620, 0.755, 0.260, 1.000),
           "hand": (0.255, 0.710, 0.110, 1.000),
           "watch": (0.455, 0.685, 0.110, 1.000),
           "screen": (0.475, 0.665, 0.225, 0.675)},
    2413: {"face": (0.565, 0.730, 0.190, 1.000),
           "hand": (0.200, 0.655, 0.090, 1.000),
           "watch": (0.415, 0.630, 0.095, 1.000),
           "screen": (0.435, 0.615, 0.185, 0.600)},
    2419: {"face": (0.550, 0.735, 0.160, 1.000),
           "hand": (0.185, 0.600, 0.095, 1.000),
           "watch": (0.395, 0.585, 0.085, 1.000),
           "screen": (0.415, 0.565, 0.145, 0.575)},
    # The withdrawal. Codex: the clip ends at 242.42 s, 241.8 is only the last
    # SAMPLED frame, and by 242.30 the watch is already leaving to the left and
    # the face is clearly back. A region built only from the close-up position
    # would clip both. The screen is not annotated on these two: it is smeared
    # by the movement, and a box round an illegible display would be a guess.
    2424: {"face": (0.450, 0.615, 0.145, 0.720),
           "hand": (0.115, 0.460, 0.240, 1.000),
           "watch": (0.185, 0.440, 0.185, 0.600)},
    2425: {"face": (0.420, 0.600, 0.145, 0.720),
           "hand": (0.060, 0.390, 0.370, 1.000),
           "watch": (0.075, 0.320, 0.375, 0.665)},
}

#: Frames where the watch is out of frame or indistinguishable. NOT "the watch
#: is absent": it is somewhere the picture does not show, so it contributes
#: nothing to a union — which is right, because a region cannot be sized to hold
#: what no frame contains.
WATCH_NOT_VISIBLE: tuple[int, ...] = (2244, 2268, 2330)

#: Frames where the hands are below or outside the picture. `2330` carried a
#: hand box at (0.47, 0.63, 0.90, 1.00) in the 2x version; at 3x that region is
#: the speaker's shirt and there is no hand in the frame at all.
HAND_NOT_VISIBLE: tuple[int, ...] = (2330,)

#: Boxes that stop at a frame edge because the thing continues outside it. The
#: extent beyond is UNKNOWN and is not guessed. Derived rather than declared
#: would be better, and is what the hold-out half does — this list is kept
#: because `build_phase_regions` reports from it.
CLIPPED_BY_FRAME: tuple[int, ...] = (2244, 2253, 2268, 2275, 2289, 2296, 2310,
                                     2316, 2338, 2355, 2365, 2379, 2384, 2395,
                                     2401, 2413, 2419, 2424, 2425)

#: Frames that were annotated and are NOT IN THE CLIP. f2425 is at 242.5 s
#: against an end of 242.42; it was reached, and added to the screen phase's
#: withdrawal frames, on a time label that the `POS_MSEC` fix later showed to be
#: one frame early. The annotation stays because the frame was genuinely looked
#: at and the record of that is worth keeping — but a region cannot be
#: constrained by a picture the viewer never sees, and
#: `build_phase_regions._in_clip` refuses it.
OUT_OF_CLIP: tuple[int, ...] = (2425,)

#: How well the margins are resolved, in fractions of the frame. Read off a
#: 480x270 proxy at 3x against a grid every 0.1; nothing finer is claimed.
MARGIN_UNCERTAINTY = 0.01


# --- the HOLD-OUT set --------------------------------------------------------
#
# The frames above BUILT the regions, so checking a region against them proves
# nothing. These 26 took no part in it: they are the midpoints between the
# construction samples plus the frames either side of each boundary, addressed
# by index and shown disjoint on the DECODED index by
# `source_caption_observation.disjoint` rather than on the times that produced
# them.
#
# READ THE SAME WAY AS THE CONSTRUCTION SET, at 3x and one frame per sheet, for
# the reason the docstring above gives at length: the first pass of THIS half
# was read off 2x two-up sheets and made the same right-tile error — f2248's
# watch came out at x 0.74-0.83 where at 3x it is plainly at 0.495-0.60, bottom
# centre. Those readings were discarded, not averaged in.
#
# `screen` is annotated wherever the display is legible, including in the
# removal phase — the phase list says the screen must be COMPLETE only in the
# last phase, but a box that exists is recorded where it is seen.
HOLDOUT: dict[int, dict[str, tuple[float, float, float, float]]] = {
    # --- speech, either side of the 224.20 boundary -------------------------
    2240: {"face": (0.450, 0.615, 0.185, 0.600),
           "hand": (0.485, 0.650, 0.580, 0.900)},
    2241: {"face": (0.440, 0.605, 0.185, 0.600),
           "hand": (0.475, 0.650, 0.580, 0.900)},
    # --- watch worn and indicated ------------------------------------------
    2243: {"face": (0.435, 0.600, 0.200, 0.610),
           "hand": (0.420, 0.700, 0.600, 1.000)},
    2248: {"face": (0.435, 0.600, 0.185, 0.600),
           "hand": (0.450, 0.600, 0.600, 0.920),
           "watch": (0.495, 0.600, 0.900, 1.000)},
    2260: {"face": (0.435, 0.605, 0.200, 0.620),
           "hand": (0.375, 0.545, 0.600, 1.000),
           "watch": (0.545, 0.600, 0.900, 1.000)},
    2271: {"face": (0.420, 0.585, 0.185, 0.600),
           "hand": (0.410, 0.580, 0.580, 0.950),
           "watch": (0.545, 0.630, 0.900, 1.000)},
    2282: {"face": (0.345, 0.530, 0.145, 0.600),
           "hand": (0.500, 0.850, 0.680, 1.000),
           "watch": (0.645, 0.745, 0.780, 1.000)},
    2292: {"face": (0.325, 0.500, 0.185, 0.620),
           "hand": (0.600, 1.000, 0.280, 0.850),
           "watch": (0.905, 1.000, 0.520, 0.850)},
    2303: {"face": (0.385, 0.575, 0.155, 0.680),
           "hand": (0.600, 1.000, 0.195, 0.800),
           "watch": (0.735, 0.885, 0.155, 0.475),
           "screen": (0.755, 0.825, 0.190, 0.450)},
    2307: {"face": (0.400, 0.585, 0.155, 0.680),
           "hand": (0.660, 1.000, 0.190, 1.000),
           "watch": (0.700, 0.845, 0.340, 0.520)},
    2308: {"face": (0.415, 0.585, 0.140, 0.620),
           "hand": (0.500, 0.680, 0.550, 1.000),
           "watch": (0.575, 0.665, 0.850, 1.000)},
    # --- removal and presentation ------------------------------------------
    2311: {"face": (0.420, 0.600, 0.145, 0.600),
           "hand": (0.510, 0.700, 0.570, 1.000),
           "watch": (0.615, 0.685, 0.905, 1.000)},
    2313: {"face": (0.420, 0.600, 0.145, 0.600),
           "hand": (0.550, 0.720, 0.570, 1.000),
           "watch": (0.630, 0.695, 0.900, 1.000)},
    2323: {"face": (0.435, 0.630, 0.155, 0.680)},
    2334: {"face": (0.400, 0.580, 0.155, 0.660)},
    2346: {"face": (0.395, 0.580, 0.140, 0.620),
           "hand": (0.360, 0.700, 0.860, 1.000),
           "watch": (0.395, 0.530, 0.940, 1.000)},
    2360: {"face": (0.440, 0.630, 0.130, 0.720),
           "hand": (0.100, 0.440, 0.085, 1.000),
           "watch": (0.225, 0.415, 0.090, 1.000),
           "screen": (0.255, 0.405, 0.155, 0.455)},
    2372: {"face": (0.440, 0.630, 0.130, 0.720),
           "hand": (0.110, 0.470, 0.085, 1.000),
           "watch": (0.280, 0.460, 0.090, 1.000),
           "screen": (0.295, 0.445, 0.160, 0.475)},
    2381: {"face": (0.440, 0.600, 0.160, 0.620),
           "hand": (0.130, 0.490, 0.240, 1.000),
           "watch": (0.355, 0.475, 0.190, 0.730),
           "screen": (0.375, 0.465, 0.280, 0.490)},
    2389: {"face": (0.500, 0.660, 0.100, 0.720),
           "hand": (0.190, 0.590, 0.100, 1.000),
           "watch": (0.395, 0.575, 0.130, 0.870),
           "screen": (0.415, 0.555, 0.165, 0.510)},
    2392: {"face": (0.560, 0.700, 0.250, 0.900),
           "hand": (0.185, 0.600, 0.100, 1.000),
           "watch": (0.385, 0.585, 0.140, 1.000),
           "screen": (0.405, 0.565, 0.185, 0.575)},
    2393: {"face": (0.570, 0.700, 0.220, 0.900),
           "hand": (0.185, 0.600, 0.090, 1.000),
           "watch": (0.390, 0.585, 0.115, 1.000),
           "screen": (0.405, 0.565, 0.155, 0.575)},
    # --- the screen shown ---------------------------------------------------
    2396: {"face": (0.635, 0.745, 0.230, 0.930),
           "hand": (0.200, 0.670, 0.130, 1.000),
           "watch": (0.425, 0.655, 0.130, 1.000),
           "screen": (0.450, 0.630, 0.160, 0.600)},
    2398: {"face": (0.620, 0.760, 0.200, 0.920),
           "hand": (0.280, 0.720, 0.115, 1.000),
           "watch": (0.475, 0.700, 0.115, 1.000),
           "screen": (0.495, 0.675, 0.235, 0.650)},
    2407: {"face": (0.575, 0.730, 0.290, 1.000),
           "hand": (0.200, 0.665, 0.155, 1.000),
           "watch": (0.425, 0.640, 0.155, 1.000),
           "screen": (0.440, 0.615, 0.200, 0.665)},
    2416: {"face": (0.545, 0.740, 0.200, 1.000),
           "hand": (0.200, 0.640, 0.110, 1.000),
           "watch": (0.400, 0.615, 0.110, 1.000),
           "screen": (0.420, 0.590, 0.175, 0.600)},
}

#: Hold-out frames where the watch is out of frame or indistinguishable. On
#: 2240, 2241 and 2243 the bright thing at the bottom is the SHIRT CUFF, not the
#: band — at 3x the two are plainly different objects, and calling the cuff a
#: watch would have put a fabricated box either side of the first boundary,
#: which is the boundary the transition test turns on.
HOLDOUT_WATCH_NOT_VISIBLE: tuple[int, ...] = (2240, 2241, 2243, 2323, 2334)

#: Hold-out frames where the hands are below or outside the picture.
HOLDOUT_HAND_NOT_VISIBLE: tuple[int, ...] = (2323, 2334)


# --- what the replay pass changed, and why -----------------------------------
#
# `scripts/replay_annotations.py` draws the STORED coordinates back over the
# frame each one names. Reading a box off a ruler and typing it into a dict are
# two operations and only the first had ever been checked, while everything the
# regions rest on passes through the second. All 46 frames were replayed and
# looked at; every decoded index matched the one asked for, so the ADDRESSING
# was sound, and 24 boxes on 19 frames were not.
#
# THE ERROR IS MOSTLY ONE-DIRECTIONAL AND HAS A CAUSE. Box TOPS were drawn
# 0.02-0.06 of the frame too high — above the fingertips, above the durag, in
# sky or building — because a dark object against a bright sky bleeds upward at
# 3x and the reading followed the bleed. Two `face` boxes on the close-up frames
# also ran right, into the bokeh beside the head sliver.
#
# BUT NOT ENTIRELY, WHICH IS THE PART WORTH KEEPING. f2289's `hand` stopped at
# 0.900 while the forearm runs to the frame edge, and f2389's `screen` cut the
# display at 0.450 where it reaches 0.510. Both make the region's job HARDER.
# A correction pass that only ever loosened the requirement would be indis-
# tinguishable from tuning until the answer came out green; these two are the
# evidence it was not that.
#
# THE SUPERSEDED VALUES ARE HERE and not in the git history alone, because the
# next reader's question is "was this box always this, or was it moved after a
# verdict" — and that is exactly the question a corrected annotation has to be
# able to answer about itself.
#
# CORRECTED (superseded -> current):
#
#    WATCH    f2289 hand    (0.5, 0.9, 0.36, 1.0) -> (0.5, 1.0, 0.38, 1.0)
#             the forearm runs to the right frame edge; the box stopped at 0.900
#             and left the rest of it out
#    WATCH    f2289 watch   (0.735, 0.855, 0.43, 0.62) -> (0.755, 0.87, 0.44, 0.65)
#             offset up and left of the device by about 0.02
#    WATCH    f2296 hand    (0.62, 1.0, 0.22, 0.8) -> (0.62, 1.0, 0.26, 0.8)
#             top 0.220 was above the arm, in sky
#    WATCH    f2316 hand    (0.63, 0.78, 0.55, 1.0) -> (0.63, 0.78, 0.7, 1.0)
#             top 0.550 was 0.15 above the hand, over his chest
#    WATCH    f2338 face    (0.395, 0.575, 0.1, 0.62) -> (0.395, 0.575, 0.14, 0.62)
#             top 0.100 was above the durag, in building
#    WATCH    f2355 face    (0.44, 0.615, 0.115, 0.62) -> (0.44, 0.615, 0.135, 0.62)
#             top 0.115 was just above the durag
#    WATCH    f2355 hand    (0.22, 0.44, 0.36, 1.0) -> (0.22, 0.44, 0.42, 1.0)
#             top 0.360 was above the hand
#    WATCH    f2365 hand    (0.055, 0.45, 0.045, 1.0) -> (0.055, 0.45, 0.08, 1.0)
#             top 0.045 was above the fingertips, in sky
#    WATCH    f2384 face    (0.45, 0.615, 0.1, 0.72) -> (0.45, 0.615, 0.115, 0.72)
#             top 0.100 was just above the durag
#    WATCH    f2395 face    (0.58, 0.72, 0.19, 0.95) -> (0.58, 0.705, 0.19, 0.95)
#             right 0.720 reached past the head sliver into bokeh
#    WATCH    f2401 face    (0.62, 0.775, 0.26, 1.0) -> (0.62, 0.755, 0.26, 1.0)
#             right 0.775 reached past the head sliver into bokeh
#    WATCH    f2419 hand    (0.185, 0.6, 0.08, 1.0) -> (0.185, 0.6, 0.095, 1.0)
#             top 0.080 was just above the fingertips
#    HOLDOUT  f2282 watch   (0.645, 0.745, 0.73, 1.0) -> (0.645, 0.745, 0.78, 1.0)
#             top 0.730 was above the band, on the forearm
#    HOLDOUT  f2303 hand    (0.6, 1.0, 0.14, 0.8) -> (0.6, 1.0, 0.195, 0.8)
#             top 0.140 was above the arm, in sky and building
#    HOLDOUT  f2307 hand    (0.66, 1.0, 0.13, 1.0) -> (0.66, 1.0, 0.19, 1.0)
#             top 0.130 was above the arm, in sky
#    HOLDOUT  f2360 hand    (0.1, 0.44, 0.05, 1.0) -> (0.1, 0.44, 0.085, 1.0)
#             top 0.050 was above the fingertips, in sky
#    HOLDOUT  f2372 hand    (0.11, 0.47, 0.05, 1.0) -> (0.11, 0.47, 0.085, 1.0)
#             top 0.050 was above the fingertips, in sky
#    HOLDOUT  f2381 hand    (0.13, 0.49, 0.22, 1.0) -> (0.13, 0.49, 0.24, 1.0)
#             top 0.220 was just above the hand
#    HOLDOUT  f2381 watch   (0.355, 0.475, 0.19, 0.85) -> (0.355, 0.475, 0.19, 0.73)
#             bottom 0.850 ran past the end of the band onto his shirt
#    HOLDOUT  f2389 face    (0.5, 0.66, 0.06, 0.72) -> (0.5, 0.66, 0.1, 0.72)
#             top 0.060 was above the durag
#    HOLDOUT  f2389 screen  (0.415, 0.555, 0.165, 0.45) -> (0.415, 0.555, 0.165, 0.51)
#             bottom 0.450 CUT THE DISPLAY, which runs to about 0.51 — the only
#             box in the set that was too SMALL
#    HOLDOUT  f2396 face    (0.62, 0.75, 0.25, 0.95) -> (0.635, 0.745, 0.23, 0.93)
#             0.620-0.750 / 0.250-0.950 reached into the bokeh right of the head
#             sliver and below its chin
#    HOLDOUT  f2396 hand    (0.2, 0.67, 0.09, 1.0) -> (0.2, 0.67, 0.13, 1.0)
#             top 0.090 was above the fingertips
#    HOLDOUT  f2398 face    (0.64, 0.79, 0.28, 1.0) -> (0.62, 0.76, 0.2, 0.92)
#             0.640-0.790 / 0.280-1.000 reached into the bokeh right of the head
#             sliver; this box alone produced the one CLIP the previous
#             verification reported


# --- the FRESH lot, next door --------------------------------------------------
#
# Split into `watch_annotations_fresh.py` ONLY because this file reached 544
# lines against a limit of 500. It is re-exported here so that every consumer
# still sees one annotation module, which is what it is: the three lots differ
# in what they are FOR, not in how they were read.


def _sibling(name: str):
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_fresh = _sibling("watch_annotations_fresh.py")

FRESH = _fresh.FRESH
FRESH_WATCH_NOT_VISIBLE = _fresh.FRESH_WATCH_NOT_VISIBLE
FRESH_HAND_NOT_VISIBLE = _fresh.FRESH_HAND_NOT_VISIBLE
WITHDRAWAL_INTERVAL = _fresh.WITHDRAWAL_INTERVAL
