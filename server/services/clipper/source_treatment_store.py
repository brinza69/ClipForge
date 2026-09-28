"""SC3 (codex-verdict-next-33 §3): a project's validated source-caption masks, what a clip may use, and the
treatment a render of that clip executes.

    source_caption_masks/<sha256>/<sha256>.json  a mask (canonical bytes, sha = name) and ITS glyph PNGs —
                                             one directory per mask, so two masks never share a glyph path
                                             (next-34 R2: a revised mask reusing `g/L1.png` broke the first)
    source_caption_masks/params/<v>.json     the ONE versioned params record (+ .sha256), SC batch 2's format
    source_caption_masks/source_identity.json  the full-hash identity the import recorded (reuse, not proof)

Masks are imported OFFLINE (`scripts/import_source_caption_mask.py`), bound by hash; nothing a client sends is
a mask's approval. Every use re-validates: `load_mask` against the clip's CURRENT bounds and the source's
identity, the params record against its `.sha256`, and the executor's own shape check.

BLUR ONLY. Erase stays in the executor for the probes, and is never offered, stored or published here
(next-33 §1): a stored erase — or an erase segment hidden in an override — is refused, not rendered.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from services.clipper import caption_policy, storage
from services.clipper import source_treatment as st
from services.clipper.source_treatment import BLUR, HUMAN, NONE, SourceTreatmentRefused

STORE = "source_caption_masks"
IDENTITY_FILE = "source_identity.json"
#: What a person may choose (next-33 §3). Erase is implemented and NOT offered.
OFFERED = (NONE, BLUR)
_SHA = re.compile(r"^[0-9a-f]{64}$")


def store_dir(project_id: str) -> Path:
    return storage.safe_join(project_id, STORE)


def mask_dir(project_id: str, sha: Any) -> Path:
    if not isinstance(sha, str) or not _SHA.match(sha):
        raise SourceTreatmentRefused("mask_missing", f"{sha!r} is not a sha256")
    return storage.safe_join(project_id, STORE, sha)


def mask_path(project_id: str, sha: Any) -> Path:
    return mask_dir(project_id, sha) / f"{sha}.json"


def params_path(project_id: str) -> Path:
    """The store's one params record. None, or more than one, is a refusal, never a pick."""
    found = sorted((store_dir(project_id) / "params").glob("*.json"))
    if len(found) != 1:
        raise SourceTreatmentRefused("params_missing", f"{len(found)} params records in the store, need 1")
    return found[0]


def identity_reuse(project_id: str) -> dict | None:
    try:
        doc = json.loads((store_dir(project_id) / IDENTITY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def masks_for_clip(project_id: str, clip_id: str) -> list[str]:
    """The stored masks that name this clip, by sha. Named is not valid: `validate` decides that."""
    d = store_dir(project_id)
    out = []
    for p in sorted(d.glob("*/*.json")) if d.is_dir() else []:
        if not _SHA.match(p.stem) or p.parent.name != p.stem:
            continue
        try:
            doc = json.loads(p.read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("clip"), dict) and doc["clip"].get("clip_id") == clip_id:
            out.append(p.stem)
    return out


def _burned(clip: Any, project: Any) -> Any:
    from services.clipper.source_treatment_render import _burned as burned
    return burned(clip, project)


def static_reason(clip: Any, project: Any) -> str | None:
    """Why this clip would render on the STATIC path, where no treatment executes — known before the render
    (next-34 R3). The same two tests `_decide_render` makes; a planner failure that falls back to static
    is still refused at the render by the gate, never delivered untreated."""
    from config import settings

    plan = getattr(clip, "layout_plan", None)
    if isinstance(plan, dict) and plan.get("game_content_fit") is True:
        return "a reaction framing renders on the static path"
    cfg = getattr(project, "clipper_settings", None) or {}
    if not bool(cfg.get("dynamic_edit", settings.clipper_dynamic_edit)):
        return "the project renders on the static path (dynamic_edit is off)"
    return None


def validate(clip: Any, project: Any, src: str, sha: str) -> dict:
    """The blur treatment this mask gives the clip NOW: `{resolved, inputs}`, or SourceTreatmentRefused."""
    why = static_reason(clip, project)
    if why:
        raise SourceTreatmentRefused("static_path_unsupported", why)
    from services.clipper import source_treatment_render as treat
    from services.clipper.proxy_provenance import media_identity
    from services.clipper.source_treatment_mask import load_mask

    reuse = identity_reuse(project.id)
    identity = media_identity(src, reuse)
    path = mask_path(project.id, sha)
    mask = load_mask(path, source_identity=identity, clip_id=clip.id,
                     clip_start=clip.start_time, clip_end=clip.end_time)
    ppath = params_path(project.id)
    params = treat.load_params(ppath)
    setting = {"treatment": BLUR, "decided_by": HUMAN, "mask_sha256": sha}
    resolved = st.resolve(None, setting, mask, {"version": params["version"], "values": params["values"]})
    if resolved.get("state") != "resolved":
        raise SourceTreatmentRefused(resolved.get("reason", "setting_invalid"), str(resolved.get("detail", "")))
    treat._executable(resolved["params"], mask, resolved["per_line"])
    return {"resolved": resolved,
            "inputs": {"project": None, "clip": setting, "mask_path": str(path), "params_path": str(ppath),
                       "source_identity_reuse": reuse}}


def availability(clip: Any, project: Any, src: str | None) -> dict:
    """Whether blur can be chosen for this clip, which masks allow it, and why not. Read by the UI."""
    masks = []
    for sha in masks_for_clip(project.id, clip.id):
        try:
            if src is None:
                raise SourceTreatmentRefused("mask_source_mismatch", "the source is not on disk")
            validate(clip, project, src, sha)
            masks.append({"mask_sha256": sha, "ok": True, "reason": None})
        except SourceTreatmentRefused as r:
            masks.append({"mask_sha256": sha, "ok": False, "reason": r.reason, "detail": r.detail})
    ok = [m["mask_sha256"] for m in masks if m["ok"]]
    return {"blur": {"available": bool(ok), "mask_sha256": ok[0] if len(ok) == 1 else None,
                     "reason": None if ok else (masks[0]["reason"] if masks else "no_validated_mask"),
                     "masks": masks}}


def for_render(clip: Any, project: Any, caption_policy_decision: dict, src: str) -> dict:
    """The decision keys a render of this clip needs, `{}` when nothing is stored. Raises
    SourceTreatmentRefused BEFORE any `.ass` is written: a stored treatment that is not offered or no
    longer executes, or ClipForge's layer burned by a person's choice over source text that stays."""
    setting = getattr(clip, "source_caption_treatment", None)
    burns = caption_policy_decision.get("action") == caption_policy.BURN
    chosen_burn = getattr(clip, "caption_layer", None) == caption_policy.BURN
    out: dict = {}
    if setting is not None:
        t = setting.get("treatment") if isinstance(setting, dict) else None
        if t not in OFFERED:
            raise SourceTreatmentRefused("treatment_not_offered", f"{t!r}: only none and blur are offered")
        if t == BLUR:
            got = validate(clip, project, src, setting.get("mask_sha256"))
            if got["inputs"]["clip"] != setting:
                raise SourceTreatmentRefused("setting_invalid", "the stored treatment is not a plain blur choice")
            out = {"source_treatment": got["resolved"], "source_treatment_inputs": got["inputs"]}
        else:
            out = {"source_treatment": st.resolve(None, setting, None, None)}
    active = bool(out.get("source_treatment", {}).get("active"))
    # Next-33 §3: a person's burn over a source whose text stays — declared, or not known — needs an
    # executable treatment. The gate already refuses a declared one; this also covers "not known".
    if burns and chosen_burn and not active and _burned(clip, project) is not False:
        raise SourceTreatmentRefused("burn_over_untreated_source_captions",
                                     "the layer was chosen to burn and the source's text is not treated")
    return out
