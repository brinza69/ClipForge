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

__all__ = ["ASPECT", "CAMERAS", "DEFAULT_STYLE", "camera_rects"]

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
    "game_height_pct": 0.86,     # skips the stream's own top HUD and bottom bar
    "game_zoom": 0.64,           # game_tight height as a fraction of the frame
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
