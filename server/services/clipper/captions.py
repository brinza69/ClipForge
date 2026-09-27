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
from typing import Any, Iterable, Sequence

from services.clipper.captions_geom import (  # noqa: F401
    CAPTION_BOX_H_PCT, CAPTION_BOX_W_PCT, CLIPPER_CAPTION_CENTER_PCT,
    SCAN_STEP_PCT, SPEC_BAND_HI, SPEC_BAND_LO, _base_y_pct, _widest_band,
    scan_bounds, scan_grid)
from services.captioner_events import _group_words
from services.clipper import caption_display
from services.captioner_presets import (
    DEFAULT_PRESETS,
    SAFE_CAPTION_BOTTOM,
    SAFE_HOOK_MID_Y,
    SAFE_TOP,
)

logger = logging.getLogger("clipforge.clipper.captions")

DEFAULT_PRESET_ID = "bold_impact"

# Two lines maximum. 22 chars/line is what a 68-76px heavy font fits inside the
# 9:16 safe width without libass shrinking or clipping it.
MAX_LINE_CHARS = 22
MAX_LINES = 2

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
    lo, hi = scan_bounds(out_h)
    base = min(max(base, lo), hi)

    rects = [
        r for r in (
            _norm_rect(rc, out_w, out_h)
            for rc in _iter_rects((layout or {}).get("safe_zones"))
        ) if r is not None
    ]
    if not rects:
        return (0.5, round(base, 4))

    if _overlap_area(base, rects) <= 0.0:
        # Nothing in the way. The preset position is a measurement in its own
        # right, so it is not moved to satisfy a rule meant for avoidance.
        return (0.5, round(base, 4))

    # Something is in the way, so the style spec's rule applies: "place the text
    # centre in the largest vertical band that contains neither the facecam
    # subject's head box nor the top of the game HUD, clamped to 55-75% of frame
    # height". The clamp is not a claim about where captions look best — three
    # of the spec's own seven measurements sit below 55% — it is the guard rail
    # that stops a largest-band search wandering to the top of the frame.
    #
    # This replaces six fixed nudges of ±4%. A ladder can step over a clear gap
    # narrower than its own step and land on the far side still covered, and it
    # has no notion of how much room a position has around it: it took the first
    # y that scored zero, which can be one pixel from a rect's edge.
    grid = scan_grid(lo, hi)
    clear = [y for y in grid if _overlap_area(y, rects) <= 0.0]

    inside = [y for y in clear if SPEC_BAND_LO <= y <= SPEC_BAND_HI]
    centre = _widest_band(inside or clear)
    if centre is not None:
        return (0.5, round(centre, 4))

    # Nothing is clear anywhere. Least covered wins, and among equals the one
    # nearest the preset — a caption that has to sit on something should at
    # least sit where it was asked to.
    best_y = min(grid, key=lambda y: (round(_overlap_area(y, rects), 6), abs(y - base)))
    logger.debug("caption placement: no clear slot, least-overlap y=%.4f", best_y)
    return (0.5, round(best_y, 4))


def panels_to_keep_out(panels: Sequence[dict], shots: Sequence[dict],
                       out_h: int = 1920) -> list[dict[str, int]]:
    """Where detected UI panels land in the OUTPUT frame, across every shot.

    A static keep-out is ill-defined for a multi-shot edit: each shot crops a
    different rectangle out of the source, so one panel lands somewhere
    different in each. The caption is burned once for the whole clip, though, so
    it has to clear the panel in EVERY shot the panel is visible in — hence the
    union rather than a per-shot answer.

    Only the vertical extent is honest here. The crop is 9:16 out of a 16:9
    frame, so horizontal position survives the mapping poorly and the caption is
    centred and nearly full width anyway; `_overlap_area` treats the caption as
    a full-width strip, which is what makes the y the only part that decides.
    """
    out: list[dict[str, int]] = []
    for shot in shots or []:
        # A face shot is a crop around the streamer's head; the game UI is not
        # in it at all. Mapping a panel through one is arithmetic with no
        # referent, and because a face crop is 240-360px tall against a 1920
        # output the scale factor is 5-8x, so a 60px panel becomes a 300-480px
        # band placed by that arithmetic rather than by anything a viewer sees.
        #
        # This is not a theory. It shipped, and it made the reviewer report "66%
        # of the caption sits on detected game UI" for a clip whose captions sit
        # on the streamer's hoodie, well clear of the counter below them. Caught
        # by a vision model disagreeing with it and confirmed by looking at the
        # frames.
        if str((shot or {}).get("camera", "")).startswith("face"):
            continue
        rect = (shot or {}).get("rect") or {}
        rh, ry = _f(rect.get("h")), _f(rect.get("y"))
        if rh <= 0:
            continue
        scale = out_h / rh
        for panel in panels or []:
            py, ph = _f(panel.get("y")), _f(panel.get("h"))
            top = (py - ry) * scale
            bottom = (py + ph - ry) * scale
            if bottom <= 0 or top >= out_h:
                continue                      # outside this shot's crop
            out.append({"x": 0, "y": int(max(0.0, top)),
                        "w": 1080, "h": int(min(out_h, bottom) - max(0.0, top)),
                        "kind": "game_ui"})
    return out


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
    entry_pop: bool = False,
) -> dict:
    """Word-synced caption plan for one candidate.

    Chunk `start`/`end` are RELATIVE TO THE CLIP (cand["start"] subtracted), because
    the burn happens on the already-trimmed clip.

    `entry_pop` asks the ASS writer for the reference scale overshoot as each card
    lands. Off by default, like the active-word highlight.
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
        # Carry the word timings alongside the rendered text: the ASS writer
        # uses them for the active-word highlight and silently ignores them if
        # they stop lining up with the tokens it ends up drawing.
        chunks.append({
            "text": text,
            "start": start,
            "end": end,
            "words": [{"word": w["word"],
                       "start": round(max(w["start"], 0.0), 3),
                       "end": round(max(w["end"], w["start"]), 3)} for w in group],
        })

    if not chunks:
        logger.debug("caption plan: no words in candidate %s", (cand or {}).get("id"))
    # Display time, declared as display (BURST1): no two cards on the anchor at once, a burst with no
    # room for a legible card is a reported limit, never a new "measured" timing.
    duration = max(_f((cand or {}).get("end")) - _f((cand or {}).get("start")), 0.0)
    chunks, limits = caption_display.settle(chunks, duration, MIN_CHUNK_S)

    return {
        "chunks": chunks,
        "display": {"rule": caption_display.RULE, "min_chunk_s": MIN_CHUNK_S, "limits": limits},
        "style": style,
        "x_pct": x_pct,
        "y_pct": y_pct,
        "scale": 1.0,
        "preset_id": preset_key,
        "entry_pop": bool(entry_pop),
    }


def caption_plan_to_overlays(plan: dict) -> list[dict]:
    """Plan -> overlay dicts in exactly the shape build_overlays_ass consumes."""
    plan = plan or {}
    style = plan.get("style") or {}
    preset_id = plan.get("preset_id") or DEFAULT_PRESET_ID
    x_pct = _f(plan.get("x_pct"), 0.5)
    y_pct = _f(plan.get("y_pct"), 0.75)
    scale = _f(plan.get("scale"), 1.0) or 1.0
    entry_pop = bool(plan.get("entry_pop"))
    # A settled plan's ends are already display time; extending them again would put two cards on
    # the anchor at once. A stored plan without the rule renders exactly as it always has.
    settled = caption_display.is_settled(plan)

    overlays: list[dict] = []
    for chunk in plan.get("chunks") or []:
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        if settled and chunk.get("display") == "no_time":
            continue                  # the clip ended first; listed in plan["display"]["limits"]
        start = _f(chunk.get("start"))
        end = _f(chunk.get("end"), start + MIN_CHUNK_S)
        overlay = {
            "text": text,
            "start_t": max(start, 0.0),
            "end_t": end if settled and end > start else max(end, start + MIN_CHUNK_S),
            "template_id": preset_id,
            "style": dict(style),
            "x_pct": x_pct,
            "y_pct": y_pct,
            "scale": scale,
            "rotation": 0.0,
        }
        if entry_pop:
            overlay["entry_pop"] = True
        # A redistributed card's slot is not when its words were said: no per-word highlight on it.
        if chunk.get("words") and not (settled and chunk.get("display") == "redistributed"):
            overlay["words"] = chunk["words"]
        overlays.append(overlay)
    return overlays
