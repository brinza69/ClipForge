"""
ClipForge — AI Stream Clipper: the dynamic multi-cam edit planner.

Turns one candidate window into an EDIT DECISION LIST: a run of short shots,
each pointing a different "camera" at the SAME source frame.

The grammar comes from measuring the reference edits (see
`docs/dynamic-edit-recipe.md`). The load-bearing finding is that those edits are
not one framing with effects sprinkled on top — they are a two-camera switch cut
roughly every second, where the cut itself IS the effect. On a stream VOD the
two cameras are already in the frame: the facecam and the gameplay. So the
planner's real job is deciding, second by second, WHICH ONE the viewer needs —
the reaction or the action.

That is the thing `layout.py` deliberately does not do. `layout.plan_layout`
picks ONE framing for a whole clip and `layout.smooth_keyframes` then damps
every movement out of it; correct for a clean export, and exactly wrong here.

Nothing in this module touches ffmpeg or the disk. It consumes the analysis
artefacts the pipeline already wrote plus a face track, and emits a plan that
`dynamic_render.py` turns into a single filtergraph.

Coordinate spaces, the same trap as layout.py: face boxes arrive in PROXY
pixels; every rect emitted here is in SOURCE pixels.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left
from typing import Any, Sequence

logger = logging.getLogger("clipforge.clipper.dynamic_edit")

__all__ = ["DEFAULT_STYLE", "CAMERAS", "camera_rects", "plan_dynamic_edit"]

ASPECT = 9.0 / 16.0

# Each camera is (height as a multiple of the detected face width, headroom).
# Headroom is where the face sits inside the crop as a fraction of crop height
# from the top: tighter shots ride higher because the eyes, not the chin, are
# what the viewer tracks. The GAME cameras ignore both and frame the action.
CAMERAS: dict[str, tuple[float, float]] = {
    "face":       (3.8, 0.44),
    "face_tight": (2.5, 0.41),
    "game":       (0.0, 0.50),
    "game_tight": (0.0, 0.50),
}
_FACE_CAMS = ("face", "face_tight")
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
}

_EMOTION = re.compile(
    r"\b(oh|damn|bro|wtf|omg|yo|what|why|how|no|yes|stop|wait|look|watch|"
    r"crazy|insane|god|hell|shit|fuck|dead|lol|haha|ahh|aye|man|bruh)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*$")
_EMPHATIC = re.compile(r"[!?]")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out


def _even(value: float) -> int:
    return int(value) // 2 * 2


def _pct(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, int(q * (len(sorted_values) - 1))))
    return float(sorted_values[idx])


def _norm(value: float, lo: float, hi: float) -> float:
    if hi - lo <= 1e-9:
        return 0.5
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))


def _series_mean(values: Sequence[float], hop: float, t0: float, t1: float) -> float:
    if not values or hop <= 0 or t1 <= t0:
        return 0.0
    i0 = max(0, int(t0 / hop))
    i1 = min(len(values), max(i0 + 1, int(t1 / hop)))
    window = values[i0:i1]
    return sum(window) / float(len(window)) if window else 0.0


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


# ---------------------------------------------------------------------------
# where to cut
# ---------------------------------------------------------------------------

def _boundaries(words: Sequence[dict], peaks: Sequence[float],
                scenes: Sequence[float], clip_start: float, duration: float,
                style: dict) -> list[tuple[float, float]]:
    """Candidate cut points as [(t, weight)], clip-relative.

    Weight is "how natural a cut here would sound". Speech pauses win, because
    a cut inside a word is the one artefact a viewer always notices; a source
    scene cut is close behind, since the source already cut there.
    """
    gap_min = _f(style.get("pause_gap_s"), 0.14)
    out: list[tuple[float, float]] = []

    prev_end: float | None = None
    prev_text = ""
    for w in words or []:
        ws = _f(w.get("start")) - clip_start
        we = _f(w.get("end"), _f(w.get("start"))) - clip_start
        if prev_end is not None and 0.0 < ws < duration:
            gap = ws - prev_end
            if gap >= gap_min:
                weight = 1.0 + min(1.0, gap / 0.7)
                if _SENTENCE_END.search(prev_text.strip()):
                    weight += 0.8
                out.append((max(0.0, (prev_end + ws) / 2.0), weight))
            elif _SENTENCE_END.search(prev_text.strip()):
                out.append((max(0.0, prev_end), 1.2))
        prev_end, prev_text = we, str(w.get("word") or "")

    for t in scenes or []:
        rel = _f(t) - clip_start
        if 0.0 < rel < duration:
            out.append((rel, 1.6))
    for t in peaks or []:
        rel = _f(t) - clip_start
        if 0.0 < rel < duration:
            out.append((rel, 0.7))

    out.sort(key=lambda b: b[0])
    return out


def _cut_times(boundaries: Sequence[tuple[float, float]], duration: float,
               style: dict) -> list[float]:
    """Greedily walk the clip, taking the best-sounding boundary in range.

    Falls back to a hard cut at the target length when a stretch offers nothing:
    a six-second unbroken shot breaks the style far more visibly than a cut
    landing mid-phrase.
    """
    lo = max(0.2, _f(style.get("min_shot_s"), 0.60))
    target = max(lo, _f(style.get("target_shot_s"), 1.25))
    hi = max(target, _f(style.get("max_shot_s"), 2.40))

    cuts: list[float] = []
    t = 0.0
    while duration - t > hi:
        window = [(bt, bw) for bt, bw in boundaries if t + lo <= bt <= t + hi]
        if window:
            # Prefer a strong boundary, tie-breaking toward the target length so
            # the cadence stays even instead of clumping at one end.
            nxt = max(window, key=lambda b: (b[1], -abs(b[0] - (t + target))))[0]
        else:
            nxt = t + target
        cuts.append(round(max(nxt, t + lo), 3))
        t = cuts[-1]

    if cuts and duration - cuts[-1] < lo:      # absorb a runt tail
        cuts.pop()
    return cuts


# ---------------------------------------------------------------------------
# what each shot looks like
# ---------------------------------------------------------------------------

def _speech_ratio(words: Sequence[dict], clip_start: float,
                  t0: float, t1: float) -> tuple[float, str]:
    """(fraction of the shot covered by speech, the words spoken in it)."""
    span = max(1e-6, t1 - t0)
    covered = 0.0
    said: list[str] = []
    for w in words or []:
        ws = _f(w.get("start")) - clip_start
        we = _f(w.get("end"), ws) - clip_start
        overlap = min(we, t1) - max(ws, t0)
        if overlap > 0:
            covered += overlap
            said.append(str(w.get("word") or ""))
    return min(1.0, covered / span), " ".join(said).strip()


def _pick_camera(speaking: bool, action: bool, alive: bool, energy: float,
                 previous: str | None, run: int, max_run: int) -> str:
    """Point the camera at whatever the viewer needs, then force a change.

    Two rules, both measured off the references. First, adjacent shots must
    never share a rectangle — a cut you cannot see is a stutter, not an edit.
    Second, and more important, the edit must keep CROSSING BETWEEN SUBJECTS:
    the reference cuts away to the other person while the first is still
    talking. On a stream VOD that is the cutaway to the game, so a face run
    longer than `max_run` is broken whatever the signals say.
    """
    if speaking and not action:
        family = _FACE_CAMS
    elif action and not speaking:
        family = _GAME_CAMS
    elif speaking and action:
        family = _FACE_CAMS if energy >= 0.5 else _GAME_CAMS
    else:
        family = _GAME_CAMS if energy < 0.35 else _FACE_CAMS

    held = _FACE_CAMS if previous in _FACE_CAMS else (
        _GAME_CAMS if previous in _GAME_CAMS else None)
    if held is not None and family is held and run >= max_run:
        family = _GAME_CAMS if held is _FACE_CAMS else _FACE_CAMS

    # A dead gameplay region beats the alternation rule: a cutaway to a black
    # loading screen is worse than holding on the face one shot too long.
    if family is _GAME_CAMS and not alive:
        family = _FACE_CAMS

    want = family[1] if energy >= 0.55 else family[0]
    if want != previous:
        return want
    return family[0] if want == family[1] else family[1]


def _hits(peaks: Sequence[float], rms: Sequence[float], hop: float,
          clip_start: float, duration: float, style: dict) -> list[float]:
    """Clip-relative times of the loudest onsets, thinned to a minimum spacing."""
    hop = hop if hop > 0 else 0.25
    inside = [t for t in ((_f(p) - clip_start) for p in peaks or [])
              if 0.15 < t < duration - 0.15]
    if not inside:
        return []

    def loudness(t: float) -> float:
        i = int((t + clip_start) / hop)
        return float(rms[i]) if 0 <= i < len(rms) else 0.0

    floor = _pct(sorted(loudness(t) for t in inside),
                 _f(style.get("hit_peak_pct"), 0.45))
    ranked = sorted((t for t in inside if loudness(t) >= floor),
                    key=lambda t: -loudness(t))

    gap = _f(style.get("hit_min_gap_s"), 0.60)
    kept: list[float] = []
    for t in ranked:
        if len(kept) >= int(style.get("max_hits") or 16):
            break
        if all(abs(t - k) >= gap for k in kept):
            kept.append(t)
    return sorted(round(t, 3) for t in kept)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def plan_dynamic_edit(cand: dict, signals: dict, face_track: Sequence[dict],
                      *, src_w: int, src_h: int,
                      proxy_w: int = 0, proxy_h: int = 0,
                      game_motion: Sequence[float] | None = None,
                      game_focus: Sequence[float] | None = None,
                      game_motion_hop: float = 0.25,
                      style: dict | None = None) -> dict:
    """Plan the shot list for one candidate.

    `face_track` is `[{"t": absolute seconds, "boxes": [[x, y, w, h], ...]}]` in
    PROXY pixels — the shape `signals.face_presence` returns, so a dense
    per-clip pass and the coarse whole-VOD track are interchangeable here.

    `game_motion` is optional per-hop motion measured INSIDE the gameplay
    region. Without it the planner falls back to the whole-frame motion signal,
    which is noisier because the facecam moves too. `game_focus` is the matching
    per-hop x centre (SOURCE pixels) of whatever moved, `-1` for "nothing did";
    it is what lets the gameplay camera follow the action instead of staring at
    a fixed rectangle.

    Returns `{duration, shots, hits, cameras, style, subject, warnings}` with
    every time CLIP-RELATIVE (the renderer seeks with -ss before -i) and every
    rect in SOURCE pixels.
    """
    cand = cand if isinstance(cand, dict) else {}
    signals = signals if isinstance(signals, dict) else {}
    merged = dict(DEFAULT_STYLE)
    merged.update(style or {})
    warnings: list[str] = []

    src_w, src_h = int(src_w), int(src_h)
    clip_start = _f(cand.get("start"))
    duration = max(0.5, _f(cand.get("end")) - clip_start)

    audio = signals.get("audio") if isinstance(signals.get("audio"), dict) else {}
    rms: Sequence[float] = audio.get("rms") or []
    hop = _f(audio.get("hop_s"), 0.25) or 0.25

    if game_motion:
        motion, motion_hop, motion_base = list(game_motion), max(0.05, game_motion_hop), 0.0
    else:
        block = signals.get("motion") if isinstance(signals.get("motion"), dict) else {}
        motion = block.get("motion") or []
        motion_hop = _f(block.get("hop_s"), 0.5) or 0.5
        motion_base = clip_start
        warnings.append(
            "No gameplay-region motion supplied; falling back to whole-frame motion, "
            "which the facecam also moves.")

    pw = int(proxy_w or _f(signals.get("proxy_width"), 0)) or src_w
    ph = int(proxy_h or _f(signals.get("proxy_height"), 0)) or src_h
    samples, face = _dominant(
        _face_samples(face_track, src_w / float(pw), src_h / float(ph), clip_start),
        src_w, src_h)
    if not samples:
        warnings.append(
            "No face detected in this window; the facecam position is a guess.")
    cams = camera_rects(face, merged, src_w, src_h)
    fallback = (face["cx"], face["cy"])

    words = cand.get("words") or []
    cuts = _cut_times(
        _boundaries(words, audio.get("peaks") or [], signals.get("scenes") or [],
                    clip_start, duration, merged),
        duration, merged)
    spans = list(zip([0.0, *cuts], [*cuts, round(duration, 3)]))

    # Normalise against THIS clip, not the whole VOD: a quiet clip must still
    # get its own loud moments, or it renders as one flat shot.
    energies = [_series_mean(rms, hop, clip_start + a, clip_start + b) for a, b in spans]
    motions = [_series_mean(motion, motion_hop, motion_base + a, motion_base + b)
               for a, b in spans]
    e_sorted, m_sorted = sorted(energies), sorted(motions)
    e_lo, e_hi = _pct(e_sorted, 0.10), _pct(e_sorted, 0.90)
    m_lo, m_hi = _pct(m_sorted, 0.10), _pct(m_sorted, 0.90)
    shake_floor = _pct(e_sorted, _f(merged.get("shake_energy_pct"), 0.70))
    speech_on = _f(merged.get("speech_ratio_on"), 0.30)
    action_on = _f(merged.get("action_pct"), 0.60)
    # The dead-region check only runs on the measured gameplay band. The
    # whole-frame fallback is on an uncalibrated scale, so applying an absolute
    # floor to it would mute the second camera for the whole clip.
    dead_below = _f(merged.get("game_dead_below"), 0.35) if game_motion else -1.0
    push_min = _f(merged.get("push_min_shot_s"), 0.95)

    max_run = max(1, int(merged.get("max_same_family") or 2))
    shots: list[dict] = []
    previous: str | None = None
    run = 0
    for i, (t0, t1) in enumerate(spans):
        if t1 - t0 <= 0.05:
            continue
        energy = _norm(energies[i], e_lo, e_hi)
        action = _norm(motions[i], m_lo, m_hi)
        ratio, text = _speech_ratio(words, clip_start, t0, t1)
        if _EMPHATIC.search(text) or _EMOTION.search(text):
            energy = min(1.0, energy + 0.18)

        camera = _pick_camera(ratio >= speech_on, action >= action_on,
                              motions[i] > dead_below, energy, previous, run, max_run)
        if i == 0:
            # Open on a face: the hook of every reference is a person, not a room.
            camera = "face_tight" if energy >= 0.5 else "face"
        same_family = (previous in _FACE_CAMS and camera in _FACE_CAMS) or \
                      (previous in _GAME_CAMS and camera in _GAME_CAMS)
        run = run + 1 if same_family else 1
        previous = camera

        rect = dict(cams[camera])
        if camera in _FACE_CAMS:
            cx, cy = _centre_at(samples, t0, t1, fallback)
            rect = _rect(0, rect["h"], cx, cy, CAMERAS[camera][1], src_w, src_h)
        else:
            # Point the second camera at whatever actually moved in this shot,
            # falling back to the static action centre when nothing did.
            hot = [v for v in (game_focus or [])[int(t0 / motion_hop):
                                                max(1, int(t1 / motion_hop))]
                   if v is not None and float(v) >= 0.0]
            if hot:
                rect = _rect(0, rect["h"], _median(hot), rect["y"] + rect["h"] / 2.0,
                             0.5, src_w, src_h)

        long_enough = (t1 - t0) >= push_min
        shots.append({
            "index": len(shots),
            "t0": round(t0, 3),
            "t1": round(t1, 3),
            "camera": camera,
            "rect": rect,
            "anchor": [rect["x"] + rect["w"] // 2, rect["y"] + rect["h"] // 2],
            "move": ("push" if energy >= 0.45 else "pull") if long_enough else "hold",
            "snap": i > 0 and energy >= 0.50,
            "shake": round(_f(merged.get("shake_px"), 6.0) * min(1.0, 0.4 + energy), 2)
                     if energies[i] >= shake_floor else 0.0,
            "energy": round(energy, 3),
            "action": round(action, 3),
            "speech": round(ratio, 3),
            "text": text[:120],
        })

    if not shots:
        warnings.append("The window was too short to cut; rendering it as one shot.")
        rect = dict(cams["face"])
        shots = [{
            "index": 0, "t0": 0.0, "t1": round(duration, 3), "camera": "face",
            "rect": rect,
            "anchor": [rect["x"] + rect["w"] // 2, rect["y"] + rect["h"] // 2],
            "move": "push", "snap": False, "shake": 0.0,
            "energy": 0.5, "action": 0.5, "speech": 0.0, "text": "",
        }]

    return {
        "duration": round(duration, 3),
        "shots": shots,
        "hits": _hits(audio.get("peaks") or [], rms, hop, clip_start, duration, merged),
        "cameras": cams,
        "style": merged,
        "subject": {"samples": len(samples), "face": face},
        "warnings": warnings,
    }
