"""The FRESH lot for `b23c14c41495` — the frames that judge the corrected
candidate.

    imported by scripts/watch_annotations.py, which re-exports these

SPLIT OUT ONLY FOR SIZE. `watch_annotations.py` reached 544 lines with this
in it and the repo's limit is 500. Everything about how these were obtained
is in that file's docstring; the two are one annotation and are read as one.
"""

from __future__ import annotations
#
# The 26 hold-out frames above were corrected in the same replay pass that moved
# the regions, so they can no longer confirm the candidate they helped change.
# `scripts/select_fresh_lot.py` chose these BEFORE anyone looked at them, by a
# rule agreed with Codex in advance, from what the corpus had left: four equal
# intervals per phase, three frames each at floor(n/4), floor(n/2), floor(3n/4)
# of the sorted eligible list, excluding every frame in either table above, the
# +/-0.5 s transition windows, and the three frames read to re-derive the
# boundaries.
#
# 34 OF 36, AND THE HOLE IS NAMED. `screen` q0 [239.40, 240.155) had a single
# eligible frame, f2400, and was not topped up from elsewhere — a lot filled
# from wherever frames were easy to find is a lot chosen for convenience. The
# five withdrawal frames f2420..f2424 are inspected whole and separately, which
# is why f2420 and f2423 are here without being part of the 34.
#
# READ THE SAME WAY AS BOTH SETS ABOVE: 3x, one frame per sheet, with no region
# and no verdict on screen. Codex: "Corecteaza marginile fara regiunea
# candidatului si verdictul afisate."
FRESH: dict[int, dict[str, tuple[float, float, float, float]]] = {
    # --- watch worn and indicated ------------------------------------------
    2251: {"face": (0.425, 0.605, 0.155, 0.605),
           "hand": (0.420, 0.600, 0.600, 1.000),
           "watch": (0.530, 0.610, 0.920, 1.000)},
    2254: {"face": (0.415, 0.595, 0.155, 0.600),
           "hand": (0.395, 0.580, 0.600, 1.000),
           "watch": (0.545, 0.615, 0.900, 1.000)},
    2256: {"face": (0.400, 0.585, 0.150, 0.600),
           "hand": (0.380, 0.560, 0.600, 1.000),
           "watch": (0.545, 0.620, 0.900, 1.000)},
    2263: {"face": (0.415, 0.600, 0.150, 0.600),
           "hand": (0.365, 0.545, 0.600, 1.000),
           "watch": (0.530, 0.615, 0.890, 1.000)},
    2266: {"face": (0.400, 0.585, 0.145, 0.600),
           "hand": (0.355, 0.545, 0.600, 1.000),
           "watch": (0.510, 0.585, 0.900, 1.000)},
    2270: {"face": (0.415, 0.600, 0.150, 0.600),
           "hand": (0.400, 0.575, 0.600, 1.000),
           "watch": (0.545, 0.600, 0.920, 1.000)},
    2279: {"face": (0.335, 0.520, 0.130, 0.590),
           "hand": (0.440, 0.720, 0.660, 1.000),
           "watch": (0.645, 0.690, 0.735, 0.900)},
    2284: {"face": (0.335, 0.520, 0.135, 0.600),
           "hand": (0.560, 0.870, 0.620, 1.000),
           "watch": (0.715, 0.775, 0.755, 0.930)},
    2287: {"face": (0.325, 0.505, 0.155, 0.620),
           "hand": (0.560, 1.000, 0.360, 1.000),
           "watch": (0.695, 0.795, 0.620, 0.800)},
    2295: {"face": (0.355, 0.540, 0.180, 0.615),
           "hand": (0.620, 1.000, 0.220, 0.800),
           "watch": (0.915, 1.000, 0.340, 0.600)},
    2298: {"face": (0.370, 0.545, 0.185, 0.620),
           "hand": (0.630, 1.000, 0.220, 0.850),
           "watch": (0.755, 0.900, 0.240, 0.510)},
    2300: {"face": (0.375, 0.560, 0.180, 0.620),
           "hand": (0.620, 1.000, 0.200, 0.850),
           "watch": (0.745, 0.885, 0.255, 0.530)},
    # --- removal and presentation ------------------------------------------
    # On the first four his head is down over his wrist and BOTH hands are below
    # the frame. Not "the watch is absent": it is somewhere the picture does not
    # show, and it contributes nothing to a union.
    2319: {"face": (0.400, 0.640, 0.155, 0.680)},
    2322: {"face": (0.420, 0.635, 0.155, 0.680)},
    2326: {"face": (0.400, 0.625, 0.160, 0.690)},
    2336: {"face": (0.395, 0.590, 0.155, 0.660)},
    2342: {"face": (0.375, 0.565, 0.135, 0.640),
           "hand": (0.440, 0.680, 0.900, 1.000),
           "watch": (0.490, 0.625, 0.905, 1.000)},
    2347: {"face": (0.395, 0.580, 0.150, 0.640),
           "hand": (0.440, 0.700, 0.860, 1.000),
           "watch": (0.445, 0.615, 0.865, 1.000)},
    2357: {"face": (0.435, 0.615, 0.115, 0.640),
           "hand": (0.185, 0.440, 0.235, 1.000),
           "watch": (0.300, 0.425, 0.240, 1.000),
           "screen": (0.325, 0.415, 0.265, 0.575)},
    2362: {"face": (0.425, 0.610, 0.115, 0.660),
           "hand": (0.095, 0.440, 0.030, 1.000),
           "watch": (0.215, 0.415, 0.075, 1.000),
           "screen": (0.245, 0.400, 0.100, 0.455)},
    2367: {"face": (0.445, 0.625, 0.100, 0.670),
           "hand": (0.065, 0.460, 0.045, 1.000),
           "watch": (0.235, 0.445, 0.085, 1.000),
           "screen": (0.255, 0.425, 0.115, 0.485)},
    2376: {"face": (0.425, 0.605, 0.130, 0.680),
           "hand": (0.150, 0.470, 0.115, 1.000),
           "watch": (0.325, 0.450, 0.195, 1.000),
           "screen": (0.340, 0.435, 0.220, 0.520)},
    2380: {"face": (0.430, 0.610, 0.135, 0.660),
           "hand": (0.200, 0.500, 0.170, 1.000),
           "watch": (0.355, 0.480, 0.160, 0.860),
           "screen": (0.375, 0.465, 0.215, 0.490)},
    2385: {"face": (0.460, 0.625, 0.115, 0.700),
           "hand": (0.235, 0.545, 0.130, 1.000),
           "watch": (0.390, 0.520, 0.130, 0.900),
           "screen": (0.405, 0.505, 0.220, 0.520)},
    # --- the screen shown, and the withdrawal ------------------------------
    2400: {"face": (0.645, 0.790, 0.145, 1.000),
           "hand": (0.285, 0.720, 0.115, 1.000),
           "watch": (0.485, 0.705, 0.115, 1.000),
           "screen": (0.505, 0.680, 0.245, 0.640)},
    2403: {"face": (0.620, 0.775, 0.150, 1.000),
           "hand": (0.250, 0.685, 0.120, 1.000),
           "watch": (0.455, 0.665, 0.120, 1.000),
           "screen": (0.470, 0.640, 0.255, 0.655)},
    2405: {"face": (0.600, 0.755, 0.155, 1.000),
           "hand": (0.235, 0.660, 0.130, 1.000),
           "watch": (0.435, 0.640, 0.130, 1.000),
           "screen": (0.450, 0.615, 0.260, 0.675)},
    2408: {"face": (0.590, 0.745, 0.135, 1.000),
           "hand": (0.225, 0.660, 0.095, 1.000),
           "watch": (0.425, 0.635, 0.095, 1.000),
           "screen": (0.440, 0.610, 0.230, 0.615)},
    2411: {"face": (0.600, 0.760, 0.130, 1.000),
           "hand": (0.225, 0.670, 0.085, 1.000),
           "watch": (0.435, 0.650, 0.085, 1.000),
           "screen": (0.450, 0.625, 0.220, 0.610)},
    2412: {"face": (0.600, 0.760, 0.125, 1.000),
           "hand": (0.225, 0.670, 0.085, 1.000),
           "watch": (0.435, 0.650, 0.085, 1.000),
           "screen": (0.450, 0.625, 0.210, 0.605)},
    2414: {"face": (0.590, 0.755, 0.120, 1.000),
           "hand": (0.215, 0.660, 0.075, 1.000),
           "watch": (0.425, 0.640, 0.075, 1.000),
           "screen": (0.440, 0.615, 0.205, 0.605)},
    2418: {"face": (0.545, 0.735, 0.145, 1.000),
           "hand": (0.195, 0.605, 0.085, 1.000),
           "watch": (0.395, 0.590, 0.085, 1.000),
           "screen": (0.410, 0.565, 0.205, 0.590)},
    2420: {"face": (0.530, 0.725, 0.140, 1.000),
           "hand": (0.185, 0.585, 0.085, 1.000),
           "watch": (0.375, 0.575, 0.085, 1.000),
           "screen": (0.390, 0.550, 0.195, 0.570)},
    2421: {"face": (0.520, 0.715, 0.155, 1.000),
           "hand": (0.185, 0.575, 0.095, 1.000),
           "watch": (0.375, 0.560, 0.095, 1.000),
           "screen": (0.390, 0.535, 0.215, 0.575)},
    2422: {"face": (0.485, 0.685, 0.155, 1.000),
           "hand": (0.185, 0.545, 0.115, 1.000),
           "watch": (0.365, 0.525, 0.135, 1.000),
           "screen": (0.380, 0.505, 0.230, 0.560)},
    2423: {"face": (0.435, 0.655, 0.155, 1.000),
           "hand": (0.195, 0.500, 0.130, 1.000),
           "watch": (0.335, 0.480, 0.185, 0.950),
           "screen": (0.365, 0.470, 0.255, 0.525)},
}

#: Fresh frames where the watch is out of frame or indistinguishable — the four
#: where his head is down over his wrist and both hands are below the picture.
FRESH_WATCH_NOT_VISIBLE: tuple[int, ...] = (2319, 2322, 2326, 2336)

#: And the same four for the hands.
FRESH_HAND_NOT_VISIBLE: tuple[int, ...] = (2319, 2322, 2326, 2336)

#: WHERE THE WITHDRAWAL ACTUALLY STARTS, from the five frames inspected whole.
#: The watch's horizontal centre holds near 0.49 through f2421 and then moves
#: left every frame: 0.445 at f2422, 0.408 at f2423, 0.313 at f2424. So the
#: movement begins in (242.10, 242.20] — one proxy frame, the finest this source
#: allows — and NOT at 242.30, which is where a note written under the old time
#: labels put it. Recorded as an interval, like the gesture at 224.20: where a
#: continuous movement "starts" is a judgement about a continuous thing, and a
#: single number would state a precision nothing supports.
WITHDRAWAL_INTERVAL = (242.10, 242.20)
