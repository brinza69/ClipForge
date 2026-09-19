"""Caption placement for game_top_face_bottom + game_content_fit plans.

Reserves the fitted foreground OUTPUT rectangle as a temporary keep-out,
so the caption lands in the blur gap between game content and the reaction
band rather than overlapping either. The stored safe_zones are not mutated.

Called from clipper_reaction.PUT and clipper_render_plan._decide_render so
both paths share one decision. Returns None for every bypass condition so
callers leave caption_plan unchanged: manual placements, missing plans and
empty/removed caption events. Callers bypass suppressed layers before invoking
this helper. Unsupported automatic styles raise an actionable ValueError.
"""

from __future__ import annotations

import math

from services.clipper.captions import (
    _norm_rect, _overlap_area, caption_plan_to_overlays, resolve_position,
)
from services.clipper.layout_geom import OUT_H, OUT_W, _bands, _game_fit_box

# Supported envelope for automatic placement. Conservative operational limits
# on the approximate two-line gap, not a proof for arbitrary fonts.
_MAX_FONT_PX = 72.0
_MAX_SCALE = 1.0
_MAX_OUTLINE = 5.0
_MAX_SHADOW = 3.0
_MAX_LINE_CHARS = 22
_MAX_LINES = 2

_MANUAL_SUFFIX = "Set the caption position manually."


def _finite(value: object, field: str) -> float:
    """Return float(value), refusing non-numeric and non-finite inputs."""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(
            f"Caption style field {field!r} is not a number. {_MANUAL_SUFFIX}"
        )
    if not math.isfinite(v):
        raise ValueError(
            f"Caption style field {field!r} must be finite; got {v}. {_MANUAL_SUFFIX}"
        )
    return v


def _check_style_envelope(
    style: dict, scale: float, entry_pop: bool
) -> None:
    """Raise ValueError with an actionable message if outside the envelope.

    `style` must be the merged preset + inline style from _resolve_style, not
    the inline-only dict from caption_plan. A plan with only preset_id and no
    inline style dict would otherwise pass with the preset's default values,
    missing e.g. viral_gradient's 76px font.
    """
    if entry_pop:
        raise ValueError(
            "entry_pop is not supported by automatic caption placement in "
            f"reaction mode. {_MANUAL_SUFFIX}"
        )
    scale_v = _finite(scale, "scale")
    if scale_v <= 0 or scale_v > _MAX_SCALE:
        raise ValueError(
            f"Automatic placement in reaction mode requires 0 < scale <= "
            f"{_MAX_SCALE}; got {scale_v:.3f}. {_MANUAL_SUFFIX}"
        )
    font_v = _finite(style.get("font_size", 64.0), "font_size")
    if font_v <= 0:
        raise ValueError(
            f"font_size must be positive; got {font_v}. {_MANUAL_SUFFIX}"
        )
    effective = font_v * scale_v
    if effective > _MAX_FONT_PX:
        raise ValueError(
            f"Automatic placement in reaction mode requires effective font "
            f"<= {_MAX_FONT_PX:.0f}px "
            f"(font_size={font_v:.0f} × scale={scale_v:.3f} = {effective:.1f}px). "
            f"{_MANUAL_SUFFIX}"
        )
    outline_v = _finite(style.get("outline_width", 4), "outline_width")
    if outline_v < 0 or outline_v > _MAX_OUTLINE:
        raise ValueError(
            f"Automatic placement in reaction mode requires 0 <= outline_width "
            f"<= {_MAX_OUTLINE}; got {outline_v}. {_MANUAL_SUFFIX}"
        )
    shadow_v = _finite(style.get("shadow_offset", 0), "shadow_offset")
    if shadow_v < 0 or shadow_v > _MAX_SHADOW:
        raise ValueError(
            f"Automatic placement in reaction mode requires 0 <= shadow_offset "
            f"<= {_MAX_SHADOW}; got {shadow_v}. {_MANUAL_SUFFIX}"
        )


def _check_text_envelope(chunks: list[dict], uppercase: bool = False) -> None:
    """Raise ValueError if any chunk contains unsupported text or ASS controls."""
    for chunk in chunks:
        text = str(chunk.get("text") or "").replace("\r\n", "\n")
        # Raw ASS control sequences bypass both the line-count and size guards:
        # \N is a hard line break, {\fs...} / {\fscy...} etc. change size. Refuse
        # rather than trying to interpret them in this automatic mode.
        if "\\" in text or "{" in text:
            raise ValueError(
                "Caption text contains ASS control sequences (backslash or "
                f"braces) and cannot be automatically placed. {_MANUAL_SUFFIX}"
            )
        lines = text.split("\n")
        if len(lines) > _MAX_LINES:
            raise ValueError(
                f"Automatic placement supports at most {_MAX_LINES} lines per "
                f"chunk; got {len(lines)} in {text!r}. {_MANUAL_SUFFIX}"
            )
        for line in lines:
            display = line.upper() if uppercase else line
            if len(display) > _MAX_LINE_CHARS:
                raise ValueError(
                    f"Automatic placement supports at most {_MAX_LINE_CHARS} "
                    f"characters per line; line {display!r} has {len(display)}. "
                    f"{_MANUAL_SUFFIX}"
                )


def _foreground_keep_out(
    plan: dict, out_w: int = OUT_W, out_h: int = OUT_H
) -> dict:
    """OUTPUT rect occupied by the fitted game content in a game_content_fit plan."""
    face_pct = float(plan.get("face_pct") or 0.35)
    game_rect = plan.get("game_rect") or {}
    _band_h, rest_h = _bands(face_pct, out_h)
    fg_w, fg_h, off_x, off_y = _game_fit_box(game_rect, out_w, rest_h)
    # game is at the top in game_top_face_bottom, so off_y is from y=0
    return {"x": off_x, "y": off_y, "w": fg_w, "h": fg_h, "kind": "game_fg"}


def resolve_reaction_caption_y(
    plan: dict,
    caption_plan: dict,
    drop_spans: list | None = None,
    clip_duration: float | None = None,
    out_w: int = OUT_W,
    out_h: int = OUT_H,
) -> float | None:
    """Resolve y_pct for a reaction layout, keeping clear of the fitted foreground.

    Applies only to game_top_face_bottom + game_content_fit=True plans.
    Returns None for every bypass condition so callers leave caption_plan
    unchanged — a None is not a placement, it is "nothing to decide here".

    Raises ValueError (actionable) when:
    - the resolved position overlaps a keep-out (no clear slot; the generic
      resolver's least-overlap fallback is not acceptable in this mode)
    - the caption style is outside the supported envelope
    """
    # Not a reaction layout — caller proceeds with whatever it has
    if (not isinstance(plan, dict)
            or plan.get("layout") != "game_top_face_bottom"
            or plan.get("game_content_fit") is not True):
        return None

    if not isinstance(caption_plan, dict):
        return None

    # Bypass: manual placement — user owns this position
    if caption_plan.get("y_pct_manual"):
        return None

    # Determine surviving overlays via the shared caption writer so drop spans
    # and empty chunks are handled consistently with the actual render path.
    overlays = caption_plan_to_overlays(caption_plan)
    # clip_duration is on the ORIGINAL clip clock, so filter before remapping.
    # In a 4s clip with [0,3) removed, an event at 5s remaps to 2s. Comparing
    # that to the original 4s would invent visibility in the delivered 1s clip.
    if clip_duration is not None and clip_duration > 0:
        overlays = [o for o in overlays
                    if float(o.get("start_t") or 0) < clip_duration]
    if drop_spans:
        from services.clipper.dead_air import remap_overlays
        overlays = remap_overlays(overlays, drop_spans)
    if not overlays:
        # No events survive — nothing to place
        return None

    # Resolve the merged preset + inline style for the first surviving overlay.
    # All overlays from one caption_plan share the same template/style/scale, so
    # checking the first is sufficient for the envelope check.
    from services.caption_overlays import _resolve_style
    first = overlays[0]
    merged_style = _resolve_style(first)
    scale = float(first.get("scale") or 1.0)
    entry_pop = bool(first.get("entry_pop"))

    _check_style_envelope(merged_style, scale, entry_pop)

    # Check only surviving text, normalized as the writer displays it.
    uppercase = bool(merged_style.get("uppercase"))
    _check_text_envelope(overlays, uppercase=uppercase)

    # Build temporary keep-outs: existing safe_zones + fitted foreground rect.
    # The stored safe_zones are not mutated.
    existing = list((plan.get("safe_zones") or {}).get("keep_out") or [])
    fg_rect = _foreground_keep_out(plan, out_w, out_h)
    temp_keep_out = existing + [fg_rect]
    temp_layout = {"safe_zones": {"keep_out": temp_keep_out}}

    position = str(caption_plan.get("position") or "bottom")
    _x, y = resolve_position(position, temp_layout, out_w=out_w, out_h=out_h)

    # Verify: the returned position must not overlap any keep-out.
    # The generic resolver's least-overlap fallback is not acceptable here.
    temp_rects = [r for r in (
        _norm_rect(rc, out_w, out_h) for rc in temp_keep_out
    ) if r is not None]
    if temp_rects and _overlap_area(y, temp_rects) > 0.0:
        raise ValueError(
            "No clear caption position was found automatically in this reaction layout — "
            "the blur gap may be too narrow for the caption style. "
            "Set the caption position manually (editor → captions) "
            "or choose a different framing."
        )

    return y
