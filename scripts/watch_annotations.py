"""The agent's visually confirmed geometry for the WATCH phases.

    imported by scripts/build_phase_regions.py

WHY THIS EXISTS. `dynamic_plan["subject"]["face"]` is a clip-wide average of the
speaker's face and has no authority over the frames where the subject is a watch.
No detector produces the watch, so §A's answer is an agent annotation, and
`build_phase_regions` refuses a phase without one rather than falling back.

HOW THESE WERE OBTAINED. `scripts/dump_source_transition.py --frames ... --grid`
renders each CONSTRUCTION frame at 2x with a labelled grid every 0.1 of width and
height, two frames to a sheet so the grid is readable. Every box below was read
off those sheets against that grid.

ADDRESSED BY FRAME INDEX, NOT BY TIME. Seeking by time lands on whatever the
decoder gives: asking for 224.3 s returned f2243 where the construction sample is
f2244. The first pass of this annotation was made on those off-by-one frames and
would have described a different picture from the one the region is built for.

THE MARGINS ARE APPROXIMATE AND SAY SO. Read off a 480x270 proxy magnified 2x;
nothing here is resolved better than about +/-0.01 of the frame. Where the watch
leaves the frame the box stops at the boundary and `clipped` records it — the
part outside is not in any picture, so a region cannot be sized to hold it, and
pretending to know its extent would be inventing geometry.

AND `not_visible` IS NOT `absent`. On two construction frames the watch is out of
frame or indistinguishable. Those contribute nothing to a union, which is right,
and they are listed so that "the agent did not annotate this" and "there was
nothing to annotate" stay apart.
"""

from __future__ import annotations

#: `{frame index: {what: (x0, x1, y0, y1)}}` in fractions of the frame.
#: `watch` is the device; `screen` is its display alone, which is what the last
#: phase must keep complete; `hand` is the hand or forearm presenting it; `face`
#: is the speaker where he is visible.
WATCH: dict[int, dict[str, tuple[float, float, float, float]]] = {
    # --- worn and indicated ------------------------------------------------
    2244: {"hand": (0.50, 0.68, 0.82, 1.00), "face": (0.42, 0.60, 0.27, 0.63)},
    2253: {"watch": (0.74, 0.82, 0.86, 1.00), "hand": (0.58, 0.82, 0.58, 1.00),
           "face": (0.60, 0.78, 0.22, 0.60)},
    2268: {"hand": (0.38, 0.58, 0.62, 1.00), "face": (0.42, 0.60, 0.28, 0.65)},
    2275: {"watch": (0.72, 0.80, 0.88, 1.00), "hand": (0.62, 0.80, 0.55, 1.00),
           "face": (0.68, 0.85, 0.28, 0.68)},
    2289: {"watch": (0.75, 0.90, 0.53, 0.80), "hand": (0.55, 1.00, 0.38, 0.90),
           "face": (0.33, 0.50, 0.22, 0.63)},
    2296: {"watch": (0.93, 1.00, 0.38, 0.62), "hand": (0.80, 1.00, 0.28, 0.75),
           "face": (0.66, 0.80, 0.25, 0.65)},
    # --- removal and presentation -----------------------------------------
    2310: {"watch": (0.58, 0.68, 0.82, 1.00), "hand": (0.48, 0.68, 0.58, 1.00),
           "face": (0.42, 0.58, 0.22, 0.60)},
    2316: {"watch": (0.78, 0.88, 0.72, 0.95), "hand": (0.72, 0.90, 0.62, 1.00),
           "face": (0.68, 0.83, 0.18, 0.62)},
    2330: {"hand": (0.47, 0.63, 0.90, 1.00), "face": (0.42, 0.60, 0.18, 0.62)},
    2338: {"watch": (0.78, 0.88, 0.90, 1.00), "hand": (0.72, 0.88, 0.88, 1.00),
           "face": (0.68, 0.85, 0.22, 0.65)},
    2355: {"watch": (0.30, 0.41, 0.46, 1.00), "hand": (0.22, 0.42, 0.44, 1.00),
           "face": (0.44, 0.62, 0.20, 0.68)},
    2365: {"watch": (0.51, 0.72, 0.15, 1.00), "hand": (0.48, 0.78, 0.05, 1.00),
           "face": (0.70, 0.85, 0.22, 0.68)},
    2379: {"watch": (0.31, 0.48, 0.10, 0.85), "hand": (0.22, 0.50, 0.08, 0.95),
           "face": (0.44, 0.60, 0.20, 0.68)},
    2384: {"watch": (0.66, 0.79, 0.12, 0.80), "hand": (0.60, 0.82, 0.10, 0.90),
           "face": (0.74, 0.88, 0.18, 0.65)},
    # --- the screen shown, and its withdrawal ------------------------------
    2395: {"screen": (0.36, 0.56, 0.13, 0.60), "watch": (0.33, 0.57, 0.05, 1.00),
           "hand": (0.10, 0.58, 0.02, 1.00), "face": (0.55, 0.72, 0.25, 0.85)},
    2401: {"screen": (0.735, 0.86, 0.22, 0.62),
           "watch": (0.72, 0.87, 0.12, 0.95), "hand": (0.60, 0.88, 0.08, 1.00),
           "face": (0.85, 1.00, 0.30, 0.90)},
    2413: {"screen": (0.42, 0.58, 0.15, 0.62), "watch": (0.40, 0.60, 0.05, 1.00),
           "hand": (0.13, 0.60, 0.02, 1.00), "face": (0.58, 0.75, 0.20, 0.90)},
    2419: {"screen": (0.34, 0.47, 0.14, 0.52), "watch": (0.32, 0.49, 0.05, 0.95),
           "hand": (0.10, 0.50, 0.03, 1.00), "face": (0.48, 0.65, 0.20, 0.85)},
    # The withdrawal. Codex: the clip ends at 242.42 s, 241.8 is only the last
    # SAMPLED frame, and by 242.30 the watch is already leaving to the left and
    # the face is clearly back. A region built only from the close-up position
    # would clip both.
    2424: {"watch": (0.10, 0.24, 0.30, 0.75), "hand": (0.02, 0.28, 0.28, 1.00),
           "face": (0.42, 0.62, 0.20, 0.85)},
    2425: {"watch": (0.10, 0.30, 0.42, 0.72), "hand": (0.05, 0.32, 0.35, 1.00),
           "face": (0.42, 0.62, 0.18, 0.85)},
}

#: Frames where the watch is out of frame or indistinguishable. NOT "the watch
#: is absent": it is somewhere the picture does not show, so it contributes
#: nothing to a union — which is right, because a region cannot be sized to hold
#: what no frame contains.
WATCH_NOT_VISIBLE: tuple[int, ...] = (2244, 2268, 2330)

#: Boxes that stop at a frame edge because the thing continues outside it. The
#: extent beyond is UNKNOWN and is not guessed.
CLIPPED_BY_FRAME: tuple[int, ...] = (2253, 2275, 2310, 2338, 2355, 2365,
                                     2395, 2413, 2296)

#: How well the margins are resolved, in fractions of the frame. Read off a
#: 480x270 proxy at 2x against a grid every 0.1; nothing finer is claimed.
MARGIN_UNCERTAINTY = 0.01
