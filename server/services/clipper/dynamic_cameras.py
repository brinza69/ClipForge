"""
ClipForge — AI Stream Clipper: what a camera is, and where to point it.

Split out of `dynamic_edit.py`, which had grown past the 500-line limit. The
division is by question: this module answers "what rectangle do I crop", the
planner answers "when do I cut and to which camera". Nothing here knows about
time or shots.

The camera ladder is parameterised in MULTIPLES OF THE DETECTED FACE WIDTH, not
in crop factors of the frame, and that is deliberate. The nine reference Shorts
(docs/refs/) measured their ladder as crop factors — 1.00 / 0.88 / 0.76 / 0.71 of
the full-height 9:16 window — but every one of them is a source where the subject
already fills the frame. Our facecam is a small inset in a 1920x1080 stream, so
cropping 0.76 of the frame would not produce a shot anything like the reference
it came from. Anchoring on face width preserves what the ladder actually means:
how big the subject reads on screen. The reference PROPORTIONS carry over; their
absolute crop factors do not.

Coordinate spaces, the same trap as layout.py: face boxes arrive in PROXY
pixels; every rect emitted here is in SOURCE pixels.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Any, Sequence

__all__ = ["ASPECT", "CAMERAS", "DEFAULT_STYLE", "camera_rects",
           "ACTION_BAND", "action_band", "face_clusters"]

ASPECT = 9.0 / 16.0


# Each camera is (height as a multiple of the detected face width, headroom).
# Headroom is where the face sits inside the crop as a fraction of crop height
# from the top: tighter shots ride higher because the eyes, not the chin, are
# what the viewer tracks. The GAME cameras ignore both and frame the action.
# `face_medium` is the rung the references say was missing. They step through
# three framings where we stepped through two, and the spacing they use is
# roughly geometric — 1.00 / 0.88 / 0.76 of the widest, each about 0.87 of the
# one before. Our span is wider than theirs (2.5/3.8 = 0.66 against their 0.76),
# so the geometric midpoint of the two rungs we already had lands at
# sqrt(3.8 * 2.5) = 3.08. Headroom interpolates between its neighbours.
#
# The endpoints 3.8 and 2.5 are INHERITED, not measured — they predate the
# reference profiling and nothing in docs/refs/ confirms them for this VOD.
# Only the spacing between them comes from the references.
CAMERAS: dict[str, tuple[float, float]] = {
    "face":        (3.8, 0.44),
    "face_medium": (3.1, 0.42),
    "face_tight":  (2.5, 0.41),
    "game":        (0.0, 0.50),
    "game_tight":  (0.0, 0.50),
}
# Ordered widest -> tightest. The planner walks these by index, so the order is
# load-bearing, not cosmetic.
_FACE_CAMS = ("face", "face_medium", "face_tight")
_GAME_CAMS = ("game", "game_tight")

DEFAULT_STYLE: dict[str, Any] = {
    # Shot grammar. All nine references now have a full profile (docs/refs/) and
    # they are tighter than the first mechanical pass suggested: eight of nine run
    # 17-45 cuts/min, and the three that are actually built like a stream VOD
    # average 1.45 / 1.71 / 2.21s per shot with p90 at 2.66-3.63s. The old 1.25 /
    # 2.40 came from a cut-rate table that has since been measured wrong on five
    # of nine clips -- it cut faster than any reference we now trust.
    "min_shot_s": 0.60,
    "target_shot_s": 1.80,
    "max_shot_s": 3.00,
    "pause_gap_s": 0.14,
    # What counts as "he is talking" / "something is happening".
    "speech_ratio_on": 0.30,
    "action_pct": 0.60,
    # Below this RAW motion the gameplay region is dead (a loading screen, a
    # static menu, an empty sky) and cutting to it would waste a shot on
    # nothing. Absolute on purpose: a percentile cannot tell "the quietest
    # moment of a busy clip" from "nothing moved at all", and the whole point of
    # the check is to catch the second one.
    "game_dead_below": 0.35,
    # ...and below this spatial standard deviation there is nothing IN it. A shot
    # cuts to gameplay only if it clears both.
    #
    # Measured through the same code path on synthetic content (never on the VOD,
    # which is not on this machine). Numbers move a little between runs because
    # the animated sources are sampled over two seconds:
    #
    #   pure black          0.0     flat colour            0.0
    #   gentle gradient     2.5-5.1 downsampled noise      2.0
    #   black + spinner     6.1     black + progress bar   44.9
    #   strong gradient     33-68   smpte bars             45.8
    #   busy video          76.8
    #
    # 8.0 sits in the empty gap between everything genuinely blank (max 6.1) and
    # anything with a picture in it (33+).
    #
    # BUT READ THE PROGRESS-BAR ROW BEFORE TRUSTING THIS TO FIX LOADING SCREENS.
    # A white bar on black is maximum contrast, so it scores 44.9 — higher than a
    # strong gradient. This guard rejects a screen that is genuinely EMPTY; it
    # does not reject a loading screen that has UI drawn on it, and no spatial
    # variance measure would, because such a screen is objectively high-contrast.
    # Catching those needs a different signal (template match on the HUD, or OCR),
    # which is not what this is.
    "game_flat_below": 8.0,
    # ...and above this share of flat mid-grey it is an inventory or crafting
    # panel, not the game. The third rejection exists because the first two
    # provably miss menus: a panel is high-contrast and the cursor keeps moving,
    # so detail and motion both call it alive, and 17% of the tested slice was
    # menu screens.
    #
    # Measured on 90 frames: 74 gameplay frames scored under 0.02, 15 menu
    # frames over 0.11, and NOTHING landed between 0.05 and 0.11. 0.08 sits in
    # that empty gap. Saturation was tried first and separates the same frames
    # far less cleanly (44-59 against 62-105, overlapping); a lattice measure
    # ran backwards, because Minecraft terrain is more periodic than a menu is.
    #
    # This assumes a UI panel drawn in flat neutral grey, which is Minecraft and
    # most game menus but is not a law. A game with a coloured or transparent
    # inventory would need its own palette.
    "game_ui_above": 0.08,
    # In-shot camera moves — OFF by default, and that is a finding, not an
    # oversight. It holds across all nine profiles, but it splits by SOURCE TYPE
    # rather than by house style: the two references that are locked-off stream
    # captures cut between discrete crop windows — our exact case — measure
    # s=1.0000 with sub-pixel translation and no ramp at all. Every reference
    # built any other way ramps continuously (+0.8-1.2%/s, -2.5 to -3.2%/s,
    # +46%/s on a studio panel), so this is not a vote we won; it is the value
    # for the kind of source we point at. shake_px=0 is the best-supported of the
    # three — no reference adds synthetic shake anywhere. The energy comes from
    # the cut, not from motion inside it. Turn them up per clip if a long shot
    # needs rescuing: --style '{"push_amount":0.07,"shake_px":6}'.
    "push_min_shot_s": 0.95,
    "push_amount": 0.0,
    "push_hz": 10.0,
    "snap_s": 0.09,
    "snap_amount": 0.0,
    "shake_energy_pct": 0.70,
    "shake_px": 0.0,
    # Hits: a short blow-out on the loudest onsets. The references run ~0.25s
    # warm/white flashes on their transitions, not single-frame blips.
    "max_hits": 16,
    "hit_min_gap_s": 0.60,
    "hit_peak_pct": 0.45,
    "flash_s": 0.18,
    # Grade.
    "saturation": 1.16,
    "contrast": 1.07,
    # Framing geometry.
    "chat_margin_pct": 0.09,     # right-hand strip the gameplay camera avoids
    # Both gameplay rungs now take the full frame height, which is the widest a
    # 9:16 crop of a 16:9 frame can ever be. Chosen by the owner off a three-way
    # A/B on the one clip anyone has watched (13030.57s of slice4h00test, same
    # shot list and captions in all three, only these two keys moved):
    #
    #   0.86 / 0.64   388px, 20.2% of frame width   no hotbar, no hearts
    #   1.00 / 0.87   526px, 27.4%                  game HUD visible
    #   1.00 / 1.00   606px, 31.6%                  all of it, plus a sliver of
    #                                               the stream's own counter
    #
    # 0.86 was there to skip the stream's top HUD and bottom bar, and it did —
    # along with the HUD of the GAME, which is the half a viewer needs. The
    # stream's overlay coming back into frame is the accepted price.
    #
    # 31.6% is the ceiling, not a setting: at full height the width is fixed by
    # the aspect ratio. If a gameplay shot still reads too tight, no value here
    # fixes it — that needs a letterboxed game band, a different layout.
    #
    # These being equal makes `game` and `game_tight` the same rectangle, so the
    # ladder has one rung and a g->G step would cut on nothing. `_merge_dead_cuts`
    # in dynamic_edit.py is what handles that, and it exists for this.
    "game_height_pct": 1.00,
    "game_zoom": 1.00,
    "game_centre_y_pct": 0.47,
    # The reference edits never sit on one subject for long: after this many
    # consecutive shots the planner crosses to the other camera family whatever
    # the signals say.
    "max_same_family": 2,
    # Which rung of a family a shot lands on, by its energy. One threshold fewer
    # than the family has rungs; a shot steps up a rung each time it clears one.
    #
    # UNMEASURED, unlike the ladder geometry above. The references show WHAT the
    # rungs are, never WHAT TRIGGERS a step between them — none of the nine
    # contains a facecam-plus-gameplay source at all, so there was nothing to
    # measure the trigger against. These are chosen, and they are the first thing
    # to tune once a render can be watched.
    #
    # Game keeps its original single 0.55 boundary exactly. Face brackets it
    # instead of straddling it: what used to jump straight to the tightest shot
    # at 0.55 now lands on the middle rung, and only real peaks go tight.
    "face_rung_energy": [0.42, 0.72],
    "game_rung_energy": [0.55],
}


# ---------------------------------------------------------------------------
# small helpers — shared with the planner, which imports them back
# ---------------------------------------------------------------------------

def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out


def _even(value: float) -> int:
    return int(value) // 2 * 2

def _median(values: Sequence[float], default: float = 0.0) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else default


# ---------------------------------------------------------------------------
# where the subject is
# ---------------------------------------------------------------------------

def _face_samples(face_track: Sequence[dict], sx: float, sy: float,
                  clip_start: float) -> list[tuple[float, float, float, float]]:
    """[(clip-relative t, cx, cy, w)] in SOURCE pixels, biggest box per sample."""
    out: list[tuple[float, float, float, float]] = []
    for sample in face_track or []:
        if not isinstance(sample, dict):
            continue
        boxes = [b for b in (sample.get("boxes") or [])
                 if isinstance(b, (list, tuple)) and len(b) >= 4]
        if not boxes:
            continue
        box = max(boxes, key=lambda b: _f(b[2]) * _f(b[3]))
        out.append((
            _f(sample.get("t")) - clip_start,
            (_f(box[0]) + _f(box[2]) / 2.0) * sx,
            (_f(box[1]) + _f(box[3]) / 2.0) * sy,
            _f(box[2]) * sx,
        ))
    out.sort(key=lambda s: s[0])
    return out


def _dominant(samples: Sequence[tuple[float, float, float, float]],
              src_w: int, src_h: int
              ) -> tuple[list[tuple[float, float, float, float]], dict[str, float]]:
    """Keep the samples belonging to the biggest face cluster.

    A Haar cascade on a busy stream frame fires on posters, avatars and the
    other person in the room. Those false positives are scattered; the real
    facecam is a tight cluster because it never moves. Anchoring on the median
    and rejecting anything more than a quarter-frame away from it is enough —
    and it is what stops the camera teleporting mid-clip.
    """
    if not samples:
        return [], {"cx": src_w / 2.0, "cy": src_h * 0.45,
                    "w": src_w * 0.11, "n": 0}

    mx, my = _median([s[1] for s in samples]), _median([s[2] for s in samples])
    tol_x, tol_y = src_w * 0.16, src_h * 0.22
    kept = [s for s in samples if abs(s[1] - mx) <= tol_x and abs(s[2] - my) <= tol_y]
    if len(kept) < max(3, len(samples) // 6):   # cluster too thin to trust
        kept = list(samples)

    return kept, {
        "cx": _median([s[1] for s in kept], mx),
        "cy": _median([s[2] for s in kept], my),
        "w": max(40.0, _median([s[3] for s in kept], src_w * 0.11)),
        "n": float(len(kept)),
    }


def _centre_at(samples: Sequence[tuple[float, float, float, float]],
               t0: float, t1: float,
               fallback: tuple[float, float]) -> tuple[float, float]:
    """Median subject centre over [t0, t1], widening the search if it is empty.

    A shot with no detection must not snap the camera to the frame centre —
    that reads as a glitch, not a cut — so it inherits the nearest sample.
    """
    if not samples:
        return fallback
    inside = [(cx, cy) for t, cx, cy, _ in samples if t0 <= t <= t1]
    if not inside:
        times = [s[0] for s in samples]
        i = min(len(samples) - 1, bisect_left(times, (t0 + t1) / 2.0))
        inside = [(samples[i][1], samples[i][2])]
    return (_median([c[0] for c in inside]), _median([c[1] for c in inside]))


# ---------------------------------------------------------------------------
# the cameras
# ---------------------------------------------------------------------------

def _rect(w: float, h: float, cx: float, cy: float, headroom: float,
          src_w: int, src_h: int) -> dict[str, int]:
    """An even 9:16 rect of the given height, anchored on (cx, cy)."""
    h = _even(min(src_h, max(180, h)))
    w = _even(min(src_w, max(100, h * ASPECT)))
    if w > src_w:                        # source narrower than 9:16
        w = _even(src_w)
        h = _even(min(src_h, w / ASPECT))
    x = _even(min(max(cx - w / 2.0, 0), src_w - w))
    y = _even(min(max(cy - h * headroom, 0), src_h - h))
    return {"x": x, "y": y, "w": w, "h": h}


def camera_rects(face: dict[str, float], style: dict,
                 src_w: int, src_h: int) -> dict[str, dict[str, int]]:
    """The source rectangle for every camera, given where the facecam is.

    The gameplay cameras are pushed clear of BOTH the facecam and the chat
    strip: a "gameplay" shot that still contains the streamer's head is not a
    second camera, it is the first one with extra clutter.
    """
    out: dict[str, dict[str, int]] = {}
    fw = float(face.get("w") or src_w * 0.11)
    fcx, fcy = float(face["cx"]), float(face["cy"])

    for name in _FACE_CAMS:
        mult, headroom = CAMERAS[name]
        out[name] = _rect(0, fw * mult, fcx, fcy, headroom, src_w, src_h)

    chat = src_w * (1.0 - _f(style.get("chat_margin_pct"), 0.09))
    # Everything to the right of the facecam, minus the chat strip.
    band_lo = min(src_w * 0.75, fcx + fw * 1.2)
    band_hi = max(band_lo + src_w * 0.12, chat)
    action_cx = (band_lo + band_hi) / 2.0

    action_cy = src_h * _f(style.get("game_centre_y_pct"), 0.47)
    out["game"] = _rect(0, src_h * _f(style.get("game_height_pct"), 0.86),
                        action_cx, action_cy, 0.5, src_w, src_h)
    out["game_tight"] = _rect(0, src_h * _f(style.get("game_zoom"), 0.64),
                              action_cx, action_cy, 0.5, src_w, src_h)
    return out


# Fallback slice of the frame that "is something happening in the game" is
# measured over, as fractions of width/height: (x0, x1, y0, y1). Used only when
# no facecam is found — otherwise the band is derived per clip by action_band().
#
# This constant used to be the only option, and it encoded ONE stream's layout
# (facecam bottom-left, chat right). A second stream in the same test set puts
# its facecam top-right, where this band would have framed the facecam itself as
# "the gameplay" — the exact thing the band exists to avoid.
ACTION_BAND = (0.34, 0.86, 0.04, 0.94)

# Keep this much clear of the frame edges regardless: the stream's own HUD (top
# bar, hotbar, subscriber counters) lives there and is not gameplay.
_EDGE_INSET = 0.04


_CLUSTER_TOL = 0.12      # centres this close, as a frame fraction, are one cam
_MIN_CLUSTER = 3         # detections below this are noise, not a facecam
_MIN_PERSISTENCE = 0.35  # share of the window a real facecam must appear across


def face_clusters(faces: list[dict], proxy_w: int, proxy_h: int
                  ) -> list[tuple[float, float, float, float]]:
    """One padded bounding box per facecam, in frame fractions.

    A co-stream has TWO facecams — the observed stream puts one top-left and one
    top-right — and taking the median of every detection lands between them, on
    the gameplay. So detections are grouped by centre first.

    Grouping alone is not enough, and the failure is instructive: with a better
    detector the second cluster came back at the MIDDLE of the frame, a Minecraft
    texture the cascade liked, and the band shrank to the bottom half to avoid a
    face that was never there. What separates a facecam from a mob is not how
    tightly it clusters but that IT IS ALWAYS THERE — an inset persists for the
    whole clip, a texture flashes past. Clusters must therefore span a real share
    of the window, not merely collect a few hits.
    """
    if proxy_w <= 0 or proxy_h <= 0:
        return []
    samples = [(i, b) for i, s in enumerate(faces)
               for b in (s.get("boxes") or [])
               if isinstance(b, (list, tuple)) and len(b) >= 4]
    if not samples or not faces:
        return []

    groups: list[list[tuple[int, float, float, float, float]]] = []
    for i, b in samples:
        x0, y0 = float(b[0]) / proxy_w, float(b[1]) / proxy_h
        x1, y1 = x0 + float(b[2]) / proxy_w, y0 + float(b[3]) / proxy_h
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for g in groups:
            gx = sum((a[1] + a[2]) / 2 for a in g) / len(g)
            gy = sum((a[3] + a[4]) / 2 for a in g) / len(g)
            if abs(cx - gx) <= _CLUSTER_TOL and abs(cy - gy) <= _CLUSTER_TOL:
                g.append((i, x0, x1, y0, y1))
                break
        else:
            groups.append([(i, x0, x1, y0, y1)])

    def med(vals: list[float]) -> float:
        vals = sorted(vals)
        return vals[len(vals) // 2]

    pad = 0.02
    out = []
    for g in groups:
        if len(g) < _MIN_CLUSTER:
            continue
        span = (max(a[0] for a in g) - min(a[0] for a in g) + 1) / len(faces)
        if span < _MIN_PERSISTENCE:
            continue
        out.append((med([a[1] for a in g]) - pad, med([a[2] for a in g]) + pad,
                    med([a[3] for a in g]) - pad, med([a[4] for a in g]) + pad))
    return out


def action_band(faces: list[dict], proxy_w: int, proxy_h: int
                ) -> tuple[float, float, float, float]:
    """The largest edge-aligned band that excludes EVERY detected facecam.

    Cut lines are taken from each cluster's own edges, so the search covers
    "left of both", "between them", "below both" and so on rather than assuming
    a single inset. With one facecam this reduces to the old behaviour; with two
    it is the difference between a gameplay camera and one framing a co-streamer.

    Falls back to ACTION_BAND when nothing was detected — a band derived from a
    phantom face would be worse than a wrong constant.
    """
    clusters = face_clusters(faces, proxy_w, proxy_h)
    lo, hi = _EDGE_INSET, 1.0 - _EDGE_INSET
    if not clusters:
        return ACTION_BAND

    xs = sorted({lo, hi} | {v for c in clusters for v in (c[0], c[1])})
    ys = sorted({lo, hi} | {v for c in clusters for v in (c[2], c[3])})

    best, best_area = None, 0.0
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1:]:
            for j, y0 in enumerate(ys):
                for y1 in ys[j + 1:]:
                    x0c, x1c = max(x0, lo), min(x1, hi)
                    y0c, y1c = max(y0, lo), min(y1, hi)
                    area = (x1c - x0c) * (y1c - y0c)
                    if area <= best_area:
                        continue
                    if any(cx0 < x1c and cx1 > x0c and cy0 < y1c and cy1 > y0c
                           for cx0, cx1, cy0, cy1 in clusters):
                        continue
                    best, best_area = (x0c, x1c, y0c, y1c), area

    if best is None or (best[1] - best[0]) < 0.25 or (best[3] - best[2]) < 0.25:
        return ACTION_BAND       # facecams dominate the frame; trust nothing
    return best
