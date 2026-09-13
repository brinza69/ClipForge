"""
ClipForge — AI Stream Clipper: explicit static reaction layout builder.

Builds a game_top_face_bottom plan from caller-supplied SOURCE rectangles.
No detector or provenance inference is performed. Caller supplied regions;
content outside them is not promised preserved.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.clipper.layout_geom import (
    FACE_PCT_MAX,
    FACE_PCT_MIN,
    _safe_zones,
)

__all__ = ["plan_reaction_layout"]


def _validate_src(src_w: Any, src_h: Any) -> None:
    for name, v in (("src_w", src_w), ("src_h", src_h)):
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"{name}: expected int, got {type(v).__name__}")
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"src_w and src_h must be positive, got {src_w}×{src_h}")
    if src_w % 2 != 0 or src_h % 2 != 0:
        raise ValueError(f"src_w and src_h must be even, got {src_w}×{src_h}")


def _validate_rect(rect: Any, src_w: int, src_h: int, name: str) -> dict[str, int]:
    """Require integer, even w/h/x/y, positive, bounded source rectangle.

    Does not silently grow, trim, or infer — any violation is refused.
    Even x and y are required because yuv420p crop rounds odd origins by default.
    """
    if not isinstance(rect, dict):
        raise ValueError(f"{name}: expected a dict, got {type(rect).__name__}")
    for key in ("x", "y", "w", "h"):
        v = rect.get(key)
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(
                f"{name}.{key}: expected int, got {type(v).__name__} ({v!r})"
            )
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    if w <= 0 or h <= 0:
        raise ValueError(f"{name}: w and h must be positive, got w={w}, h={h}")
    if w % 2 != 0 or h % 2 != 0:
        raise ValueError(f"{name}: w and h must be even, got w={w}, h={h}")
    if x % 2 != 0 or y % 2 != 0:
        raise ValueError(f"{name}: x and y must be even, got x={x}, y={y}")
    if x < 0 or y < 0:
        raise ValueError(f"{name}: x and y must be >= 0, got x={x}, y={y}")
    if x + w > src_w or y + h > src_h:
        raise ValueError(
            f"{name}: rectangle ({x},{y},{w},{h}) extends outside source "
            f"({src_w}×{src_h})"
        )
    return {"x": x, "y": y, "w": w, "h": h}


def plan_reaction_layout(
    content_rect: Any,
    face_rect: Any,
    src_w: int,
    src_h: int,
    *,
    face_pct: float = 0.40,
    hud: Sequence[dict] | None = None,
    chat: dict | None = None,
) -> dict:
    """Build an explicit static game_top_face_bottom plan.

    Rectangles are caller-supplied SOURCE pixels — not detected, not inferred.
    Both are preserved exactly in the returned plan. game_content_fit=True
    scales the game rectangle proportionally inside its band over a blurred fill
    of that same rectangle; no browser chrome or UI outside game_rect enters
    either the foreground or the background.

    Caller supplied regions; content outside them is not promised preserved.
    """
    _validate_src(src_w, src_h)
    game_rect = _validate_rect(content_rect, src_w, src_h, "content_rect")
    face_r = _validate_rect(face_rect, src_w, src_h, "face_rect")

    face_pct = float(face_pct)
    if not (FACE_PCT_MIN <= face_pct <= FACE_PCT_MAX):
        raise ValueError(
            f"face_pct must be between {FACE_PCT_MIN} and {FACE_PCT_MAX}, "
            f"got {face_pct}"
        )

    safe = _safe_zones(
        "game_top_face_bottom",
        face_r, game_rect,
        chat, list(hud or []),
        face_pct,
        game_content_fit=True,
    )

    return {
        "layout": "game_top_face_bottom",
        "game_rect": game_rect,
        "face_rect": face_r,
        "face_pct": face_pct,
        "game_content_fit": True,
        "src_w": src_w,
        "src_h": src_h,
        "note": "caller supplied regions; content outside them is not promised preserved.",
        "safe_zones": safe,
    }
