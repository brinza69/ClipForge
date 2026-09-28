"""The treatment manifest, checked — and the fingerprint's semantic projection.

TWO IDENTITIES, KEPT APART ON PURPOSE (codex-verdict-next-10 §1).

`manifest_sha256` is the hash of the manifest FILE. The manifest names its
attempt (`job_id`, `nonce`), the patch's path in that attempt's scratch dir and
the provenance labels, so two attempts of the same recipe always differ here.
That is what it is for: `render_record` keeps it to bind the attempt to exactly
what its encode consumed, and binding another attempt's manifest is refused
even when the recipe is identical.

`treatment_identity_sha256` is the digest of `fingerprint_identity()`: what
changes the PICTURE — source, mask, glyphs, the treated frames and their
treatments, params, window/clock offset, the overlay and the patch's content —
and nothing that only says who made it, when, where, or how a hash was
obtained. The same recipe retried in another scratch dir gives the same
digest; a different frame, treatment or param gives another. Putting
`manifest_sha256` into the fingerprint would have hidden the paths it
contains inside a hash (the contradiction next-10 §1 found in v2 §5–§6).

`decided_by` is not in it either: confirming `none` does not change a pixel
(SC-addendum-v2 §5). An inactive treatment is exactly `NONE_IDENTITY`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.clipper.source_treatment import (IDENTITY_SCHEMA, MANIFEST_SCHEMA, NONE,
                                               SourceTreatmentRefused, canonical_bytes,
                                               sha256_bytes)
from services.clipper.source_treatment_mask import IDENTITY_STREAM_KEYS

NONE_IDENTITY: dict[str, str] = {"schema": IDENTITY_SCHEMA, "treatment": NONE}

#: The patch's content, without its path.
PATCH_SEMANTIC_KEYS: tuple[str, ...] = ("sha256", "bytes", "frames", "pix_fmt", "w", "h", "tags",
                                        "spare_trailing_frame")


def treated_frames(resolved: dict) -> list[dict]:
    """Run-length over the whole window: the exact treated set, untreated runs null."""
    k_first, end = resolved["window"]["k_first"], resolved["window"]["k_last"] + 1
    out, at = [], k_first
    for seg in sorted(resolved["per_line"], key=lambda s: s["k_from"]):
        if seg["k_from"] > at:
            out.append({"k_from": at, "k_to": seg["k_from"], "treatment": None, "line_id": None})
        out.append({"k_from": seg["k_from"], "k_to": seg["k_to"], "treatment": seg["treatment"],
                    "line_id": seg["line_id"]})
        at = seg["k_to"]
    if at < end:
        out.append({"k_from": at, "k_to": end, "treatment": None, "line_id": None})
    return out


def verify_manifest(path: str | Path, *, expected_sha256: Any, attempt: dict,
                    scratch_dir: str | Path, resolved: dict, mask: dict) -> dict:
    """`{manifest_sha256, identity, identity_sha256}`, or `SourceTreatmentRefused`.

    `expected_sha256` is the one the decision carried; `attempt` is THIS
    attempt's `{job_id, nonce}` and `scratch_dir` its own directory; `resolved`
    is `source_treatment.resolve()`'s active result; `mask` is a mask loaded
    NOW (its glyph PNGs were re-hashed from their bytes by that load)."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise SourceTreatmentRefused("manifest_missing", str(e)) from None
    sha = sha256_bytes(data)
    if sha != expected_sha256:
        raise SourceTreatmentRefused("manifest_hash_mismatch",
                                     f"manifest is {sha}, the decision carried {expected_sha256}")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SourceTreatmentRefused("manifest_invalid", str(e)) from None
    if (not isinstance(doc, dict) or doc.get("schema") != MANIFEST_SCHEMA
            or canonical_bytes(doc) != data):
        raise SourceTreatmentRefused("manifest_invalid", "not a canonical manifest")
    if doc.get("attempt") != attempt:
        raise SourceTreatmentRefused("manifest_of_another_attempt",
                                     f"manifest is {doc.get('attempt')}, this attempt is {attempt}")
    if doc.get("mask") != {"sha256": mask["sha256"]} or resolved["mask_sha256"] != mask["sha256"]:
        raise SourceTreatmentRefused("manifest_mask_mismatch", f"manifest mask {doc.get('mask')}")
    src, msrc = doc.get("source") if isinstance(doc.get("source"), dict) else {}, mask["doc"]["source"]
    stream = src.get("stream") if isinstance(src.get("stream"), dict) else {}
    if src.get("sha256") != msrc["sha256"] or any(stream.get(k) != msrc["stream"].get(k)
                                                  for k in IDENTITY_STREAM_KEYS):
        raise SourceTreatmentRefused("mask_source_mismatch", "the manifest's source is not the mask's")
    want_glyphs = [{"line_id": lid, "png_sha256": line["glyph_png"]["sha256"]}
                   for lid, line in sorted(mask["lines"].items())]
    if doc.get("glyphs") != want_glyphs:
        raise SourceTreatmentRefused("glyph_hash_mismatch", "the manifest's glyphs are not the mask's")
    if doc.get("params") != {"version": resolved["params_version"], "values": resolved["params"]}:
        raise SourceTreatmentRefused("manifest_params_mismatch", f"manifest params {doc.get('params')}")
    if doc.get("window") != resolved["window"] or doc.get("frames") != treated_frames(resolved):
        raise SourceTreatmentRefused("manifest_frames_mismatch",
                                     "the manifest's treated frames are not the resolved ones")
    patch = doc.get("patch") if isinstance(doc.get("patch"), dict) else {}
    patch_file = Path(str(patch.get("file")))
    if not patch_file.resolve().is_relative_to(Path(scratch_dir).resolve()):
        raise SourceTreatmentRefused("manifest_of_another_attempt",
                                     f"patch {patch_file} is not in this attempt's scratch dir")
    try:
        patch_bytes = patch_file.read_bytes()
    except OSError as e:
        raise SourceTreatmentRefused("patch_missing", str(e)) from None
    if sha256_bytes(patch_bytes) != patch.get("sha256") or len(patch_bytes) != patch.get("bytes"):
        raise SourceTreatmentRefused("patch_hash_mismatch", f"{patch_file} is not the manifest's patch")
    identity = fingerprint_identity(resolved, doc)
    return {"manifest_sha256": sha, "identity": identity,
            "identity_sha256": treatment_identity_sha256(identity)}


def fingerprint_identity(resolved: dict, manifest: dict | None = None) -> dict:
    """The semantic projection the v3 fingerprint carries. No job, nonce, path,
    verification time, cache/full-hash label or manifest file hash."""
    if not resolved.get("active"):
        return dict(NONE_IDENTITY)
    if not isinstance(manifest, dict):
        raise SourceTreatmentRefused("manifest_missing", "an active treatment has no manifest")
    src = manifest["source"]
    return {"schema": IDENTITY_SCHEMA, "treatment": resolved["treatment"],
            "per_line": resolved["per_line"],
            "source": {"sha256": src["sha256"],
                       "stream": {k: src["stream"].get(k) for k in IDENTITY_STREAM_KEYS}},
            "mask_sha256": manifest["mask"]["sha256"], "glyphs": manifest["glyphs"],
            "params": manifest["params"], "window": manifest["window"],
            "pts_offset": manifest["pts_offset"], "frames": manifest["frames"],
            "overlay_filter": manifest["overlay_filter"],
            "patch": {k: manifest["patch"].get(k) for k in PATCH_SEMANTIC_KEYS}}


def treatment_identity_sha256(identity: dict) -> str:
    """Digest of the projection — named apart from `manifest_sha256` on purpose.

    Not a cache key: it includes the resulting patch's hash. A batch-2 patch
    cache looks up by a key derived from the frozen inputs plus the
    params/algorithm version, then checks the hit through its manifest and
    patch hash, and only then computes this digest (codex-verdict-next-14 §1).
    `generator` stays outside it, in the full provenance (next-13 §7)."""
    return sha256_bytes(canonical_bytes(identity))
