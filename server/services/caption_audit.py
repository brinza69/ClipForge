"""
Coverage audit for the tight caption eraser (anti-relic pass).

detect_caption_displays() can miss text: OCR skips a faint word on every
sample, a word shows only between two samples, etc. Every miss becomes a
readable relic in the output. This module verifies the detection against
two INDEPENDENT signals and patches the gaps:

  1. Transcript checklist: whisper transcribes the video's ORIGINAL
     language with word-level timestamps, and burned-in captions track the
     voice. Every transcript word should tick off against a display whose
     OCR text contains it (fuzzy — OCR mangles diacritics/casing). An
     unticked word means OCR never saw the caption for it.
  2. Pixel presence: edge-density inside the caption band (the same signal
     the band detector's presence scan uses), calibrated against the
     density measured while CONFIRMED displays are on screen.

Outcomes:
  - unticked word DURING a display  → the display's mask probably missed a
    word → its mask line-strips are widened to the full band WIDTH (height
    stays tight) — still far smaller than a band rectangle;
  - unticked word with NO display   → if pixel presence agrees, emit a
    fallback BAND segment (plain rect; inpaint rasterizes rects natively);
  - strong presence with NO display → fallback too (catches non-speech
    captions, e.g. sound-effect text), gated at the confirmed-display
    density level so plain video motion can't trigger it.

A clip whose captions don't include some spoken word loses a little
tightness (band-width strips), never gains a relic — zero relics first,
minimal area second.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("clipforge.caption_audit")

WORD_TIME_TOL_S = 0.75       # caption may lead/lag the spoken word this much
TICK_RATIO_MIN = 0.72        # fuzzy-match threshold (diacritics-stripped)
PRESENCE_FPS = 10.0          # band edge-density sampling rate
FALLBACK_BLEED_S = 0.30      # pad emitted fallback segments (cover fades)
MIN_FALLBACK_S = 0.10        # ignore sub-frame uncovered slivers
STRONG_PRESENCE_FRAC = 0.85  # ≥ this × confirmed-display density = "text for
                             # sure" even without a transcript word


def _norm(s: str) -> str:
    """Lowercase, strip diacritics + punctuation. OCR with the 'en' model
    reads Romanian text but mangles diacritics; whisper emits them — both
    sides must land on the same plain-ASCII form to compare."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "", s)


def _ticked(word: str, display_tokens: List[List[str]]) -> bool:
    """True if `word` fuzzy-matches any token of any overlapping display."""
    w = _norm(word)
    if len(w) < 2:
        return True  # punctuation / one-letter words can't be audited
    for tokens in display_tokens:
        for t in tokens:
            if not t:
                continue
            if w in t or t in w:
                return True
            if SequenceMatcher(None, w, t).ratio() >= TICK_RATIO_MIN:
                return True
    return False


def _scan_band(
    video_path: str,
    band: Tuple[int, int, int, int],
    probe_times: List[float],
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[List[float], List[float], List[Tuple[float, "np.ndarray"]]]:
    """One sequential decode pass: sample the band's edge density at
    ~PRESENCE_FPS AND capture the band crop nearest each probe time (for the
    OCR probes). Returns (times, dens, [(probe_t, crop), ...])."""
    bx, by, bw, bh = band
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return [], [], []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    stride = max(1, int(round(fps / PRESENCE_FPS)))
    probe_frames = sorted({max(0, int(round(t * fps))) for t in probe_times})
    times: List[float] = []
    dens: List[float] = []
    probes: List[Tuple[float, np.ndarray]] = []
    pi = 0
    fidx = 0
    while True:
        if not cap.grab():
            break
        want_probe = pi < len(probe_frames) and fidx == probe_frames[pi]
        if fidx % stride == 0 or want_probe:
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                crop = frame[by:by + bh, bx:bx + bw]
                if crop.size:
                    if fidx % stride == 0:
                        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                        edges = cv2.Canny(gray, 60, 160)
                        times.append(fidx / fps)
                        dens.append(float(edges.mean()))
                    if want_probe:
                        probes.append((fidx / fps, crop.copy()))
                if on_progress and total and fidx % 120 == 0:
                    on_progress(min(1.0, fidx / total), f"Audit scan {fidx}/{total}")
        if want_probe:
            pi += 1
        fidx += 1
    cap.release()
    return times, dens, probes


def _widen_suspect_mask(seg: dict, bx: int, bw: int) -> None:
    """Widen a suspect display's mask line-strips to the full band width.
    Height stays tight (only rows the mask already touches). Updates the
    segment's bbox in place."""
    mask = seg.get("mask")
    if mask is None:
        return
    rows = mask.any(axis=1)
    if not rows.any():
        return
    mask[rows, bx:bx + bw] = 255
    ys = np.where(rows)[0]
    seg["x"] = bx
    seg["w"] = bw
    seg["y"] = int(ys.min())
    seg["h"] = int(ys.max() - ys.min() + 1)
    seg["mask_kind"] = "tight_widened"


def audit_caption_coverage(
    video_path: str,
    *,
    roi: dict,
    segments: List[dict],
    transcript_words: Optional[List[dict]] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> List[dict]:
    """Audit detect_caption_displays() output against the transcript + band
    pixels. MUTATES suspect displays' masks in place (line-strips widened to
    the band) and RETURNS extra fallback band segments to append.

    `roi` is the caption band {x,y,w,h} (auto-located or drawn).
    `transcript_words` is [{"word","start","end"}, ...] from whisper.
    """
    if not segments:
        return []

    bx, by = int(roi.get("x", 0)), int(roi.get("y", 0))
    bw, bh = max(1, int(roi.get("w", 1))), max(1, int(roi.get("h", 1)))

    disp_windows = [(float(s["start_t"]), float(s["end_t"])) for s in segments]
    disp_tokens = [
        [_norm(t) for t in (s.get("ocr_text") or "").split()] for s in segments
    ]

    # ── 1. Transcript checklist ─────────────────────────────────────────────
    suspects: set = set()
    miss_windows: List[Tuple[float, float]] = []  # unticked word, NO display
    n_words = n_unticked = 0
    for wrec in transcript_words or []:
        try:
            ws = float(wrec["start"]); we = float(wrec["end"])
            word = str(wrec.get("word") or "")
        except (KeyError, TypeError, ValueError):
            continue
        if not word.strip():
            continue
        n_words += 1
        lo, hi = ws - WORD_TIME_TOL_S, we + WORD_TIME_TOL_S
        over = [i for i, (ds, de) in enumerate(disp_windows) if ds < hi and de > lo]
        if _ticked(word, [disp_tokens[i] for i in over]):
            continue
        n_unticked += 1
        if over:
            suspects.update(over)       # a display was there but missed the word
        else:
            miss_windows.append((ws, we))  # nothing detected near this word

    for i in sorted(suspects):
        _widen_suspect_mask(segments[i], bx, bw)
    if n_words:
        logger.info(
            f"audit checklist: {n_words} transcript words, {n_unticked} unticked "
            f"→ {len(suspects)} suspect displays widened, "
            f"{len(miss_windows)} uncovered word windows"
        )

    # ── 2. Uncovered windows → OCR probes + presence ────────────────────────
    # Complement of the display windows over the clip. Interior gaps ≤ ~0.45s
    # are already covered by the detector's bridge segments, so what's left is
    # real caption pauses, the clip head/tail, and anything OCR missed
    # wholesale. Each uncovered window gets 1–3 direct OCR probes — direct
    # evidence beats a density threshold.
    merged: List[List[float]] = []
    for ds, de in sorted(disp_windows):
        if merged and ds <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], de)
        else:
            merged.append([ds, de])
    duration = _video_duration(video_path)
    uncovered: List[Tuple[float, float]] = []
    cursor = 0.0
    for ds, de in merged:
        if ds - cursor >= MIN_FALLBACK_S:
            uncovered.append((cursor, ds))
        cursor = max(cursor, de)
    if duration - cursor >= MIN_FALLBACK_S:
        uncovered.append((cursor, duration))

    probe_times: List[float] = []
    for a, b in uncovered:
        n_p = min(3, max(1, int((b - a) / 0.4)))
        probe_times += [a + (b - a) * (k + 1) / (n_p + 1) for k in range(n_p)]

    times, dens, probes = _scan_band(
        video_path, (bx, by, bw, bh), probe_times, on_progress
    )
    if not times:
        # Can't verify pixels — cover the missed words unconditionally (the
        # checklist alone says text was probably there; a few band-rect
        # seconds beat a relic).
        return [
            {"start_t": max(0.0, ws - FALLBACK_BLEED_S), "end_t": we + FALLBACK_BLEED_S,
             "x": bx, "y": by, "w": bw, "h": bh, "mask_kind": "band_fallback"}
            for ws, we in miss_windows
        ]

    fallbacks: List[dict] = []

    # 2a. OCR probes — any readable text in an uncovered window = certain miss.
    probe_hits: List[float] = []
    if probes:
        from services.caption_detector import _get_reader
        reader = _get_reader()
        for pt, crop in probes:
            try:
                results = reader.readtext(crop)
            except Exception:
                results = []
            if any(c and c > 0.25 and txt and txt.strip() for _b, txt, c in results):
                probe_hits.append(pt)
    for pt in probe_hits:
        win = next(((a, b) for a, b in uncovered if a <= pt <= b), None)
        lo = max(0.0, max(pt - 0.75, win[0]) if win else pt - 0.75)
        hi = min(pt + 0.75, win[1]) if win else pt + 0.75
        fallbacks.append({
            "start_t": lo, "end_t": hi,
            "x": bx, "y": by, "w": bw, "h": bh, "mask_kind": "band_fallback",
        })

    # 2b. Presence runs at 10fps — catch flashes BETWEEN the probe points.
    d = np.array(dens, dtype=np.float32)
    covered = np.zeros(len(times), dtype=bool)
    for k, t in enumerate(times):
        for ds, de in disp_windows:
            if ds <= t < de:
                covered[k] = True
                break
    if covered.any() and (~covered).any():
        conf_dens = float(np.median(d[covered]))          # confirmed-text level
        idle_dens = float(np.percentile(d[~covered], 20))  # quiet-band level
    else:
        conf_dens = float(np.percentile(d, 90))
        idle_dens = float(np.percentile(d, 20))
    thr_word = idle_dens + 0.35 * max(1e-3, conf_dens - idle_dens)
    thr_strong = max(thr_word, STRONG_PRESENCE_FRAC * conf_dens)

    n = len(times)
    i = 0
    while i < n:
        if covered[i] or d[i] <= thr_word:
            i += 1
            continue
        j = i
        while j < n and not covered[j] and d[j] > thr_word:
            j += 1
        t0, t1 = times[i], times[j - 1]
        run_mean = float(d[i:j].mean())
        word_hit = any(ws < t1 + WORD_TIME_TOL_S and we > t0 - WORD_TIME_TOL_S
                       for ws, we in miss_windows)
        if (t1 - t0) >= MIN_FALLBACK_S and (word_hit or run_mean >= thr_strong):
            fallbacks.append({
                "start_t": max(0.0, t0 - FALLBACK_BLEED_S),
                "end_t": t1 + FALLBACK_BLEED_S,
                "x": bx, "y": by, "w": bw, "h": bh,
                "mask_kind": "band_fallback",
            })
        i = j

    fallbacks = _merge_fallbacks(fallbacks)
    logger.info(
        f"audit: {len(uncovered)} uncovered windows, {len(probe_hits)} OCR probe "
        f"hits; presence conf={conf_dens:.2f} idle={idle_dens:.2f} "
        f"→ {len(fallbacks)} fallback band segment(s)"
    )
    return fallbacks


def _video_duration(path: str) -> float:
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    cap.release()
    return (total / fps) if fps > 0 else 0.0


def _merge_fallbacks(fallbacks: List[dict]) -> List[dict]:
    """Merge overlapping/adjacent fallback band segments (same rect)."""
    if not fallbacks:
        return []
    fallbacks.sort(key=lambda s: s["start_t"])
    out = [fallbacks[0]]
    for f in fallbacks[1:]:
        if f["start_t"] <= out[-1]["end_t"] + 0.05:
            out[-1]["end_t"] = max(out[-1]["end_t"], f["end_t"])
        else:
            out.append(f)
    return out
