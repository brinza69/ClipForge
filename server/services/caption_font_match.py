"""
ClipForge — Caption font matching

Renders the reference caption's words in every installed font face and ranks
them by how closely the rendered silhouette matches. Used by caption_cloner to
guess the typeface. Best-effort: exact only if the font is installed; otherwise
the closest faces are returned as candidates for the user to pick from.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("clipforge.caption_font_match")

_FONT_EXTS = {".ttf", ".otf", ".ttc"}


def font_file_list() -> List[dict]:
    """Scan font roots → {family, path, bold, italic} per face (not deduped by
    family, so the actual bold/italic file is available for slanted matching)."""
    import os
    from services.font_manager import fonts_dir
    roots: List[Path] = [fonts_dir()]
    if os.name == "nt":
        roots.append(Path(r"C:\Windows\Fonts"))
    roots += [Path("/mnt/c/Windows/Fonts"), Path("/usr/share/fonts"),
              Path.home() / ".fonts", Path.home() / ".local/share/fonts"]
    try:
        from fontTools.ttLib import TTFont  # type: ignore
    except Exception:
        return []

    def _face(tt) -> Optional[dict]:
        nm = tt["name"]
        family = (nm.getDebugName(16) or nm.getDebugName(1) or "").strip()
        if not family:
            return None
        sub = (nm.getDebugName(17) or nm.getDebugName(2) or "").lower()
        bold = italic = False
        try:
            mac = tt["head"].macStyle
            bold, italic = bool(mac & 0x01), bool(mac & 0x02)
        except Exception:
            pass
        bold = bold or "bold" in sub or "black" in sub or "heavy" in sub
        italic = italic or "italic" in sub or "oblique" in sub
        return {"family": family, "bold": bold, "italic": italic}

    seen: set = set()
    out: List[dict] = []
    for root in roots:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.suffix.lower() not in _FONT_EXTS:
                continue
            try:
                tt = TTFont(str(f), lazy=True, fontNumber=0)
                face = _face(tt)
                tt.close()
                if not face:
                    continue
                key = (face["family"].lower(), face["bold"], face["italic"])
                if key in seen:
                    continue
                seen.add(key)
                face["path"] = str(f)
                out.append(face)
            except Exception:
                continue
    return out


def _iou(a_bool: np.ndarray, b_bool: np.ndarray) -> float:
    inter = np.logical_and(a_bool, b_bool).sum()
    union = np.logical_or(a_bool, b_bool).sum()
    return float(inter) / float(union) if union else 0.0


def match_font(samples: List[Tuple[str, np.ndarray]], bold: bool,
               italic: bool = False) -> List[dict]:
    """Rank faces by mean silhouette IoU across several reference word crops,
    minus a width-mismatch penalty (so condensed refs prefer condensed faces).
    Each tuple is (word, tight white-on-black glyph mask)."""
    try:
        from PIL import Image, ImageFont, ImageDraw
    except Exception:
        return []
    faces = font_file_list()
    if not faces or not samples:
        return []

    # Italic refs also include non-italic faces (Bebas Neue, Anton… have no
    # italic; editors fake it) rendered with a synthetic shear. Upright refs
    # exclude italic faces.
    if italic:
        styled = [f for f in faces if (f["bold"] == bold or bold)] or faces
    else:
        styled = [f for f in faces if not f["italic"] and (f["bold"] == bold or bold)]
        if len(styled) < 8:
            styled = [f for f in faces if not f["italic"]] or faces

    # Each ref: square-normalised mask (shape) + tight aspect ratio.
    refs = []
    for text, mask in samples:
        word = (text.strip().split()[0] if text.strip() else "Sample")[:14] or "Sample"
        h0, w0 = mask.shape[:2]
        refs.append((word, (cv2.resize(mask, (256, 64), interpolation=cv2.INTER_AREA) > 127),
                     w0 / max(1, h0)))

    shear = np.array([[1, math.tan(math.radians(12)), 0], [0, 1, 0]], dtype=np.float32)

    by_family: Dict[str, float] = {}
    for face in styled:
        try:
            font = ImageFont.truetype(face["path"], 48)
        except Exception:
            continue
        fake_italic = italic and not face["italic"]
        scores = []
        for word, ref_b, ar_ref in refs:
            try:
                bbox = font.getbbox(word)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                if tw <= 0 or th <= 0:
                    continue
                img = Image.new("L", (tw + 8, th + 8), 0)
                ImageDraw.Draw(img).text((4 - bbox[0], 4 - bbox[1]), word, font=font, fill=255)
                arr = np.array(img)
                if fake_italic:
                    h1, w1 = arr.shape
                    arr = cv2.warpAffine(arr, shear, (w1 + int(0.22 * h1) + 2, h1), flags=cv2.INTER_NEAREST)
                cand_b = (cv2.resize(arr, (256, 64), interpolation=cv2.INTER_AREA) > 127)
                shape = _iou(ref_b, cand_b)
                ar_cand = (tw + (0.22 * th if fake_italic else 0)) / max(1, th)
                penalty = abs(math.log((ar_ref + 1e-3) / (ar_cand + 1e-3)))
                scores.append(max(0.0, shape - 0.45 * penalty))
            except Exception:
                continue
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        fam = face["family"]
        if avg > by_family.get(fam, -1.0):
            by_family[fam] = avg
    scored = [{"family": k, "score": round(v, 4)} for k, v in by_family.items()]
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:6]
