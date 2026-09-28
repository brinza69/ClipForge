"""
ClipForge — AI Stream Clipper: reaction layout binding and validation.

Owns source-version computation, face-aspect guard, and binding validation
for the ordinary editor/worker render path. The pure builder
(plan_reaction_layout) does not require a binding; this module is about the
HTTP and render paths where an unvalidated crop must never silently render.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

__all__ = [
    "BINDING_SCHEMA",
    "FACE_ASPECT_W",
    "FACE_ASPECT_H",
    "compute_source_version",
    "validate_face_aspect",
    "validate_binding",
]

BINDING_SCHEMA = "clipper_reaction_binding_v1"

# UI locks the face selection to 1080:768. The PRP mandates exactly this ratio.
FACE_ASPECT_W = 1080
FACE_ASPECT_H = 768
_FACE_ASPECT = FACE_ASPECT_W / FACE_ASPECT_H  # 1.40625


def compute_source_version(path: str, src_w: int, src_h: int) -> str:
    """Opaque replacement guard for a source file. NOT a content hash.

    Covers: resolved path, file size, mtime_ns, and declared source dimensions.
    A re-download produces a different token; a re-read of the identical file
    produces the same one. Truncated to 32 hex chars — enough for a guard, not
    so long it crowds a JSON response.
    """
    p = Path(path)
    st = p.stat()
    raw = f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}|{src_w}|{src_h}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def validate_face_aspect(face_w: int, face_h: int) -> None:
    """Refuse |face.w - face.h * 1080/768| > 2 source px.

    Stops arbitrary rectangles from stretching a face crop. The UI locks
    the aspect on selection, so this is a guard against a malformed request
    or a stale payload — not a common path.
    """
    if face_h <= 0:
        raise ValueError(f"face_h must be positive, got {face_h}")
    expected_w = face_h * _FACE_ASPECT
    deviation = abs(face_w - expected_w)
    if deviation > 2:
        raise ValueError(
            f"face_rect aspect must be {FACE_ASPECT_W}:{FACE_ASPECT_H}; "
            f"face_w={face_w} deviates {deviation:.2f}px from "
            f"expected {expected_w:.2f}px"
        )


def _require_finite_window(start: Any, end: Any, label: str) -> tuple[float, float]:
    """Return (start, end) as finite floats or raise RuntimeError.

    Explicitly rejects booleans (True/False), strings such as 'NaN' or
    'Infinity', and any non-finite numeric value.
    """
    for name, v in ((f"{label}_start", start), (f"{label}_end", end)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise RuntimeError(
                f"{name} must be a number, got {type(v).__name__}: {v!r}"
            )
        if not math.isfinite(v):
            raise RuntimeError(f"{name} must be finite, got {v!r}")
    s, e = float(start), float(end)
    if s < 0:
        raise RuntimeError(f"{label}_start must be >= 0, got {s}")
    if e <= s:
        raise RuntimeError(f"{label}_end ({e}) must be > {label}_start ({s})")
    return s, e


def validate_binding(
    plan: Any,
    source_path: str,
    project_w: int,
    project_h: int,
    clip_start: float,
    clip_end: float,
) -> None:
    """Validate a stored reaction_binding against the current source state.

    A clip trimmed to a SHORTER window inside the saved interval is valid.
    The following all raise RuntimeError with an actionable message:
      - not a dict / no reaction_binding key
      - unknown schema
      - wrong or missing source dimensions
      - stale source version (file replaced since save)
      - non-finite or non-numeric saved window
      - clip window expanded outside the saved interval
      - plan geometry inconsistent with binding (layout, face_pct, src dims, rects)
      - missing or invalid face aspect

    Never silently falls back to a different crop.
    """
    if not isinstance(plan, dict):
        raise RuntimeError("not a reaction layout plan")

    binding = plan.get("reaction_binding")
    if not isinstance(binding, dict):
        raise RuntimeError(
            "layout plan has no reaction_binding — "
            "an unbound game_content_fit plan cannot render in the ordinary path"
        )

    schema = binding.get("schema")
    if schema != BINDING_SCHEMA:
        raise RuntimeError(
            f"unknown reaction binding schema: {schema!r} "
            f"(expected {BINDING_SCHEMA!r})"
        )

    # --- source dimensions -------------------------------------------------
    saved_w = binding.get("src_w")
    saved_h = binding.get("src_h")
    if (isinstance(saved_w, bool) or not isinstance(saved_w, int) or
            isinstance(saved_h, bool) or not isinstance(saved_h, int)):
        raise RuntimeError(
            "reaction binding src_w/src_h must be integers"
        )
    if saved_w != project_w or saved_h != project_h:
        raise RuntimeError(
            f"reaction binding was saved for {saved_w}×{saved_h} source; "
            f"current project source is {project_w}×{project_h}"
        )

    # --- source file identity ----------------------------------------------
    current_version = compute_source_version(source_path, project_w, project_h)
    saved_version = binding.get("source_version")
    if saved_version != current_version:
        raise RuntimeError(
            "source file has changed since the reaction layout was saved — "
            "re-open the editor to record a new crop"
        )

    # --- saved time window (NaN / Infinity guard) --------------------------
    try:
        saved_start, saved_end = _require_finite_window(
            binding.get("source_start"), binding.get("source_end"), "source"
        )
    except RuntimeError as exc:
        raise RuntimeError(f"reaction binding has invalid window: {exc}") from exc

    # --- current clip interval vs saved interval ---------------------------
    try:
        clip_start, clip_end = _require_finite_window(clip_start, clip_end, "clip")
    except RuntimeError as exc:
        raise RuntimeError(f"clip window invalid: {exc}") from exc
    eps = 1e-4
    if clip_start < saved_start - eps or clip_end > saved_end + eps:
        raise RuntimeError(
            f"clip interval [{clip_start:.3f}, {clip_end:.3f}] extends outside "
            f"the reaction binding interval [{saved_start:.3f}, {saved_end:.3f}]"
        )

    # --- plan geometry cross-check against binding -------------------------
    plan_layout = plan.get("layout")
    if plan_layout != "game_top_face_bottom":
        raise RuntimeError(
            f"reaction plan layout must be 'game_top_face_bottom', "
            f"got {plan_layout!r}"
        )

    plan_face_pct = plan.get("face_pct")
    if (isinstance(plan_face_pct, bool) or
            not isinstance(plan_face_pct, (int, float)) or
            not math.isfinite(float(plan_face_pct)) or
            abs(float(plan_face_pct) - 0.40) > 1e-4):
        raise RuntimeError(
            f"reaction plan face_pct must be 0.40, got {plan_face_pct!r}"
        )

    plan_w = plan.get("src_w")
    plan_h = plan.get("src_h")
    if (isinstance(plan_w, bool) or not isinstance(plan_w, int) or
            isinstance(plan_h, bool) or not isinstance(plan_h, int)):
        raise RuntimeError(
            f"plan src_w/src_h must be integers, "
            f"got {type(plan_w).__name__}/{type(plan_h).__name__}"
        )
    if plan_w != saved_w or plan_h != saved_h:
        raise RuntimeError(
            f"plan source dimensions {plan_w}×{plan_h} do not match "
            f"binding {saved_w}×{saved_h}"
        )

    # The generic clip PATCH can edit a plan too. Validate all source pixels
    # and keep-outs against the same builder that the reaction editor uses.
    from services.clipper.reaction_layout import plan_reaction_layout
    try:
        canonical = plan_reaction_layout(
            plan.get("game_rect"), plan.get("face_rect"), saved_w, saved_h)
    except ValueError as exc:
        raise RuntimeError(
            f"reaction plan geometry invalid: {exc}"
        ) from exc
    if plan.get("safe_zones") != canonical["safe_zones"]:
        raise RuntimeError("reaction caption keep-outs no longer match the layout; re-save it in the editor")

    # --- face aspect (last: most specific) ---------------------------------
    try:
        face_rect = plan.get("face_rect")
        if not isinstance(face_rect, dict):
            raise ValueError("face_rect missing")
        validate_face_aspect(int(face_rect["w"]), int(face_rect["h"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"reaction binding face geometry invalid: {exc}"
        ) from exc
