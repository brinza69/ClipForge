"""
Auto-detect on-screen captions in a video.

Strategy:
  1. Sample frames at ~3 fps (cheap enough; catches caption transitions).
  2. Run EasyOCR on each sample frame — gives text + bbox + confidence.
  3. Cluster detected boxes by vertical position (Y-center) into "lanes".
     Most clips have one caption lane (bottom); occasionally two (top + bottom)
     or shifting lanes (caption moves mid-clip).
  4. Per lane, split detections into time segments — a >1.5s gap with no
     text → new segment. This is what handles "captions move mid-clip":
     before the move = one segment with the old bbox; after = another
     segment with the new bbox.
  5. For each segment, take the UNION of all detected boxes — the widest
     bbox spanning the segment's lifetime. This is the "longest caption
     occupies the largest zone" rule.
  6. Return a list of {start_t, end_t, x, y, w, h} entries that
     `inpaint_region(segments=...)` knows how to consume.

EasyOCR is imported lazily so the server boots even if it's not installed
yet. First detect call may take ~10s to warm up the model.
"""

from __future__ import annotations

import difflib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import cv2
import numpy as np

logger = logging.getLogger("clipforge.caption_detector")

# ── T20 tight-mask params (see docs/improvement-plan.md §11.6) ───────────────
DISPLAY_SIM_MIN = 0.6      # text-similarity below this = a new display
DISPLAY_GAP_S = 0.8       # time gap above this = a new display
GLYPH_DILATE_PX = 4       # grow the glyph mask to cover outline/shadow edges
GLYPH_LOCAL_THR = 28      # local-contrast threshold for a glyph/outline pixel
GLYPH_LINE_BRIDGE = 0.9   # horizontal close kernel = this × text height (bridge
                          # inter-word gaps into one line strip — anti-relic)
BOX_STD_MAX = 25.0        # non-glyph colour std below this = a solid box style
BOUND_EXPAND_S = 0.30     # extend each display's time bounds (cover fades)

_reader = None
_reader_lock = None  # lazy init
_LANGS = os.environ.get("CLIPFORGE_OCR_LANGS", "en").split(",")


def _get_reader():
    """Lazy singleton — EasyOCR Reader is expensive to construct."""
    global _reader, _reader_lock
    if _reader is not None:
        return _reader
    if _reader_lock is None:
        import threading
        _reader_lock = threading.Lock()
    with _reader_lock:
        if _reader is not None:
            return _reader
        try:
            import easyocr  # noqa
        except ImportError as e:
            raise RuntimeError(
                "easyocr is not installed. Run "
                "`pip install easyocr` in the server venv and retry."
            ) from e
        try:
            import torch
            gpu = torch.cuda.is_available()
        except Exception:
            gpu = False
        logger.info(f"Loading EasyOCR (langs={_LANGS}, gpu={gpu})…")
        t0 = time.time()
        _reader = easyocr.Reader(_LANGS, gpu=gpu, verbose=False)
        logger.info(f"EasyOCR loaded in {time.time() - t0:.1f}s")
        return _reader


@dataclass
class _Detection:
    t: float
    x: int
    y: int
    w: int
    h: int
    text: str
    conf: float

    @property
    def y_center(self) -> float:
        return self.y + self.h / 2


@dataclass
class _Lane:
    y_center: float
    detections: List[_Detection] = field(default_factory=list)

    def add(self, d: _Detection) -> None:
        n = len(self.detections)
        # Running average: weight new detection's y_center modestly so the
        # lane center doesn't drift forever if caption migrates.
        self.y_center = (self.y_center * n + d.y_center) / (n + 1)
        self.detections.append(d)


def _bbox_from_easyocr(bbox) -> tuple[int, int, int, int]:
    """EasyOCR returns 4 corner points: [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]."""
    xs = [int(p[0]) for p in bbox]
    ys = [int(p[1]) for p in bbox]
    x, y = min(xs), min(ys)
    w, h = max(xs) - x, max(ys) - y
    return x, y, max(1, w), max(1, h)


def _ocr_frame(reader, frame: np.ndarray) -> List[tuple]:
    """Run OCR on a BGR frame. Returns easyocr's [(bbox, text, conf), ...]."""
    # EasyOCR accepts BGR ndarray directly
    return reader.readtext(frame)


def detect_caption_segments(
    video_path: str,
    *,
    sample_fps: float = 5.0,        # was 3.0 — catches transient text
    min_conf: float = 0.25,         # was 0.35 — stylized fonts score lower
    lane_threshold_frac: float = 0.08,
    min_detections_per_lane: int = 3,
    segment_gap_s: float = 1.5,
    padding_px: int = 6,            # was 12 — tighter bbox; inpaint dilates separately
    bleed_s: float = 0.4,           # was 0.2 — covers brief fade-in/fade-out
    drift_threshold: float = 0.30,  # NEW — split segments when bbox center
                                     # drifts more than this fraction of current size
    roi: Optional[dict] = None,     # {x,y,w,h} — only look for captions whose
                                     # centre falls inside this region. The user's
                                     # drawn erase rect, so scene text elsewhere
                                     # (busy animated frames) is never erased.
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> List[dict]:
    """
    Scan a video and return caption time-segments.

    Each returned dict: {start_t, end_t, x, y, w, h} in input-pixel coords.
    Empty list = no captions detected.
    """
    reader = _get_reader()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = (total / fps) if fps > 0 else 0.0

    step = max(1, int(round(fps / max(0.5, sample_fps))))
    n_samples = total // step if step > 0 else 0
    logger.info(
        f"Caption detect start: {vw}x{vh} {duration:.1f}s, "
        f"sampling every {step} frames (~{sample_fps} fps)"
    )

    detections: List[_Detection] = []
    sample_idx = 0
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % step == 0:
            t = frame_idx / fps
            try:
                ocr_results = _ocr_frame(reader, frame)
            except Exception:
                logger.exception(f"OCR failed at t={t:.2f}; skipping frame")
                ocr_results = []
            for bbox, text, conf in ocr_results:
                if conf is None or conf < min_conf:
                    continue
                if not text or not text.strip():
                    continue
                x, y, w, h = _bbox_from_easyocr(bbox)
                detections.append(_Detection(t=t, x=x, y=y, w=w, h=h, text=text, conf=float(conf)))
            sample_idx += 1
            if on_progress and n_samples > 0 and sample_idx % 5 == 0:
                try:
                    on_progress(min(1.0, sample_idx / n_samples), f"OCR {sample_idx}/{n_samples}")
                except Exception:
                    pass
        frame_idx += 1

    cap.release()
    logger.info(f"OCR done: {len(detections)} text detections across {sample_idx} sample frames")

    # Restrict to the region of interest (the user's erase rect). Keep only
    # detections whose CENTRE is inside it — busy frames have scene text all
    # over, and we must not erase anything outside the marked caption band.
    if roi:
        rx, ry = int(roi.get("x", 0)), int(roi.get("y", 0))
        rx2, ry2 = rx + int(roi.get("w", vw)), ry + int(roi.get("h", vh))
        before = len(detections)
        detections = [
            d for d in detections
            if rx <= (d.x + d.w / 2) <= rx2 and ry <= (d.y + d.h / 2) <= ry2
        ]
        logger.info(f"ROI filter: {before} → {len(detections)} detections inside "
                    f"({rx},{ry})-({rx2},{ry2})")

    if not detections:
        return []

    # Cluster detections into lanes by Y-center
    lane_threshold = vh * lane_threshold_frac
    lanes: List[_Lane] = []
    for d in sorted(detections, key=lambda x: x.t):
        matched: Optional[_Lane] = None
        best_dist = lane_threshold
        for lane in lanes:
            dist = abs(d.y_center - lane.y_center)
            if dist < best_dist:
                matched = lane
                best_dist = dist
        if matched is not None:
            matched.add(d)
        else:
            lanes.append(_Lane(y_center=d.y_center, detections=[d]))

    logger.info(f"Clustered into {len(lanes)} caption lanes")

    # Per lane → a robust, tight zone bbox (percentile union drops the odd
    # over-wide OCR box that would inflate the erase region).
    zones: List[dict] = []
    for lane in lanes:
        if len(lane.detections) < min_detections_per_lane:
            continue
        xs0 = np.array([d.x for d in lane.detections], dtype=np.float32)
        xs1 = np.array([d.x + d.w for d in lane.detections], dtype=np.float32)
        ys0 = np.array([d.y for d in lane.detections], dtype=np.float32)
        ys1 = np.array([d.y + d.h for d in lane.detections], dtype=np.float32)
        x0 = max(0, int(np.percentile(xs0, 3)) - padding_px)
        y0 = max(0, int(np.percentile(ys0, 3)) - padding_px)
        x1 = min(vw, int(np.percentile(xs1, 97)) + padding_px)
        y1 = min(vh, int(np.percentile(ys1, 97)) + padding_px)
        # Never let the erase box spill outside the user's region.
        if roi:
            x0 = max(x0, int(roi.get("x", 0)))
            y0 = max(y0, int(roi.get("y", 0)))
            x1 = min(x1, int(roi.get("x", 0)) + int(roi.get("w", vw)))
            y1 = min(y1, int(roi.get("y", 0)) + int(roi.get("h", vh)))
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        zones.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                      "ocr_times": sorted(d.t for d in lane.detections)})

    if not zones:
        return []

    # Frame-accurate presence: OCR (sampled) only located the zones; now scan
    # EVERY frame's edge density inside each zone. Text = many edges, idle
    # background = few. This catches frames OCR missed (hard-to-read words,
    # fades, transitions) so no caption frame is left un-erased.
    segments = _presence_segments(
        video_path, zones, fps, total, bleed_s, on_progress
    )
    segments.sort(key=lambda s: s["start_t"])
    logger.info(f"Produced {len(segments)} caption segments (per-frame presence)")
    return segments


def _presence_segments(
    video_path: str, zones: List[dict], fps: float, total: int,
    bleed_s: float, on_progress: Optional[Callable[[float, str], None]],
) -> List[dict]:
    """Presence scan inside each zone → contiguous time segments. Samples at
    ~`presence_fps` (not every frame) using grab() to skip the decode of
    skipped frames — captions persist far longer than 50ms, so this keeps the
    timing accurate while cutting the extra decode pass ~3× on 60fps clips."""
    presence_fps = 20.0
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    stride = max(1, int(round(fps / presence_fps)))
    eff_fps = fps / stride
    dens: List[List[float]] = [[] for _ in zones]
    times: List[float] = []
    fidx = 0
    while True:
        if not cap.grab():           # advance without decoding
            break
        if fidx % stride == 0:
            ok, frame = cap.retrieve()  # decode only the sampled frame
            if ok and frame is not None:
                times.append(fidx / fps)
                for zi, z in enumerate(zones):
                    crop = frame[z["y0"]:z["y1"], z["x0"]:z["x1"]]
                    if crop.size == 0:
                        dens[zi].append(0.0)
                        continue
                    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    edges = cv2.Canny(gray, 60, 160)
                    dens[zi].append(float(edges.mean()))
                if on_progress and total and len(times) % 30 == 0:
                    on_progress(min(1.0, fidx / total), f"Scanning {fidx}/{total}")
        fidx += 1
    cap.release()
    if not times:
        return []

    bridge = max(1, int(round(0.30 * eff_fps)))   # fill flicker gaps up to ~0.3s
    min_len = max(1, int(round(0.12 * eff_fps)))   # drop sub-0.12s noise runs
    out: List[dict] = []
    for zi, z in enumerate(zones):
        d = np.array(dens[zi], dtype=np.float32)
        if d.max() <= 1e-3:
            continue
        # Adaptive threshold between the idle floor and the text peak.
        lo = float(np.percentile(d, 20))
        hi = float(np.percentile(d, 90))
        thr = lo + 0.30 * max(1e-3, hi - lo)
        present = d > thr
        n = len(present)
        # Anchor: every sample OCR actually saw text is forced present (a busy
        # background that confuses the edge threshold can't drop an OCR-confirmed
        # moment → no regression vs the old detector).
        anchor = max(1, int(round(0.12 * eff_fps)))
        for ot in z.get("ocr_times", []):
            k = int(round(ot * eff_fps))
            present[max(0, k - anchor):min(n, k + anchor + 1)] = True
        # Bridge short gaps so word-to-word transitions stay one segment.
        i = 0
        while i < n:
            if present[i]:
                i += 1
                continue
            j = i
            while j < n and not present[j]:
                j += 1
            if 0 < i and j < n and (j - i) <= bridge:
                present[i:j] = True
            i = j
        # Emit runs of True as segments.
        i = 0
        while i < n:
            if not present[i]:
                i += 1
                continue
            j = i
            while j < n and present[j]:
                j += 1
            if (j - i) >= min_len:
                out.append({
                    "start_t": max(0.0, times[i] - bleed_s),
                    "end_t": times[min(j, n - 1)] + bleed_s,
                    "x": z["x0"], "y": z["y0"],
                    "w": max(1, z["x1"] - z["x0"]),
                    "h": max(1, z["y1"] - z["y0"]),
                })
            i = j
    return out


# ═══════════════════════════════════════════════════════════════════════════
# T20 — per-display TIGHT masks (glyph or box). Erase the least, no band rect.
# ═══════════════════════════════════════════════════════════════════════════

def _frame_text(dets_at_t: List[_Detection]) -> str:
    """Join the texts of one sample frame's detections, ordered left-to-right
    then top-to-bottom, into a single comparable string."""
    ordered = sorted(dets_at_t, key=lambda d: (round(d.y / 20), d.x))
    return " ".join(d.text.strip() for d in ordered if d.text and d.text.strip())


def _glyph_or_box_mask(
    frame_bgr: np.ndarray, boxes: List[tuple], vw: int, vh: int
) -> np.ndarray:
    """Build a tight full-frame uint8 {0,255} mask for one display from its
    line boxes. Per box: Otsu-threshold the glyphs; if the NON-glyph pixels in
    the box are a near-uniform colour (a solid caption box), mask the whole box
    rectangle instead (§11.3d). Returns a (vh, vw) mask."""
    full = np.zeros((vh, vw), dtype=np.uint8)
    for (x, y, w, h) in boxes:
        x0 = max(0, x); y0 = max(0, y)
        x1 = min(vw, x + w); y1 = min(vh, y + h)
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        crop = frame_bgr[y0:y1, x0:x1]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        # LOCAL-CONTRAST glyph mask (robust to OUTLINED text). Captions are
        # almost always a bright FILL with a dark OUTLINE (or vice-versa). Otsu
        # picks only one polarity, leaving the other as a readable ghost. The
        # absolute difference from a heavily-blurred version of the crop is
        # HIGH for BOTH the bright fill and the dark outline (both stand out
        # from the local background) and LOW for the smooth background — so the
        # mask covers the whole glyph + outline, tight, on any background.
        bh = max(1, y1 - y0)
        sigma = max(5.0, bh / 4.0)
        blurred = cv2.GaussianBlur(gray, (0, 0), sigma)
        local = cv2.absdiff(gray, blurred)
        glyph = (local > GLYPH_LOCAL_THR).astype(np.uint8) * 255
        # Close small holes so a glyph is solid, drop tiny specks.
        glyph = cv2.morphologyEx(glyph, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

        # Box-vs-glyph (§11.3d). A real caption BOX has an interior fill that is
        # (1) near-uniform AND (2) DIFFERENT from the video just OUTSIDE the
        # bbox. Glyphs over plain video fail test (2) → stay tight.
        is_box = False
        bg_mask = glyph == 0
        if bg_mask.any():
            interior = crop[bg_mask].reshape(-1, 3).astype(np.float32)
            if interior.std(axis=0).mean() < BOX_STD_MAX:
                m = 6
                strips = []
                for s in (frame_bgr[max(0, y0 - m):y0, x0:x1],
                          frame_bgr[y1:min(vh, y1 + m), x0:x1],
                          frame_bgr[y0:y1, max(0, x0 - m):x0],
                          frame_bgr[y0:y1, x1:min(vw, x1 + m)]):
                    if s.size:
                        strips.append(s.reshape(-1, 3).astype(np.float32))
                if strips:
                    outer = np.concatenate(strips, axis=0)
                    is_box = float(np.linalg.norm(interior.mean(0) - outer.mean(0))) > BOX_STD_MAX
        if is_box:
            full[y0:y1, x0:x1] = 255            # solid box → erase whole rect
        else:
            full[y0:y1, x0:x1] = np.maximum(full[y0:y1, x0:x1], glyph)  # tight glyphs

    # LINE-STRIP close (the anti-RELIC step). Bridge horizontal gaps between
    # letters AND between adjacent WORD-boxes on the same line, so each caption
    # line becomes one continuous covered strip instead of separate glyph/word
    # islands. If a word is faint or barely detected, the strip still covers its
    # position → no edge fragments left (this is exactly the "sco…nter" relic).
    # Kernel width ≈ the median text height bridges normal inter-word gaps;
    # vertical stays small (3px) so separate lines don't merge. The erased
    # spaces inpaint to background — visually free.
    if boxes and full.any():
        med_h = int(np.median([max(1, h) for (_, _, _, h) in boxes]))
        kw = max(3, int(med_h * GLYPH_LINE_BRIDGE))
        full = cv2.morphologyEx(full, cv2.MORPH_CLOSE, np.ones((3, kw), np.uint8))
    return full


def detect_caption_displays(
    video_path: str,
    *,
    roi: dict,
    sample_fps: float = 5.0,
    min_conf: float = 0.25,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> List[dict]:
    """T20 tight path. Returns caption segments — one per DISPLAY (a held-still
    text) — each carrying a tight per-glyph/box `mask` (full-frame uint8).

    `roi` ({x,y,w,h}) bounds where captions live (the drawn box, or the
    auto-located band from Step D). Only detections centred inside it count.

    Each dict: {start_t, end_t, x, y, w, h, mask, mask_kind}. Feed straight to
    inpaint_region(segments=...).
    """
    reader = _get_reader()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, int(round(fps / max(0.5, sample_fps))))

    rx = int(roi.get("x", 0)); ry = int(roi.get("y", 0))
    rx2 = rx + int(roi.get("w", vw)); ry2 = ry + int(roi.get("h", vh))

    # Pass 1: OCR each sample → per-sample (frame_idx, t, detections, conf-sum).
    samples: List[dict] = []
    frame_idx = 0
    nsmp = (total // step) if step else 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % step == 0:
            t = frame_idx / fps
            try:
                results = _ocr_frame(reader, frame)
            except Exception:
                results = []
            dets: List[_Detection] = []
            for bbox, text, conf in results:
                if conf is None or conf < min_conf or not (text and text.strip()):
                    continue
                bx, by, bw, bh = _bbox_from_easyocr(bbox)
                cx, cy = bx + bw / 2, by + bh / 2
                if not (rx <= cx <= rx2 and ry <= cy <= ry2):
                    continue
                dets.append(_Detection(t=t, x=bx, y=by, w=bw, h=bh,
                                       text=text, conf=float(conf)))
            if dets:
                samples.append({
                    "fidx": frame_idx, "t": t, "dets": dets,
                    "text": _frame_text(dets),
                    "conf": sum(d.conf for d in dets),
                })
            if on_progress and nsmp:
                k = frame_idx // step
                if k % 5 == 0:
                    on_progress(min(1.0, k / nsmp), f"OCR {k}/{nsmp}")
        frame_idx += 1
    cap.release()

    if not samples:
        logger.info("detect_caption_displays: no in-ROI text found")
        return []

    # Pass 2: group consecutive samples into displays by text similarity + gap.
    displays: List[dict] = []
    cur: Optional[dict] = None
    for s in samples:
        if cur is None:
            cur = {"samples": [s], "best": s}
            continue
        prev = cur["samples"][-1]
        sim = difflib.SequenceMatcher(None, prev["text"], s["text"]).ratio()
        gap = s["t"] - prev["t"]
        if sim >= DISPLAY_SIM_MIN and gap <= DISPLAY_GAP_S:
            cur["samples"].append(s)
            if s["conf"] > cur["best"]["conf"]:
                cur["best"] = s
        else:
            displays.append(cur)
            cur = {"samples": [s], "best": s}
    if cur is not None:
        displays.append(cur)

    logger.info(f"detect_caption_displays: {len(samples)} text samples → "
                f"{len(displays)} displays")

    # Pass 3: build each display's tight mask from its BEST frame. We decode
    # the video SEQUENTIALLY (not cap.set(POS_FRAMES) — index seeking lands on
    # the nearest keyframe on long-GOP mp4, so the mask would be built from the
    # WRONG frame's text and leak). Build the mask the moment we reach the
    # needed frame, then discard it (low memory).
    need: dict = {}   # best frame_idx → display
    for disp in displays:
        need.setdefault(disp["best"]["fidx"], disp)
    built: dict = {}  # frame_idx → (mask, bbox)
    cap = cv2.VideoCapture(video_path)
    fpos = 0
    remaining = set(need.keys())
    while remaining:
        ret, frame = cap.read()
        if not ret:
            break
        if fpos in remaining:
            disp = need[fpos]
            boxes = [(d.x, d.y, d.w, d.h) for d in disp["best"]["dets"]]
            mask = _glyph_or_box_mask(frame, boxes, vw, vh)
            ys, xs = np.where(mask > 0)
            if xs.size:
                built[fpos] = (mask, (int(xs.min()), int(ys.min()),
                                      int(xs.max()), int(ys.max())))
            remaining.discard(fpos)
        fpos += 1
    cap.release()

    out: List[dict] = []
    for disp in displays:
        fidx = disp["best"]["fidx"]
        if fidx not in built:
            continue
        mask, (x0, y0, x1, y1) = built[fidx]
        out.append({
            "start_t": max(0.0, disp["samples"][0]["t"]),
            "end_t": disp["samples"][-1]["t"],
            "x": x0, "y": y0, "w": max(1, x1 - x0 + 1), "h": max(1, y1 - y0 + 1),
            "mask": mask,
            "mask_kind": "tight",
        })
    out.sort(key=lambda s: s["start_t"])

    # T20 Step E — fade/boundary completeness. Extend each display's time
    # bounds outward by BOUND_EXPAND_S so the (reused) tight mask also covers
    # fade-in/out frames. Clamp into the GAP to the neighbouring display so two
    # adjacent displays (different masks) never overlap in time — in the
    # overlap zone _find_active_segment would pick one mask and the other text
    # would leak.
    for i, seg in enumerate(out):
        prev_end = out[i - 1]["end_t"] if i > 0 else -1e9
        next_start = out[i + 1]["start_t"] if i + 1 < len(out) else 1e9
        new_start = max(seg["start_t"] - BOUND_EXPAND_S, prev_end)
        new_end = min(seg["end_t"] + BOUND_EXPAND_S, next_start)
        seg["start_t"] = max(0.0, min(new_start, seg["start_t"]))
        seg["end_t"] = max(seg["end_t"], new_end)

    logger.info(f"detect_caption_displays: produced {len(out)} tight-mask segments")
    return out


# ── T20 Step D — auto-localize the caption band (no manual box) ──────────────

DRIFT_MAX_FRAC = 0.012     # held-still: centre may move < this × frame diagonal
SPEECH_OVERLAP_MIN = 0.25  # a caption lane should overlap speech at least this


def _overlap_fraction(intervals_a, intervals_b) -> float:
    """Fraction of total time in `intervals_a` that overlaps any interval in
    `intervals_b`. Both are lists of (start, end) seconds."""
    if not intervals_a:
        return 0.0
    total = sum(max(0.0, e - s) for s, e in intervals_a)
    if total <= 0:
        return 0.0
    ov = 0.0
    for s, e in intervals_a:
        for bs, be in intervals_b:
            lo, hi = max(s, bs), min(e, be)
            if hi > lo:
                ov += hi - lo
    return min(1.0, ov / total)


def auto_locate_caption_band(
    video_path: str,
    *,
    speech_intervals: Optional[List[tuple]] = None,
    sample_fps: float = 3.0,
    min_conf: float = 0.25,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> Optional[dict]:
    """Find the caption lane automatically — no manual box. Returns an ROI
    {x,y,w,h} or None if no convincing caption lane is found (caller should
    then fall back to a default band or Thorough mode).

    Discriminators (vs scene text): held-still (captions hold, scene text
    moves), recurs in a consistent band, and — when `speech_intervals` are
    given — overlaps speech in time (captions track the voice).
    """
    reader = _get_reader()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, int(round(fps / max(0.5, sample_fps))))
    diag = (vw ** 2 + vh ** 2) ** 0.5
    drift_max = DRIFT_MAX_FRAC * diag

    # Sample → per-sample list of detections over the WHOLE frame.
    per_sample: List[List[_Detection]] = []
    fidx = 0
    nsmp = (total // step) if step else 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if fidx % step == 0:
            t = fidx / fps
            try:
                results = _ocr_frame(reader, frame)
            except Exception:
                results = []
            dets = []
            for bbox, text, conf in results:
                if conf is None or conf < min_conf or not (text and text.strip()):
                    continue
                bx, by, bw, bh = _bbox_from_easyocr(bbox)
                dets.append(_Detection(t=t, x=bx, y=by, w=bw, h=bh, text=text, conf=float(conf)))
            per_sample.append(dets)
            if on_progress and nsmp:
                k = fidx // step
                if k % 5 == 0:
                    on_progress(min(1.0, k / nsmp), f"Locating band {k}/{nsmp}")
        fidx += 1
    cap.release()

    # Held-still filter: keep a detection only if a detection in the adjacent
    # sample sits at nearly the same centre (it persisted in place).
    held: List[_Detection] = []
    for i, dets in enumerate(per_sample):
        neighbours = []
        if i > 0:
            neighbours += per_sample[i - 1]
        if i + 1 < len(per_sample):
            neighbours += per_sample[i + 1]
        for d in dets:
            dcx, dcy = d.x + d.w / 2, d.y + d.h / 2
            for o in neighbours:
                ocx, ocy = o.x + o.w / 2, o.y + o.h / 2
                if ((dcx - ocx) ** 2 + (dcy - ocy) ** 2) ** 0.5 <= drift_max:
                    held.append(d)
                    break
    if not held:
        logger.info("auto_locate_caption_band: no held-still text found")
        return None

    # Cluster held-still detections into lanes by Y-centre.
    lane_threshold = vh * 0.08
    lanes: List[_Lane] = []
    for d in sorted(held, key=lambda x: x.t):
        best, bd = None, lane_threshold
        for lane in lanes:
            dist = abs(d.y_center - lane.y_center)
            if dist < bd:
                best, bd = lane, dist
        if best is not None:
            best.add(d)
        else:
            lanes.append(_Lane(y_center=d.y_center, detections=[d]))

    # Score each lane: distinct sample-times with text × (speech overlap if given).
    best_lane, best_score = None, 0.0
    for lane in lanes:
        times = sorted({round(d.t, 2) for d in lane.detections})
        if len(times) < 3:
            continue
        coverage = len(times)
        score = float(coverage)
        if speech_intervals:
            # Build the lane's text-present intervals (merge close sample times).
            ivs = [(t - 0.3, t + 0.3) for t in times]
            ov = _overlap_fraction(ivs, speech_intervals)
            if ov < SPEECH_OVERLAP_MIN:
                score *= 0.3   # demote lanes that don't track speech (logos etc.)
            else:
                score *= (1.0 + ov)
        if score > best_score:
            best_lane, best_score = lane, score

    if best_lane is None:
        logger.info("auto_locate_caption_band: no lane scored high enough")
        return None

    xs0 = np.array([d.x for d in best_lane.detections], dtype=np.float32)
    xs1 = np.array([d.x + d.w for d in best_lane.detections], dtype=np.float32)
    ys0 = np.array([d.y for d in best_lane.detections], dtype=np.float32)
    ys1 = np.array([d.y + d.h for d in best_lane.detections], dtype=np.float32)
    pad = 10
    x0 = max(0, int(np.percentile(xs0, 5)) - pad)
    y0 = max(0, int(np.percentile(ys0, 5)) - pad)
    x1 = min(vw, int(np.percentile(xs1, 95)) + pad)
    y1 = min(vh, int(np.percentile(ys1, 95)) + pad)
    roi = {"x": x0, "y": y0, "w": max(1, x1 - x0), "h": max(1, y1 - y0)}
    logger.info(f"auto_locate_caption_band: lane at {roi} (score={best_score:.1f})")
    return roi
