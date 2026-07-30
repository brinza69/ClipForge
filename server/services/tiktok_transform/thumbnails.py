"""
ClipForge — Video Transformare TikTok: thumbnail generation (step 7).

Builds the 3 required 1080x1920 PNG thumbnail variants from the project's
already-selected frames (services/tiktok_transform/frames.py output, stored
in project["frames"]). No frame extraction, no LLM calls, no network — this
is pure Pillow compositing over on-disk frame images.

Variants (spec §12):
  before_after  — top/bottom split using the frames marked "before"/"after"
  mystery       — one darkened mid frame with big question-style text
  final_result  — the final reveal frame, dominant, with a closing headline

Text rules (STRICT, spec §12): Romanian, 3-7 words, big + centred, white and
yellow with a black outline, a dark plate behind it for contrast, bottom area
left clear (TikTok's own UI lives there), no narrator avatar, no invented
people/buildings, never "Like & Follow". Text is pulled from the project's
own script/description when a short enough phrase exists there (never
fabricated); otherwise a safe generic Romanian phrase is used.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("clipforge.tiktok.thumbnails")

CANVAS_W, CANVAS_H = 1080, 1920

# Keep TikTok's own UI (like/comment/share rail + caption) readable — no text
# or focal content goes below this line.
SAFE_BOTTOM_MARGIN = 420

_VARIANTS = ("before_after", "mystery", "final_result")

# Words that flag a sentence as a call-to-action ("like & follow" etc.) so it
# never gets reused as thumbnail headline text — the spec explicitly forbids it.
_CTA_WORDS = (
    "like", "follow", "urmăr", "urmar", "abonea", "distribuie", "share",
    "comentea", "comentariu", "dă follow", "da follow",
)

# Safe generic Romanian fallbacks (3-7 words each), used only when nothing
# short enough can be lifted from the project's own script/description.
_FALLBACK_TEXT = {
    "before_after": [
        "Nu o să-ți vină să crezi",
        "Transformare completă, uite ce-a ieșit",
        "Uite cum arăta la început",
        "De la haos la perfecțiune",
    ],
    "mystery": [
        "Ce s-a întâmplat aici de fapt?",
        "Ghici ce urmează să vezi?",
        "Poți ghici ce e asta?",
        "Ce crezi că urmează acum?",
    ],
    "final_result": [
        "Rezultatul final e uluitor",
        "Așa arată acum, incredibil",
        "Nimeni nu se aștepta la asta",
        "Transformarea s-a încheiat perfect",
    ],
}

# Two-tone palette (spec: "white and yellow with a black outline").
_WHITE = (255, 255, 255, 255)
_YELLOW = (255, 214, 10, 255)
_BLACK = (0, 0, 0, 255)


def _fonts_dir() -> Path:
    from services.font_manager import fonts_dir

    return fonts_dir()


def _headline_font(size: int) -> ImageFont.FreeTypeFont:
    path = _fonts_dir() / "BebasNeue-Regular.ttf"
    return ImageFont.truetype(str(path), size)


def _label_font(size: int) -> ImageFont.FreeTypeFont:
    path = _fonts_dir() / "Inter-Black.ttf"
    return ImageFont.truetype(str(path), size)


# ── frame lookup helpers ─────────────────────────────────────────────────────


def _resolve_frame_path(project_id: str, file_field: str) -> Path:
    """`file_field` (from a frames.candidates entry) may already be an
    absolute path, or just a filename relative to candidates/selected. Try
    both rather than assuming one convention."""
    from services.tiktok_transform.storage import paths

    p = Path(file_field)
    if p.is_absolute() and p.exists():
        return p
    pdict = paths(project_id)
    for base in (pdict["candidates_dir"], pdict["selected_dir"], pdict["dir"]):
        cand = base / p.name
        if cand.exists():
            return cand
    raise FileNotFoundError(
        f"frame file {file_field!r} not found for project {project_id} "
        f"(looked in candidates/, selected/, and project dir)"
    )


def _by_index(candidates: list[dict]) -> dict[int, dict]:
    return {c["index"]: c for c in candidates if isinstance(c.get("index"), int)}


def _pick_frame(
    project_id: str,
    candidates: list[dict],
    index_by_idx: dict[int, dict],
    idx: Optional[int],
    fallback: dict,
) -> dict:
    if idx is not None and idx in index_by_idx:
        return index_by_idx[idx]
    return fallback


def _select_frames(project: dict) -> tuple[list[dict], dict[int, dict], list[int], dict]:
    frames = project.get("frames") or {}
    candidates = frames.get("candidates") or []
    if not candidates:
        raise RuntimeError(
            f"project {project.get('id')} has no extracted frames — "
            f"run the frame-extraction step (2) before generating thumbnails"
        )
    return candidates, _by_index(candidates), (frames.get("selected") or []), (frames.get("marks") or {})


# ── image composition helpers ────────────────────────────────────────────────


def _cover_resize(im: Image.Image, w: int, h: int) -> Image.Image:
    """Scale `im` to cover a w x h box (cropping the overflow), never letterboxed."""
    src_w, src_h = im.size
    scale = max(w / src_w, h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    im = im.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return im.crop((left, top, left + w, top + h))


def _load_frame(project_id: str, candidate: dict, w: int, h: int) -> Image.Image:
    path = _resolve_frame_path(project_id, candidate["file"])
    im = Image.open(path).convert("RGB")
    return _cover_resize(im, w, h)


def _darken(im: Image.Image, amount: float) -> Image.Image:
    """amount in [0,1]: 0 = untouched, 1 = black."""
    if amount <= 0:
        return im
    overlay = Image.new("RGBA", im.size, (0, 0, 0, int(255 * min(1.0, amount))))
    return Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")


def _draw_plate_label(canvas: Image.Image, text: str, xy: tuple[int, int]) -> None:
    """Small corner tag (e.g. "ÎNAINTE" / "DUPĂ") — same two-tone/outline
    treatment as the headline, just smaller, for the before/after split."""
    font = _label_font(52)
    draw = ImageDraw.Draw(canvas, "RGBA")
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=4)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 18
    plate = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(plate)
    x, y = xy
    pdraw.rounded_rectangle(
        [x - pad, y - pad, x + tw + pad, y + th + pad], radius=14, fill=(0, 0, 0, 150)
    )
    merged = Image.alpha_composite(canvas.convert("RGBA"), plate)
    draw = ImageDraw.Draw(merged)
    draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=_WHITE, stroke_width=4, stroke_fill=_BLACK)
    canvas.paste(merged.convert("RGB"), (0, 0))


def _compose_before_after(project_id: str, before_c: dict, after_c: dict) -> Image.Image:
    half_h = CANVAS_H // 2
    top = _load_frame(project_id, before_c, CANVAS_W, half_h)
    bottom = _load_frame(project_id, after_c, CANVAS_W, CANVAS_H - half_h)
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), (8, 8, 8))
    canvas.paste(top, (0, 0))
    canvas.paste(bottom, (0, half_h))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, half_h - 3, CANVAS_W, half_h + 3], fill=(255, 255, 255))
    _draw_plate_label(canvas, "ÎNAINTE", (28, 28))
    _draw_plate_label(canvas, "DUPĂ", (28, half_h + 28))
    return canvas


def _compose_single(project_id: str, candidate: dict, darken: float) -> Image.Image:
    im = _load_frame(project_id, candidate, CANVAS_W, CANVAS_H)
    return _darken(im, darken)


# ── headline text: word-wrap, two-tone, outline, dark plate ────────────────


def _wrap_words(
    words: list[str], font: ImageFont.FreeTypeFont, max_width: int, stroke_width: int
) -> list[list[str]]:
    """Greedy word-wrap using measured widths (draw.textlength ignores stroke,
    so pad by stroke_width*2 per word to stay safely inside max_width)."""
    dummy = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(dummy)
    space_w = draw.textlength(" ", font=font)
    lines: list[list[str]] = []
    current: list[str] = []
    current_w = 0.0
    for word in words:
        w = draw.textlength(word, font=font) + stroke_width * 2
        add_w = w if not current else space_w + w
        if current and current_w + add_w > max_width:
            lines.append(current)
            current = [word]
            current_w = w
        else:
            current.append(word)
            current_w += add_w
    if current:
        lines.append(current)
    return lines


def _fit_headline_font(text: str, max_width: int, max_lines: int = 3) -> tuple[ImageFont.FreeTypeFont, list[list[str]]]:
    words = text.split()
    for size in range(160, 59, -8):
        font = _headline_font(size)
        stroke_width = max(3, size // 18)
        lines = _wrap_words(words, font, max_width, stroke_width)
        if len(lines) <= max_lines:
            return font, lines
    # Smallest size still didn't fit within max_lines — use it anyway rather
    # than shrinking indefinitely (3-7 words at 60px always fits 3 lines).
    font = _headline_font(60)
    return font, _wrap_words(words, font, max_width, max(3, 60 // 18))


def _draw_headline(canvas: Image.Image, text: str) -> None:
    max_width = CANVAS_W - 140
    font, lines = _fit_headline_font(text, max_width)
    stroke_width = max(3, font.size // 18)

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    line_bboxes = [dummy_draw.textbbox((0, 0), " ".join(words), font=font, stroke_width=stroke_width) for words in lines]
    line_heights = [b[3] - b[1] for b in line_bboxes]
    line_gap = int(font.size * 0.28)
    block_h = sum(line_heights) + line_gap * (len(lines) - 1)

    block_bottom = CANVAS_H - SAFE_BOTTOM_MARGIN
    block_top = block_bottom - block_h
    pad_x, pad_y = 56, 40

    space_w = dummy_draw.textlength(" ", font=font)
    line_widths = []
    for words in lines:
        w = sum(dummy_draw.textlength(word, font=font) for word in words) + space_w * (len(words) - 1)
        line_widths.append(w)
    plate_w = max(line_widths) + pad_x * 2
    plate_left = (CANVAS_W - plate_w) / 2
    plate_right = plate_left + plate_w

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rounded_rectangle(
        [plate_left, block_top - pad_y, plate_right, block_bottom + pad_y],
        radius=28,
        fill=(0, 0, 0, 165),
    )
    merged = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    mdraw = ImageDraw.Draw(merged)

    y = block_top
    for words, lw, lh in zip(lines, line_widths, line_heights):
        x = (CANVAS_W - lw) / 2
        for i, word in enumerate(words):
            color = _WHITE if i % 2 == 0 else _YELLOW
            mdraw.text((x, y), word, font=font, fill=color, stroke_width=stroke_width, stroke_fill=_BLACK)
            x += dummy_draw.textlength(word, font=font) + space_w
        y += lh + line_gap

    canvas.paste(merged.convert("RGB"), (0, 0))


# ── text selection (reuse project content, never fabricate facts) ──────────


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _is_cta(sentence: str) -> bool:
    low = sentence.lower()
    return any(w in low for w in _CTA_WORDS)


def _trim_to_words(sentence: str, min_words: int = 3, max_words: int = 7) -> Optional[str]:
    words = sentence.strip().split()
    if len(words) < min_words:
        return None
    return " ".join(words[:max_words])


def _fallback_text(project_id: str, variant: str) -> str:
    options = _FALLBACK_TEXT[variant]
    # Deterministic per-project pick (not random) so re-running the step is
    # reproducible, while different projects don't all show the same line.
    idx = int(hashlib.sha1(project_id.encode("utf-8")).hexdigest(), 16) % len(options)
    return options[idx]


def _pick_variant_text(project: dict, variant: str) -> str:
    script_text = ((project.get("script") or {}).get("text") or "").strip()
    desc_text = ((project.get("description") or {}).get("text") or "").strip()
    sentences = [s for s in (_split_sentences(script_text) + _split_sentences(desc_text)) if not _is_cta(s)]

    if variant == "mystery":
        questions = [s for s in sentences if "?" in s]
        for s in questions:
            trimmed = _trim_to_words(s)
            if trimmed:
                return trimmed if trimmed.endswith("?") else trimmed.rstrip(".!") + "?"
        for s in sentences:
            trimmed = _trim_to_words(s)
            if trimmed:
                return trimmed.rstrip(".!?") + "?"
        return _fallback_text(project["id"], variant)

    if variant == "final_result":
        for s in reversed(sentences):
            trimmed = _trim_to_words(s)
            if trimmed:
                return trimmed
        return _fallback_text(project["id"], variant)

    # before_after: prefer the opening line (the hook usually sets up the "before")
    for s in sentences:
        trimmed = _trim_to_words(s)
        if trimmed:
            return trimmed
    return _fallback_text(project["id"], variant)


# ── public entry point ───────────────────────────────────────────────────────


def generate_thumbnails(
    project: dict,
    out_paths: dict[str, Any],
    *,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> list[dict]:
    """Render the 3 thumbnail variants to the paths given in `out_paths`
    (keyed exactly "before_after" / "mystery" / "final_result"). Returns
    [{"variant","path","text"}] in that same order."""
    project_id = project["id"]
    candidates, index_by_idx, selected, marks = _select_frames(project)

    results: list[dict] = []
    for i, variant in enumerate(_VARIANTS):
        out_path = out_paths.get(variant)
        if not out_path:
            raise KeyError(f"out_paths is missing required key {variant!r}")

        if variant == "before_after":
            fallback_before = index_by_idx.get(selected[0]) if selected else candidates[0]
            fallback_after = index_by_idx.get(selected[-1]) if selected else candidates[-1]
            before_c = _pick_frame(project_id, candidates, index_by_idx, marks.get("before"), fallback_before)
            after_c = _pick_frame(project_id, candidates, index_by_idx, marks.get("after"), fallback_after)
            canvas = _compose_before_after(project_id, before_c, after_c)
        elif variant == "mystery":
            mid_fallback = (
                index_by_idx.get(selected[len(selected) // 2])
                if selected
                else candidates[len(candidates) // 2]
            )
            mid_c = mid_fallback or candidates[len(candidates) // 2]
            canvas = _compose_single(project_id, mid_c, darken=0.55)
        else:  # final_result
            final_fallback = index_by_idx.get(selected[-1]) if selected else candidates[-1]
            final_c = _pick_frame(project_id, candidates, index_by_idx, marks.get("final_reveal"), final_fallback)
            canvas = _compose_single(project_id, final_c, darken=0.22)

        text = _pick_variant_text(project, variant)
        _draw_headline(canvas, text)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(out_path, "PNG")
        results.append({"variant": variant, "path": str(out_path), "text": text})

        if on_progress:
            on_progress((i + 1) / len(_VARIANTS), f"Thumbnail {variant} gata")

    logger.info(f"tiktok project {project_id}: generated {len(results)} thumbnails")
    return results
