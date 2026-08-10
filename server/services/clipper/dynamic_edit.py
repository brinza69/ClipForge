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

from services.clipper.dynamic_cameras import (   # noqa: F401  (re-exported)
    ASPECT,
    CAMERAS,
    DEFAULT_STYLE,
    _centre_at,
    _dominant,
    _even,
    _f,
    _face_samples,
    _median,
    _rect,
    camera_rects,
)
from services.clipper.dynamic_cameras import _FACE_CAMS, _GAME_CAMS


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
    lo = max(0.2, _f(style.get("min_shot_s"), DEFAULT_STYLE["min_shot_s"]))
    target = max(lo, _f(style.get("target_shot_s"), DEFAULT_STYLE["target_shot_s"]))
    hi = max(target, _f(style.get("max_shot_s"), DEFAULT_STYLE["max_shot_s"]))

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


def _rung(family: Sequence[str], energy: float,
          thresholds: Sequence[float]) -> str:
    """Which rung of a family this shot's energy earns, widest first.

    Generic over family length on purpose: the face family has three rungs and
    the game family two, and hard-coding `family[0]` / `family[1]` is what made
    adding the middle face rung a rewrite rather than a constant.
    """
    step = sum(1 for t in thresholds if energy >= _f(t))
    return family[min(step, len(family) - 1)]


def _pick_camera(speaking: bool, action: bool, alive: bool, energy: float,
                 previous: str | None, run: int, max_run: int,
                 style: dict | None = None) -> str:
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

    style = style or {}
    key = "face_rung_energy" if family is _FACE_CAMS else "game_rung_energy"
    want = _rung(family, energy, style.get(key) or DEFAULT_STYLE[key])
    if want != previous:
        return want
    # Adjacent shots must differ. Step ONE rung rather than flipping to the far
    # end — with two rungs those were the same move, with three they are not,
    # and a forced cut should still read as a reframe.
    i = family.index(want)
    return family[i - 1] if i else family[1]


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
                      game_detail: Sequence[float] | None = None,
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
    a fixed rectangle. `game_detail` is the matching per-hop spatial standard
    deviation of the band — how much there is to look at, regardless of whether
    it moved. Without it the planner keeps the old motion-only guard.

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
    # Detail is the second half of that guard: motion answers "did anything
    # change", detail answers "is anything there". Measured, the pair only
    # reliably rejects a genuinely empty band — see game_flat_below, which
    # documents what it does NOT catch. Same reasoning as above: only applied
    # when the caller measured the band, never to the whole-frame fallback.
    details = ([_series_mean(list(game_detail), motion_hop,
                             motion_base + a, motion_base + b) for a, b in spans]
               if game_detail else [])
    flat_below = _f(merged.get("game_flat_below"), 8.0) if details else -1.0
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

        # "Alive" now means both: something moved AND there is something there.
        alive = motions[i] > dead_below and (
            not details or details[i] > flat_below)
        camera = _pick_camera(ratio >= speech_on, action >= action_on,
                              alive, energy, previous, run, max_run, merged)
        if i == 0:
            # Open on a face: the hook of every reference is a person, not a room.
            camera = _rung(_FACE_CAMS, energy, merged["face_rung_energy"])
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
