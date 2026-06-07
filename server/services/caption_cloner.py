"""
ClipForge — Caption Cloner

Analyse a reference caption video and produce a ClipForge caption template
(same JSON shape as captioner.DEFAULT_PRESETS) reproducing the style. Extracts
position/size/uppercase/words (OCR), fill+outline colour (pixel sampling),
italic (shear search), font family (render-and-match vs installed fonts) and
animation (temporal heuristic). Returns {"template", "diagnostics"} for the UI
to confirm/tweak before saving. Reuses EasyOCR + the font roots.
"""

from __future__ import annotations

import base64
import logging
import math
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("clipforge.caption_cloner")

ProgressCb = Optional[Callable[[float, str], None]]


# ---------------------------------------------------------------------------
# Frame sampling + OCR
# ---------------------------------------------------------------------------

def _sample_detections(
    video_path: str, max_frames: int, on_progress: ProgressCb,
) -> Tuple[List[dict], int, int, float]:
    """OCR a spread of frames → (detections, width, height, fps). Each
    detection is {t, x, y, w, h, text, conf}; the strongest few also carry a
    BGR `crop` for colour + font sampling."""
    from services.caption_detector import _get_reader, _ocr_frame, _bbox_from_easyocr

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if total <= 0:
        total = int(fps * 10)

    reader = _get_reader()
    step = max(1, total // max(1, max_frames))
    detections: List[dict] = []
    kept_frames = 0
    idx = 0
    while True:
        ret = cap.grab()
        if not ret:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                t = idx / fps
                for bbox, text, conf in _ocr_frame(reader, frame):
                    text = (text or "").strip()
                    if not text or conf < 0.3:
                        continue
                    x, y, bw, bh = _bbox_from_easyocr(bbox)
                    det = {"t": t, "x": x, "y": y, "w": bw, "h": bh,
                           "text": text, "conf": float(conf)}
                    # Keep crops (colour + font work). Favour longer words —
                    # they discriminate fonts far better than 1-2 char tokens.
                    if conf > 0.5 and len(text) >= 3 and kept_frames < 16:
                        pad = max(4, bh // 6)
                        y0, y1 = max(0, y - pad), min(frame.shape[0], y + bh + pad)
                        x0, x1 = max(0, x - pad), min(frame.shape[1], x + bw + pad)
                        det["crop"] = frame[y0:y1, x0:x1].copy()
                        kept_frames += 1
                    detections.append(det)
                if on_progress:
                    on_progress(min(0.6, 0.6 * idx / max(1, total)), "Scanning captions (OCR)…")
        idx += 1
    cap.release()
    return detections, w, h, fps


# ── Colour extraction ──────────────────────────────────────────────────────

def _bg_color(crop: np.ndarray) -> np.ndarray:
    """Estimate background colour from the crop's border band."""
    h, wd = crop.shape[:2]
    band = max(2, min(h, wd) // 12)
    edges = np.concatenate([
        crop[:band].reshape(-1, 3), crop[-band:].reshape(-1, 3),
        crop[:, :band].reshape(-1, 3), crop[:, -band:].reshape(-1, 3),
    ], axis=0)
    return np.median(edges, axis=0)


def _bgr_to_hex(bgr) -> str:
    b, g, r = (int(max(0, min(255, round(c)))) for c in bgr)
    return f"#{r:02X}{g:02X}{b:02X}"


def _extract_colors(crop: np.ndarray) -> Dict:
    """Return {text_color, outline_color, outline_px, has_outline, density, bg}.

    Erodes the glyph mask: the core is the FILL, the (mask − core) shell is the
    OUTLINE ring. Measures outline thickness too. A black outline on black bg
    blends in → kept black."""
    bg = _bg_color(crop)
    dist = np.linalg.norm(crop.astype(np.float32) - bg.astype(np.float32), axis=2)
    text_mask = (dist > 60.0).astype(np.uint8)
    density = float(text_mask.mean())
    n_text = int(text_mask.sum())
    if n_text < 40:
        return {"text_color": "#FFFFFF", "outline_color": "#000000", "outline_px": 0,
                "has_outline": False, "density": density, "bg": _bgr_to_hex(bg)}

    # Measure stroke thickness via distance transform → choose erosion radius.
    dt = cv2.distanceTransform(text_mask, cv2.DIST_L2, 3)
    stroke = float(np.median(dt[dt > 0])) * 2.0  # ~full stroke width
    erode_r = max(1, int(round(stroke * 0.28)))
    core = cv2.erode(text_mask, np.ones((erode_r * 2 + 1, erode_r * 2 + 1), np.uint8))
    ring = cv2.subtract(text_mask, core)

    fill_px = crop[core.astype(bool)]
    ring_px = crop[ring.astype(bool)]
    if len(fill_px) < 20:
        fill_px = crop[text_mask.astype(bool)]
    fill = np.median(fill_px, axis=0)

    has_outline = False
    outline = np.array([0, 0, 0], dtype=np.float32)
    outline_px = 0
    if len(ring_px) >= 30:
        outline = np.median(ring_px, axis=0)
        # Real outline only if the ring colour differs from the fill AND from bg.
        if (np.linalg.norm(outline - fill) > 45.0
                and np.linalg.norm(outline - bg) > 45.0):
            has_outline = True
            # Ring thickness: the distance transform peaks at half the band
            # width, so a high percentile ×2 approximates the full outline px.
            dtr = cv2.distanceTransform(ring, cv2.DIST_L2, 3)
            vals = dtr[dtr > 0]
            outline_px = int(round(float(np.percentile(vals, 80)) * 2.0)) if len(vals) else erode_r

    return {
        "text_color": _bgr_to_hex(fill),
        "outline_color": _bgr_to_hex(outline) if has_outline else "#000000",
        "outline_px": outline_px,
        "has_outline": has_outline,
        "density": density,
        "bg": _bgr_to_hex(bg),
    }


def _detect_italic(mask: np.ndarray) -> Tuple[bool, float]:
    """Detect italic/oblique text by shear-search: find the shear that makes
    vertical strokes most vertical (max variance of the column projection).
    A large de-shear angle ⇒ the glyphs were slanted ⇒ italic."""
    h, w = mask.shape[:2]
    if h < 8 or w < 8:
        return False, 0.0
    best_deg, best_score = 0.0, -1.0
    for deg in range(-26, 27, 2):
        rad = math.radians(deg)
        s = math.tan(rad)
        M = np.array([[1, s, -s * h / 2], [0, 1, 0]], dtype=np.float32)
        out_w = w + int(abs(s) * h) + 2
        sheared = cv2.warpAffine(mask, M, (out_w, h), flags=cv2.INTER_NEAREST)
        col = (sheared > 127).sum(axis=0).astype(np.float32)
        score = float(np.var(col))
        if score > best_score:
            best_score, best_deg = score, float(deg)
    # Italic faces lean right ~8-20°; require a clear de-shear to straighten.
    is_italic = abs(best_deg) >= 8
    return is_italic, best_deg


# ── Font render-and-match (lives in caption_font_match) ─────────────────────

def _binary_glyphs(crop: np.ndarray, bg_hex: str) -> Optional[np.ndarray]:
    """White-on-black binary mask of the glyphs, tightly cropped."""
    bg = np.array([int(bg_hex[5:7], 16), int(bg_hex[3:5], 16), int(bg_hex[1:3], 16)])
    dist = np.linalg.norm(crop.astype(np.float32) - bg.astype(np.float32), axis=2)
    mask = (dist > 60).astype(np.uint8) * 255
    ys, xs = np.where(mask > 0)
    if len(xs) < 20:
        return None
    return mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


# ---------------------------------------------------------------------------
# Animation heuristic
# ---------------------------------------------------------------------------

def _guess_animation(detections: List[dict], fps: float, words_per: int) -> str:
    """word-by-word reveal vs whole-phrase.

    - 1-2 words shown at a time → "word" (the captions change word by word,
      whether they replace or accumulate — that's the word-reveal look).
    - Otherwise, group by time gaps and check whether the word count grows
      within a caption (accumulating reveal) vs appears whole (phrase).
    """
    if not detections:
        return "word"
    if words_per <= 2:
        return "word"
    dets = sorted(detections, key=lambda d: d["t"])
    groups: List[List[dict]] = [[dets[0]]]
    for d in dets[1:]:
        if d["t"] - groups[-1][-1]["t"] <= 0.6:
            groups[-1].append(d)
        else:
            groups.append([d])
    grew = 0
    stable = 0
    for g in groups:
        counts = [len(d["text"].split()) for d in g]
        if len(counts) >= 2 and max(counts) - min(counts) >= 1:
            grew += 1
        else:
            stable += 1
    return "word" if grew >= stable else "phrase"


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def clone_caption_style(video_path: str, *, max_frames: int = 24,
                        on_progress: ProgressCb = None) -> Dict:
    """Analyse the reference video and return {"template", "diagnostics"}."""
    if not Path(video_path).exists():
        raise FileNotFoundError(video_path)
    t0 = time.time()
    if on_progress:
        on_progress(0.02, "Loading reference video…")

    detections, w, h, fps = _sample_detections(video_path, max_frames, on_progress)
    if not detections:
        raise RuntimeError(
            "No captions detected in the reference video. Make sure the clip "
            "shows readable on-screen text."
        )
    w = w or 1080
    h = h or 1920

    # ── Position (top/center/bottom + margin) ──────────────────────────────
    y_centers = np.array([d["y"] + d["h"] / 2 for d in detections]) / h
    y_med = float(np.median(y_centers))
    position = "top" if y_med < 0.34 else ("center" if y_med < 0.66 else "bottom")

    # ── Font size (normalised to a 1080-wide reference canvas) ─────────────
    heights = np.array([d["h"] for d in detections], dtype=np.float32)
    med_h = float(np.median(heights))
    font_size = int(round(med_h * (1080.0 / w) * 0.92))
    font_size = max(28, min(140, font_size))

    # ── Uppercase + words per line ─────────────────────────────────────────
    alpha = [c for d in detections for c in d["text"] if c.isalpha()]
    uppercase = bool(alpha) and sum(c.isupper() for c in alpha) / len(alpha) > 0.8
    # EasyOCR may split one caption line into several boxes — sum words shown
    # at each sampled timestamp, then take the median across timestamps.
    from collections import defaultdict
    words_at_t: Dict[float, int] = defaultdict(int)
    for d in detections:
        words_at_t[round(d["t"], 2)] += len(d["text"].split())
    words_per = int(round(float(np.median(list(words_at_t.values())))))
    words_per = max(1, min(6, words_per))

    if on_progress:
        on_progress(0.65, "Sampling colours…")

    # ── Colours (from the strongest crop) ──────────────────────────────────
    crops = [d for d in detections if "crop" in d]
    crops.sort(key=lambda d: d["conf"] * d["w"] * d["h"], reverse=True)
    best = crops[0] if crops else None
    if best is not None:
        colors = _extract_colors(best["crop"])
    else:
        colors = {"text_color": "#FFFFFF", "outline_color": "#000000",
                  "has_outline": False, "density": 0.15, "bg": "#000000"}
    bold = colors.get("density", 0.15) > 0.16
    # Outline width from the measured ring thickness, normalised to 1080 width.
    # No outline detected → 0 (clean text, e.g. white-on-black with no border).
    if colors["has_outline"] and colors.get("outline_px", 0) > 0:
        outline_width = max(1, round(colors["outline_px"] * (1080.0 / w)))
    elif colors["has_outline"]:
        outline_width = max(2, round(font_size / 16))
    else:
        outline_width = 0

    # ── Italic + font match ────────────────────────────────────────────────
    if on_progress:
        on_progress(0.72, "Matching font against your library…")
    italic = False
    font_candidates: List[dict] = []
    if best is not None:
        gm = _binary_glyphs(best["crop"], colors["bg"])
        if gm is not None:
            try:
                italic, _ = _detect_italic(gm)
            except Exception as e:
                logger.warning(f"italic detect failed: {e}")
            # Gather several word crops for the font match. Prefer the LONGEST
            # words — short tokens ("să", "to") barely constrain the typeface,
            # so averaging over long words is both more accurate and stable.
            font_crops = sorted(crops, key=lambda d: len(d["text"]), reverse=True)[:8]
            samples: List[Tuple[str, np.ndarray]] = []
            for c in font_crops:
                m = _binary_glyphs(c["crop"], colors["bg"])
                if m is not None:
                    samples.append((c["text"], m))
            if not samples:
                samples = [(best["text"], gm)]
            try:
                from services.caption_font_match import match_font
                font_candidates = match_font(samples, bold, italic)
            except Exception as e:
                logger.warning(f"font match failed: {e}")
    font_family = font_candidates[0]["family"] if font_candidates else ("Impact" if bold else "Arial")

    # ── Animation ──────────────────────────────────────────────────────────
    if on_progress:
        on_progress(0.9, "Detecting animation…")
    animation = _guess_animation(detections, fps, words_per)

    template = {
        "name": "Cloned style",
        "font_family": font_family,
        "font_size": font_size,
        "font_weight": "Black" if (bold and colors.get("density", 0) > 0.2) else ("Bold" if bold else "Regular"),
        "italic": italic,
        "text_color": colors["text_color"],
        "highlight_color": colors["text_color"],
        "outline_color": colors["outline_color"],
        "outline_width": outline_width,
        "shadow_offset": 0 if colors["has_outline"] else 2,
        "shadow_color": "#000000A0",
        "position": position,
        "uppercase": uppercase,
        "animation": animation,
        "max_words_per_line": words_per,
    }

    # Reference crop (PNG, base64) for the UI side-by-side.
    ref_b64 = ""
    if best is not None:
        ok, png = cv2.imencode(".png", best["crop"])
        if ok:
            ref_b64 = base64.b64encode(png.tobytes()).decode("ascii")

    diagnostics = {
        "frames_with_text": len({round(d["t"], 2) for d in detections}),
        "detections": len(detections),
        "video_dims": [w, h],
        "font_candidates": font_candidates,
        "font_matched": bool(font_candidates and font_candidates[0]["score"] > 0.55),
        "outline_detected": colors["has_outline"],
        "bg_color": colors["bg"],
        "reference_crop_png_b64": ref_b64,
        "sample_text": best["text"] if best else "",
        "elapsed_s": round(time.time() - t0, 1),
        "confidence_notes": _confidence_notes(colors, font_candidates),
    }
    logger.info(
        f"caption clone: font={font_family} size={font_size} pos={position} "
        f"upper={uppercase} anim={animation} ({diagnostics['elapsed_s']}s)"
    )
    return {"template": template, "diagnostics": diagnostics}


def _confidence_notes(colors: Dict, font_candidates: List[dict]) -> List[str]:
    notes: List[str] = []
    if not font_candidates or font_candidates[0]["score"] < 0.55:
        notes.append("Font is a best guess — pick/upload the exact font for a 1:1 match.")
    if not colors["has_outline"]:
        notes.append("No outline detected (or it blends with the background) — defaulted to black.")
    notes.append("Animation is detected heuristically — confirm it matches the reference.")
    return notes
