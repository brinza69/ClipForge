"""
ClipForge — AI Stream Clipper: the pure half of content classification.

Split out of content_type.py to keep both files under the repo's 500-line
limit, and because the split is a real seam rather than an arbitrary cut: NONE
of this needs opencv or a decoded image. It is rectangle maths, transcript
vocabulary scoring, signal summarisation and the classifier itself — all
deterministic functions over numbers, which is what makes the decision logic
unit-testable without decoding a single frame.

content_type.py re-exports these, so an existing
"from services.clipper.content_type import classify_features" keeps working.
"""

from __future__ import annotations

import logging
import math
import unicodedata
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from services.clipper.ffmpeg_tools import even

logger = logging.getLogger("clipforge.clipper.content_type")

CONTENT_TYPES = (
    "gaming", "podcast", "interview", "irl", "commentary",
    "talking_head", "tutorial", "sports", "low_dialogue", "unknown",
)

_MAX_FRAMES = 40            # enough to judge stability; more only costs time
_WORK_WIDTH = 480           # frames already come from the 480p analysis proxy
_EDGE_LO, _EDGE_HI = 80, 200
_CORNER_FRAC = 0.22         # side of the four corner patches, as a frame fraction
_WEBCAM_ASPECT = (1.1, 1.9)
_WEBCAM_AREA = (0.015, 0.30)
_TRACK_IOU = 0.7            # same box across two frames
_CHAT_ASPECT_MAX = 0.6
_HUD_MAX = 6
_GRID_COLS, _GRID_ROWS = 8, 6

# Short, high-precision vocabularies (EN + RO). Deliberately not exhaustive:
# these only nudge a decision that the visual signals already lean towards, so
# recall matters far less than not firing on ordinary speech.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "gaming": ("kill", "respawn", "lobby", "loot", "boss", "clutch", "spawn",
               "headshot", "runda", "adversar", "harta", "nivel"),
    "tutorial": ("step", "click", "tutorial", "install", "settings menu",
                 "pasul", "apasa", "setari", "instalezi"),
    "interview": ("question", "you said", "tell me", "asked you",
                  "intrebare", "spuneai", "povesteste", "te-am intrebat"),
    "sports": ("goal", "referee", "penalty", "halftime", "final score",
               "gol", "meci", "arbitru", "repriza"),
    "commentary": ("i think", "honestly", "my take", "in my opinion",
                   "parerea mea", "sincer", "cred ca"),
    "podcast": ("welcome back", "episode", "our guest", "sponsor",
                "episod", "invitat", "bine ati venit"),
}

_CV: Any = None
_CV_TRIED = False
_CASCADE: Any = None
_CASCADE_TRIED = False


# --------------------------------------------------------------------------
# pure geometry / numeric helpers
# --------------------------------------------------------------------------

def clamp01(value: float) -> float:
    """Clamp to [0,1]; NaN becomes 0.0 so one bad sample can't poison a mean."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v:
        return 0.0
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def make_rect(x: float, y: float, w: float, h: float) -> dict[str, int]:
    """A rect in the sampled frames' pixel coords."""
    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}


def rect_iou(a: dict | None, b: dict | None) -> float:
    """Intersection over union of two rects. 0.0 if either is missing."""
    if not a or not b:
        return 0.0
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    iw = max(0, min(ax2, bx2) - max(a["x"], b["x"]))
    ih = max(0, min(ay2, by2) - max(a["y"], b["y"]))
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def rect_overlap_frac(a: dict | None, b: dict | None) -> float:
    """How much of `a` is covered by `b` (0..1) — asymmetric, unlike IoU."""
    if not a or not b or a["w"] <= 0 or a["h"] <= 0:
        return 0.0
    iw = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    ih = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return (iw * ih) / float(a["w"] * a["h"])


def rect_aspect(rect: dict | None) -> float:
    """w/h. 0.0 for a degenerate rect rather than a ZeroDivisionError."""
    if not rect or rect.get("h", 0) <= 0:
        return 0.0
    return float(rect["w"]) / float(rect["h"])


def aspect_ok(rect: dict | None, lo: float, hi: float) -> bool:
    a = rect_aspect(rect)
    return a > 0.0 and lo <= a <= hi


def rect_area_frac(rect: dict | None, fw: int, fh: int) -> float:
    if not rect or fw <= 0 or fh <= 0:
        return 0.0
    return clamp01((rect["w"] * rect["h"]) / float(fw * fh))


def rect_centre(rect: dict) -> tuple[float, float]:
    return rect["x"] + rect["w"] / 2.0, rect["y"] + rect["h"] / 2.0


def corner_proximity(rect: dict | None, fw: int, fh: int) -> float:
    """1.0 when the rect hugs a frame corner, 0.0 when it sits dead centre.

    Streamers park facecams in corners, so this breaks ties between otherwise
    equally plausible rectangles.
    """
    if not rect or fw <= 0 or fh <= 0:
        return 0.0
    cx, cy = rect_centre(rect)
    half_diag = math.hypot(fw, fh) / 2.0
    if half_diag <= 0:
        return 0.0
    best = min(math.hypot(cx - x, cy - y)
               for x in (0.0, float(fw)) for y in (0.0, float(fh)))
    return clamp01(1.0 - best / half_diag)


def track_stability(rects: Sequence[dict], min_iou: float = _TRACK_IOU) -> float:
    """Fraction of consecutive detections that stayed put (IoU > min_iou)."""
    if len(rects) < 2:
        return 1.0 if rects else 0.0
    hits = sum(1 for a, b in zip(rects, rects[1:]) if rect_iou(a, b) > min_iou)
    return hits / float(len(rects) - 1)


def median_rect(rects: Sequence[dict]) -> dict[str, int] | None:
    """Per-field median — robust to the one frame where a contour blew up."""
    if not rects:
        return None
    return make_rect(*(float(np.median([r[k] for r in rects]))
                       for k in ("x", "y", "w", "h")))


def snap_rect(rect: dict | None, fw: int, fh: int) -> dict[str, int] | None:
    """Clamp into frame and force even dimensions (H.264 refuses odd crops)."""
    if not rect or fw <= 0 or fh <= 0:
        return None
    x = max(0, min(even(rect["x"]), max(0, even(fw) - 2)))
    y = max(0, min(even(rect["y"]), max(0, even(fh) - 2)))
    w = max(2, min(even(rect["w"]), even(fw - x)))
    h = max(2, min(even(rect["h"]), even(fh - y)))
    return make_rect(x, y, w, h)


def build_tracks(per_frame: Sequence[Sequence[dict]],
                 min_iou: float = _TRACK_IOU) -> list[list[dict]]:
    """Greedily chain per-frame rects into tracks by IoU with the last member."""
    tracks: list[list[dict]] = []
    for frame_rects in per_frame:
        for rect in frame_rects:
            match = max(tracks, key=lambda t: rect_iou(t[-1], rect), default=None)
            if match is not None and rect_iou(match[-1], rect) > min_iou:
                match.append(rect)
            else:
                tracks.append([rect])
    return tracks


# --------------------------------------------------------------------------
# pure signal / transcript summaries
# --------------------------------------------------------------------------

def _fold(text: str) -> str:
    """Lowercase and strip diacritics so 'întrebare' matches 'intrebare'."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def transcript_text(transcript: Any) -> str:
    """Best-effort plain text from whatever transcript shape we're handed."""
    if isinstance(transcript, str):
        return transcript
    if not isinstance(transcript, dict):
        return ""
    full = transcript.get("full_text") or transcript.get("text")
    if isinstance(full, str) and full.strip():
        return full
    parts = []
    for seg in transcript.get("segments") or transcript.get("words") or []:
        if isinstance(seg, dict):
            parts.append(str(seg.get("text") or seg.get("word") or ""))
        elif isinstance(seg, str):
            parts.append(seg)
    return " ".join(p for p in parts if p)


def keyword_score(text: str, words: Sequence[str]) -> float:
    """0..1 saturating hit rate, normalised by length so a 40-word clip with
    one match doesn't outscore a 10-minute one with eight."""
    folded = _fold(text)
    if not folded.strip():
        return 0.0
    total = max(1, len(folded.split()))
    hits = sum(folded.count(_fold(w)) for w in words)
    return clamp01(hits / max(2.0, total / 120.0) / 2.0)


def keyword_scores(text: str) -> dict[str, float]:
    return {f"kw_{name}": keyword_score(text, words)
            for name, words in _KEYWORDS.items()}


def _series(value: Any, key: str) -> list[float]:
    """Pull a numeric list out of either {key: [..]} or a bare list."""
    if isinstance(value, dict):
        value = value.get(key) or value.get("values") or []
    out: list[float] = []
    for item in value or []:
        if isinstance(item, dict):
            item = item.get("v", item.get("value", item.get("motion")))
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def _spans(value: Any, key: str) -> list[tuple[float, float]]:
    if isinstance(value, dict):
        value = value.get(key) or []
    out: list[tuple[float, float]] = []
    for item in value or []:
        if isinstance(item, dict):
            item = (item.get("start"), item.get("end"))
        try:
            s, e = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if e > s:
            out.append((s, e))
    return out


def summarize_motion(signals: dict | None) -> dict[str, float]:
    """{'motion_mean','motion_spikiness'} — gaming/sports run high and spiky,
    a seated podcast runs low and flat."""
    values = _series((signals or {}).get("motion"), "motion")
    if not values:
        return {"motion_mean": 0.0, "motion_spikiness": 0.0}
    arr = np.asarray(values, dtype=float)
    mean = float(np.mean(arr))
    # Coefficient of variation, not raw std: a flat-but-loud scene and a flat
    # quiet one should both read as "not spiky".
    spikiness = float(np.std(arr)) / mean if mean > 1e-6 else 0.0
    return {"motion_mean": clamp01(mean), "motion_spikiness": clamp01(spikiness)}


def summarize_faces(signals: dict | None, fw: int = 0, fh: int = 0) -> dict[str, float]:
    """{'face_count','face_count_mean','face_stability','face_area'}.

    face_count is the MODAL per-frame count — one frame that briefly found
    three faces shouldn't turn a two-person interview into a crowd.
    """
    samples = (signals or {}).get("faces")
    if isinstance(samples, dict):
        samples = samples.get("faces") or samples.get("samples") or []
    counts: list[int] = []
    biggest = 0.0
    for sample in samples or []:
        boxes = sample.get("boxes") if isinstance(sample, dict) else sample
        valid = [b for b in (boxes or []) if isinstance(b, (list, tuple)) and len(b) >= 4]
        counts.append(len(valid))
        if fw > 0 and fh > 0:
            for b in valid:
                try:
                    biggest = max(biggest, (float(b[2]) * float(b[3])) / (fw * fh))
                except (TypeError, ValueError):
                    continue
    if not counts:
        return {"face_count": 0.0, "face_count_mean": 0.0,
                "face_stability": 0.0, "face_area": 0.0}
    modal = max(set(counts), key=counts.count)
    return {
        "face_count": float(modal),
        "face_count_mean": float(np.mean(counts)),
        "face_stability": counts.count(modal) / float(len(counts)),
        "face_area": clamp01(biggest),
    }


def speech_ratio(signals: dict | None) -> float:
    """Share of the timeline carrying speech. Accepts a ready-made float, or
    speech spans plus a duration to divide by.

    `speech_coverage` wins when it is there, and it is what the analyse stage
    now writes. The spans below come from the AUDIO ENVELOPE — anything above
    the silence floor — and on a stream carrying game audio, music and a voice
    at once, that is nearly everything. Measured across all eleven labelled
    sources it ran 0.863 to 1.000, a range of 0.137: it could not tell a
    Minecraft stream from an interview, which is the only thing the classifier
    wanted it for.

    From the transcript instead, the same eleven run 0.276 to 0.918 — 4.7x the
    spread — and they separate on the axis that matters: edited talking content
    sits at 0.86-0.92 and live streams at 0.28-0.64.

    The per-candidate path has done exactly this since session 3
    (`candidates._spoken_ratio`, which overwrites the envelope value with one
    derived from the words in the window). The classifier was simply never
    given the same fix.
    """
    signals = signals or {}
    coverage = signals.get("speech_coverage")
    if isinstance(coverage, (int, float)):
        return clamp01(float(coverage))
    raw = signals.get("speech")
    if isinstance(raw, (int, float)):
        return clamp01(float(raw))
    spans = _spans(raw, "speech")
    total = sum(e - s for s, e in spans)
    duration = 0.0
    try:
        duration = float(signals.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        ends = [e for _, e in spans] + [e for _, e in _spans(signals.get("silence"), "silence")]
        duration = max(ends) if ends else 0.0
    return clamp01(total / duration) if duration > 0 else 0.0


# --------------------------------------------------------------------------
# the classifier (pure)
# --------------------------------------------------------------------------

def classify_features(features: dict[str, Any]) -> dict[str, Any]:
    """Numeric feature summary -> {'content_type','confidence','evidence'}.

    Rule-based on purpose: with no labelled corpus a learned model would just be
    an unauditable version of these same thresholds, and the UI has to show a
    human why it guessed what it guessed.
    """
    def g(key: str, default: float = 0.0) -> float:
        try:
            return float(features.get(key, default))
        except (TypeError, ValueError):
            return default

    frames = int(g("frame_count"))
    edges_n = clamp01(g("edge_density") / 0.14)
    lines = clamp01(g("straight_line_ratio"))
    sat_n = clamp01(g("saturation") / 0.45)
    corner = clamp01(g("corner_stability"))
    centre = clamp01(g("centre_motion"))
    motion, spiky = clamp01(g("motion_mean")), clamp01(g("motion_spikiness"))
    faces, face_mean = g("face_count"), g("face_count_mean")
    face_stab, face_area = clamp01(g("face_stability")), clamp01(g("face_area"))
    speech = clamp01(g("speech_ratio"))

    scores = {t: 0.0 for t in CONTENT_TYPES}
    evidence: dict[str, list[str]] = {t: [] for t in CONTENT_TYPES}

    def vote(kind: str, weight: float, why: str = "") -> None:
        if weight <= 0.01:
            return
        scores[kind] += weight
        if why and why not in evidence[kind]:
            evidence[kind].append(why)

    # Synthetic game art: saturated, edge-dense, full of long straight runs.
    synthetic = clamp01(0.5 * edges_n + 0.3 * lines + 0.2 * sat_n)
    vote("gaming", 1.6 * synthetic,
         f"synthetic-looking frames ({g('edge_density'):.1%} edge pixels, "
         f"{g('saturation'):.0%} saturation)")
    # There used to be a second gaming vote here, weight 1.8, on
    # `corner_stability * centre_motion` — "static border + moving middle = a
    # HUD". It contributed exactly 0.0 for its entire life, because
    # corner_stability was pinned at 0 by an absolute threshold (see
    # content_type._patch_motion). Un-pinning those features made it fire for
    # the first time, and measured on five real sources it points the WRONG
    # WAY: the highest score is a Just Chatting stream (0.397) and the gaming
    # stream is near the bottom (0.044), because a locked-off webcam has a
    # static border while a stream that switches between Fortnite, Minecraft
    # and a Discord call does not. Gating it on `synthetic` does not rescue it
    # — Just Chatting still leads 0.230 to 0.034.
    #
    # So the signal is real and now measurable, but "static corners" means
    # "locked-off camera", not "game HUD". Removing the vote keeps today's
    # behaviour exactly (it was contributing nothing) instead of activating a
    # rule that is wrong. Deciding what it SHOULD vote for needs labelled
    # sources, which do not exist yet.
    vote("gaming", 1.0 * motion * spiky)
    vote("gaming", 2.0 * g("kw_gaming"), "gaming vocabulary in the transcript")
    if faces <= 1 and face_area < 0.10:
        vote("gaming", 0.4)

    # Sports is keyword-gated: broadcast footage looks like anything else.
    vote("sports", 2.2 * g("kw_sports") * (1.0 + motion), "sports vocabulary in the transcript")

    if faces == 1:
        vote("talking_head", 2.0 * face_stab * (0.5 + 0.5 * clamp01(face_area / 0.12)),
             f"1 stable face, {speech:.0%} speech")
        vote("talking_head", 1.0 * speech * (1.0 - motion))
    two_up = 1.0 if faces == 2 else (0.5 if faces == 3 else 0.0)
    if two_up:
        seated = 2.0 * two_up * face_stab
        vote("podcast", seated, f"{int(faces)} stable faces, {speech:.0%} speech")
        vote("interview", seated * 0.9, f"{int(faces)} stable faces, {speech:.0%} speech")
    vote("podcast", 1.2 * speech * (1.0 - motion) * (1.0 - spiky) + 1.6 * g("kw_podcast"),
         "podcast vocabulary in the transcript" if g("kw_podcast") > 0.1 else "")
    vote("interview", 2.0 * g("kw_interview") + 0.8 * speech,
         "question-and-answer phrasing" if g("kw_interview") > 0.1 else "")

    # Screen recordings: edge-dense but desaturated and near-static.
    screen_like = clamp01(edges_n * (1.0 - sat_n)) * corner * (1.0 - centre)
    vote("tutorial", 2.2 * g("kw_tutorial"), "step-by-step vocabulary in the transcript")
    vote("tutorial", 1.2 * screen_like * speech, "static screen-recording look")

    vote("commentary", 1.4 * speech * (1.0 - clamp01(face_mean / 2.0)) + 1.6 * g("kw_commentary"),
         f"{speech:.0%} speech with no steady on-camera face")

    vote("irl", 1.6 * (1.0 - corner) * motion + 0.8 * (1.0 - face_stab) * clamp01(face_mean),
         "hand-held look: nothing on screen stays put")

    if speech < 0.15:
        vote("low_dialogue", 2.2 * (1.0 - speech / 0.15),
             f"only {speech:.0%} of the timeline carries speech")

    # Floor so weak evidence lands on "unknown" instead of on a coin flip.
    scores["unknown"] = 0.45

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top <= 0.0:
        return {"content_type": "unknown", "confidence": 0.15,
                "evidence": ["no usable frames or signals"]}
    margin = (top - second) / top
    strength = min(1.0, top / 3.0)
    confidence = round(clamp01(0.15 + 0.5 * margin * strength + 0.35 * strength), 3)
    reasons = evidence[best][:4] or ["no single content type stood out"]
    return {"content_type": best, "confidence": confidence, "evidence": reasons}

