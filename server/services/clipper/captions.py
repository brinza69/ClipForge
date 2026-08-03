"""
ClipForge — AI Stream Clipper caption planning.

Produces a *plan* (word-synced chunks + a resolved style + a placement) that is
handed straight to `services.caption_overlays.build_overlays_ass`. Nothing here
writes ASS: the repo already has one ASS writer and a second one would drift.

Two jobs only:

  * chunking — delegated to `captioner_events._group_words`, which already does
    punctuation-aware, pause-aware, orphan-avoiding grouping. Re-deriving that
    logic is how the two caption paths would start disagreeing.
  * placement — the preset safe-zone constants give the starting Y; the layout
    plan's keep-out rects (face band, HUD, chat) pull it off anything it would
    cover.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from services.captioner_events import _group_words
from services.captioner_presets import (
    DEFAULT_PRESETS,
    SAFE_CAPTION_BOTTOM,
    SAFE_CAPTION_CENTER,
    SAFE_HOOK_MID_Y,
    SAFE_TOP,
)

logger = logging.getLogger("clipforge.clipper.captions")

DEFAULT_PRESET_ID = "bold_impact"

# Two lines maximum. 22 chars/line is what a 68-76px heavy font fits inside the
# 9:16 safe width without libass shrinking or clipping it.
MAX_LINE_CHARS = 22
MAX_LINES = 2

# The caption block as a fraction of the 1920-tall frame: two lines of a ~72px
# font with leading and outline. Used only for keep-out collision maths, so an
# approximation that errs large is the safe direction.
CAPTION_BOX_H_PCT = 0.10
CAPTION_BOX_W_PCT = 0.80

NUDGE_STEP_PCT = 0.04
MAX_NUDGE_TRIES = 5

# A chunk shorter than this reads as a flicker; libass also rounds to ms.
MIN_CHUNK_S = 0.12


# ---------------------------------------------------------------------------
# Profanity masking
# ---------------------------------------------------------------------------

# Deliberately conservative and EXACT-MATCH ONLY (no prefix/stem matching):
# a false negative just leaves a word unmasked, while stemming across a mixed
# EN+RO list mangles innocent words ("fut" would eat "future"). Inflections are
# listed explicitly rather than derived.
_PROFANITY = {
    # English
    "fuck", "fucks", "fucked", "fucking", "fucker", "fuckers",
    "shit", "shits", "shitty", "bullshit",
    "bitch", "bitches", "cunt", "cunts",
    "asshole", "assholes", "dick", "dickhead",
    "bastard", "bastards", "whore", "whores", "slut", "sluts",
    "motherfucker", "motherfuckers", "retard", "retarded",
    # Romanian
    "pula", "pule", "pulă", "pizda", "pizdă", "pizde",
    "muie", "muist", "fut", "futut", "futu",
    "curva", "curvă", "curve", "coaie", "sugi", "căcat", "cacat",
}

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def mask_profanity(text: str) -> str:
    """Star out the interior of profane words, keeping the first + last letter.

    "fucking" -> "f*****g". Words of 2 characters or fewer are left alone —
    there is no interior to mask.
    """
    if not text:
        return ""

    def _sub(m: re.Match[str]) -> str:
        word = m.group(0)
        if word.lower() not in _PROFANITY or len(word) <= 2:
            return word
        return word[0] + ("*" * (len(word) - 2)) + word[-1]

    return _WORD_RE.sub(_sub, text)


# ---------------------------------------------------------------------------
# Word extraction
# ---------------------------------------------------------------------------

def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _spread(tokens: list[str], start: float, end: float) -> list[dict]:
    """Distribute untimed tokens evenly across [start, end]."""
    if not tokens:
        return []
    span = max(end - start, 0.0)
    step = span / len(tokens) if len(tokens) else 0.0
    return [
        {"word": tok, "start": start + i * step, "end": start + (i + 1) * step}
        for i, tok in enumerate(tokens)
    ]


def _clip_words(cand: dict, transcript: dict) -> list[dict]:
    """Words inside the candidate, re-based to t=0 at the clip's start.

    Selection is by OVERLAP, not containment, then clamped: a word straddling
    the boundary is kept and trimmed rather than dropped, which matters on the
    3-second clips where dropping the first word loses a quarter of the text.
    """
    c_start = _f((cand or {}).get("start"))
    c_end = _f((cand or {}).get("end"))
    if c_end <= c_start:
        return []
    dur = c_end - c_start

    raw: list[dict] = []
    for seg in ((transcript or {}).get("segments") or []):
        s0, s1 = _f(seg.get("start")), _f(seg.get("end"))
        if s1 <= c_start or s0 >= c_end:
            continue
        words = seg.get("words") or []
        if words:
            raw.extend(words)
        else:
            # No word-level timestamps from the ASR model — spread the segment's
            # own tokens across its span so grouping still has something to cut.
            raw.extend(_spread(
                (seg.get("text") or "").split(),
                max(s0, c_start), min(s1, c_end),
            ))

    if not raw:
        # Transcript gave us nothing usable: fall back to whatever the candidate
        # itself carries (segmentation._window attaches source-timed words).
        raw = list((cand or {}).get("words") or [])
    if not raw:
        return _spread(((cand or {}).get("text") or "").split(), 0.0, dur)

    out: list[dict] = []
    for w in raw:
        token = str(w.get("word") or w.get("text") or "").strip()
        if not token:
            continue
        ws, we = _f(w.get("start")), _f(w.get("end"), _f(w.get("start")))
        if we <= c_start or ws >= c_end:
            continue
        rel_s = min(max(ws - c_start, 0.0), dur)
        rel_e = min(max(we - c_start, rel_s), dur)
        out.append({"word": token, "start": rel_s, "end": rel_e})

    out.sort(key=lambda w: (w["start"], w["end"]))
    return out


# ---------------------------------------------------------------------------
# Line wrapping
# ---------------------------------------------------------------------------

def _wrap(text: str, max_chars: int = MAX_LINE_CHARS) -> str:
    """Break into at most two balanced lines with a literal newline.

    build_overlays_ass turns "\\n" into libass "\\N", so a literal newline here
    is the whole contract.
    """
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    words = text.split(" ")
    if len(words) < 2:
        return text

    best_i, best_cost = 1, None
    for i in range(1, len(words)):
        a = " ".join(words[:i])
        b = " ".join(words[i:])
        # Balance the two lines; a 30-char/8-char split looks broken even
        # though both lines "fit".
        cost = max(len(a), len(b))
        if best_cost is None or cost < best_cost:
            best_i, best_cost = i, cost
    return " ".join(words[:best_i]) + "\n" + " ".join(words[best_i:])


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def _iter_rects(zones: Any) -> Iterable[dict]:
    """Yield rect dicts from a safe_zones dict-of-(rect|list) or plain list."""
    if isinstance(zones, dict):
        values: Iterable[Any] = zones.values()
    elif isinstance(zones, (list, tuple)):
        values = zones
    else:
        return
    for v in values:
        if isinstance(v, dict) and "w" in v and "h" in v:
            yield v
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, dict) and "w" in item and "h" in item:
                    yield item


def _norm_rect(rect: dict, out_w: int, out_h: int) -> tuple[float, float, float, float] | None:
    """Rect -> (x0, y0, x1, y1) in 0..1 frame fractions, or None if degenerate.

    Accepts both pixel rects and already-normalised ones: if the far edges both
    land inside 1.0 the rect is treated as fractions, otherwise as pixels. A
    1x1-pixel rect is the only ambiguous case and is degenerate anyway.
    """
    x, y = _f(rect.get("x")), _f(rect.get("y"))
    w, h = _f(rect.get("w")), _f(rect.get("h"))
    if w <= 0 or h <= 0:
        return None
    if (x + w) > 1.0 or (y + h) > 1.0:
        if out_w <= 0 or out_h <= 0:
            return None
        x, w = x / out_w, w / out_w
        y, h = y / out_h, h / out_h
    return (x, y, min(x + w, 1.0), min(y + h, 1.0))


def _overlap_area(y_pct: float, rects: list[tuple[float, float, float, float]]) -> float:
    """Total intersection between the caption box centred at y_pct and rects."""
    bx0 = 0.5 - CAPTION_BOX_W_PCT / 2
    bx1 = 0.5 + CAPTION_BOX_W_PCT / 2
    by0 = y_pct - CAPTION_BOX_H_PCT / 2
    by1 = y_pct + CAPTION_BOX_H_PCT / 2
    total = 0.0
    for rx0, ry0, rx1, ry1 in rects:
        ix = min(bx1, rx1) - max(bx0, rx0)
        iy = min(by1, ry1) - max(by0, ry0)
        if ix > 0 and iy > 0:
            total += ix * iy
    return total


def _base_y_pct(position: str, out_h: int) -> float:
    half = CAPTION_BOX_H_PCT / 2
    pos = (position or "bottom").strip().lower()
    if pos in ("top", "upper"):
        return (SAFE_TOP / out_h) + half
    if pos in ("center", "middle", "mid"):
        return (out_h / 2 - SAFE_CAPTION_CENTER) / out_h
    if pos in ("hook", "mid_high"):
        return SAFE_HOOK_MID_Y / out_h
    return (out_h - SAFE_CAPTION_BOTTOM) / out_h


def resolve_position(
    position: str,
    layout: dict,
    *,
    out_w: int = 1080,
    out_h: int = 1920,
) -> tuple[float, float]:
    """Caption centre as (x_pct, y_pct) fractions, clear of the keep-out rects.

    Starts from the preset/position default, then nudges UP first (a caption
    rising toward the frame centre stays readable; one sinking toward the
    bottom disappears under the platform UI), 4% at a time, up to 5 tries. If
    nothing is clear, the least-covered position wins.
    """
    out_w = int(out_w) if out_w else 1080
    out_h = int(out_h) if out_h else 1920

    base = _base_y_pct(position, out_h)
    lo = (SAFE_TOP / out_h) + CAPTION_BOX_H_PCT / 2
    hi = (out_h - SAFE_CAPTION_BOTTOM) / out_h
    if lo > hi:  # pathological output size — keep the band non-empty
        lo = hi
    base = min(max(base, lo), hi)

    rects = [
        r for r in (
            _norm_rect(rc, out_w, out_h)
            for rc in _iter_rects((layout or {}).get("safe_zones"))
        ) if r is not None
    ]
    if not rects:
        return (0.5, round(base, 4))

    offsets = [0.0]
    for k in range(1, MAX_NUDGE_TRIES + 1):
        offsets.append(-NUDGE_STEP_PCT * ((k + 1) // 2) if k % 2 else NUDGE_STEP_PCT * (k // 2))
    # -> 0, -4%, +4%, -8%, +8%, -12%

    best_y, best_cost = base, None
    seen: set[float] = set()
    for off in offsets:
        y = round(min(max(base + off, lo), hi), 4)
        if y in seen:
            continue
        seen.add(y)
        cost = _overlap_area(y, rects)
        if cost <= 0.0:
            return (0.5, y)
        if best_cost is None or cost < best_cost:
            best_y, best_cost = y, cost

    logger.debug("caption placement: no clear slot, least-overlap y=%.4f", best_y)
    return (0.5, best_y)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def build_caption_plan(
    cand: dict,
    transcript: dict,
    *,
    preset_id: str,
    max_words: int,
    position: str,
    layout: dict,
) -> dict:
    """Word-synced caption plan for one candidate.

    Chunk `start`/`end` are RELATIVE TO THE CLIP (cand["start"] subtracted), because
    the burn happens on the already-trimmed clip.
    """
    preset_key = preset_id if preset_id in DEFAULT_PRESETS else DEFAULT_PRESET_ID
    style = dict(DEFAULT_PRESETS[preset_key])

    per_group = int(max_words or 0) or int(style.get("max_words_per_line") or 3)
    per_group = max(1, min(per_group, 8))

    pos = position or style.get("position") or "bottom"
    out_w = int((layout or {}).get("out_w") or 1080)
    out_h = int((layout or {}).get("out_h") or 1920)
    x_pct, y_pct = resolve_position(pos, layout, out_w=out_w, out_h=out_h)

    words = _clip_words(cand, transcript)
    chunks: list[dict] = []
    for group in _group_words(words, per_group) if words else []:
        if not group:
            continue
        text = _wrap(mask_profanity(" ".join(w["word"] for w in group)))
        if not text.strip():
            continue
        start = round(max(group[0]["start"], 0.0), 3)
        end = round(max(group[-1]["end"], start + MIN_CHUNK_S), 3)
        chunks.append({"text": text, "start": start, "end": end})

    if not chunks:
        logger.debug("caption plan: no words in candidate %s", (cand or {}).get("id"))

    return {
        "chunks": chunks,
        "style": style,
        "x_pct": x_pct,
        "y_pct": y_pct,
        "scale": 1.0,
        "preset_id": preset_key,
    }


def caption_plan_to_overlays(plan: dict) -> list[dict]:
    """Plan -> overlay dicts in exactly the shape build_overlays_ass consumes."""
    plan = plan or {}
    style = plan.get("style") or {}
    preset_id = plan.get("preset_id") or DEFAULT_PRESET_ID
    x_pct = _f(plan.get("x_pct"), 0.5)
    y_pct = _f(plan.get("y_pct"), 0.75)
    scale = _f(plan.get("scale"), 1.0) or 1.0

    overlays: list[dict] = []
    for chunk in plan.get("chunks") or []:
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        start = _f(chunk.get("start"))
        end = _f(chunk.get("end"), start + MIN_CHUNK_S)
        overlays.append({
            "text": text,
            "start_t": max(start, 0.0),
            "end_t": max(end, start + MIN_CHUNK_S),
            "template_id": preset_id,
            "style": dict(style),
            "x_pct": x_pct,
            "y_pct": y_pct,
            "scale": scale,
            "rotation": 0.0,
        })
    return overlays
