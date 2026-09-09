"""The render recipe, and the digest taken over it.

Split out of `edit_quality.py` when that file crossed 500 lines. It is a
separate subject anyway: everything here is about WHAT an export was made from,
while the rest of the audit is about what the edit turned out like. Re-exported
from `edit_quality` so callers did not have to move.

The one definition, used by both ends. `clipper_render_jobs` computes the digest
from the sidecar it is about to write; `audit_clipper_exports` recomputes it
from the file on disk and compares. Two implementations would drift, and a
fingerprint only means anything while both ends agree on what it covers.

TWO SCHEMAS, AND THE RECORD SAYS WHICH. v1 does not cover the caption policy,
so two renders of the same plan — one that burned our layer and one that
suppressed it — fingerprint identically. v2 covers the resolved policy and the
CONTENT of the `.ass` that was burned, because both change the image.

    record                       verified with              conclusion allowed
    legacy, no schema field      the v1 formula, exactly    recipe v1 matches;
                                                            policy NOT covered
    v1 declared                  the v1 formula only        the same limit
    v2 declared                  the v2 formula only        the v2 fields hold
    anything else                refused                    no other version

THE RULES THAT MAKE THAT A CONTRACT RATHER THAN A LIST. A v2 mismatch stays a
mismatch even where the v1 formula would match — otherwise every export gets a
route around the policy check by claiming to be older than it is. Nothing tries
both formulas looking for one that fits. And the legacy assumption is REPORTED,
not silently applied, so a reader of a green row can see which question it
answered.

WHAT DOES NOT GO IN. The command line, the temporary paths, and anything else
that is a detail of one run: those change between two renders of the same recipe
and would make the digest say nothing. The `.ass` CONTENT does go in, because it
is an input that changes the delivered picture. `render_record` as a whole does
not — it describes the execution and the output.

The 58 stored sidecars are not touched. They validate under v1 with the
limitation visible, which keeps the old evidence as evidence rather than turning
it into either corruption or a stronger claim than it ever supported.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from services.clipper import reasoning_trace

__all__ = ["FINGERPRINT_SCHEMA", "FINGERPRINT_SCHEMA_V1", "FINGERPRINT_SCHEMA_V2",
           "FINGERPRINT_KEYS", "FINGERPRINT_KEYS_V1", "FINGERPRINT_KEYS_V2",
           "SCHEMAS", "LEGACY", "declared_schema", "render_input",
           "input_fingerprint", "caption_identity"]

#: What the render fingerprint is taken over. The recipe — everything the
#: export was made FROM — and nothing that only labels the result: no title, no
#: scores, no review, no ids, because two reruns of the same recipe must
#: fingerprint the same or the digest says nothing.
#:
#: NOT a claim that this is everything that changes the image. Since the shared
#: export path (2026-09-09), output dimensions are explicit in `render` and so
#: covered by this projection. Older records retain their original, smaller
#: render dictionary; their missing dimensions are never backfilled.
FINGERPRINT_SCHEMA_V1 = "clipper_render_input_v1"
FINGERPRINT_KEYS_V1: tuple[str, ...] = (
    "source", "source_start", "source_end", "layout_plan", "dynamic_plan",
    "caption_plan", "caption_y", "drop_spans", "render_version", "render",
)

#: v1 plus the two things that decide whether there is text on the screen and
#: what it says. `caption_identity` is a PROJECTION, not the raw records: the
#: resolved policy and the digest of the `.ass` the filter actually used, with
#: the paths and the run details left out.
FINGERPRINT_SCHEMA_V2 = "clipper_render_input_v2"
FINGERPRINT_KEYS_V2: tuple[str, ...] = FINGERPRINT_KEYS_V1 + ("caption_identity",)

SCHEMAS: tuple[str, ...] = (FINGERPRINT_SCHEMA_V1, FINGERPRINT_SCHEMA_V2)
#: A record with no `fingerprint_schema` field. It is an ASSUMPTION and it is
#: reported as one — every sidecar written before 2026-09-05 is one of these.
LEGACY = "legacy_v1"

#: The names the rest of the codebase still imports. They mean v1 because that
#: is what a caller who did not ask has always got.
FINGERPRINT_SCHEMA = FINGERPRINT_SCHEMA_V1
FINGERPRINT_KEYS = FINGERPRINT_KEYS_V1

_KEYS_FOR: dict[str, tuple[str, ...]] = {
    FINGERPRINT_SCHEMA_V1: FINGERPRINT_KEYS_V1,
    FINGERPRINT_SCHEMA_V2: FINGERPRINT_KEYS_V2,
}


def declared_schema(sidecar: Any) -> tuple[str | None, bool]:
    """`(schema, was_assumed)` — which formula this record must be read with.

    `(None, False)` means the field is present and is not a schema this code
    knows. That is a refusal, and the caller may not fall back to another
    version: "try v1, then v2, take whichever matches" is a route around the
    policy check for any record that claims to be something else.
    """
    if not isinstance(sidecar, dict):
        return None, False
    got = sidecar.get("fingerprint_schema")
    if got is None:
        # No field at all. Written before v2 existed, so it is v1 — assumed,
        # and the flag is what makes the assumption visible in the result.
        return FINGERPRINT_SCHEMA_V1, True
    if isinstance(got, str) and got in _KEYS_FOR:
        return got, False
    return None, False


def caption_identity(sidecar: Any) -> dict:
    """What the caption decision contributed to the delivered picture.

    Three things and deliberately not the records they come from: the action
    and who decided it, the per-interval decisions once those exist, and the
    digest of the `.ass` the filter actually used. A path is not content — two
    renders of one recipe write the subtitle file to two temporary names — and
    the argv is a detail of a run.

    `ass_sha256` is `None` when no filter was used, which is a DIFFERENT record
    from a filter whose file could not be read: the second leaves `ass_refused`
    set, and a digest that ignored it would let an unreadable subtitle file
    fingerprint the same as no subtitle at all.
    """
    if not isinstance(sidecar, dict):
        return {"policy": None, "intervals": None, "ass_sha256": None,
                "ass_refused": None}
    policy = sidecar.get("caption_policy")
    record = sidecar.get("render_record")
    record = record if isinstance(record, dict) else {}
    return {
        "policy": ({"action": policy.get("action"),
                    "decided_by": policy.get("decided_by")}
                   if isinstance(policy, dict) else None),
        # Batch A's second half. `None` until per-interval decisions exist, and
        # it is a key rather than an omission so that adding them changes the
        # digest of everything they apply to, which is the point.
        "intervals": sidecar.get("caption_intervals"),
        "caption_filter": record.get("caption_filter"),
        "ass_sha256": record.get("ass_sha256"),
        "ass_refused": record.get("ass_refused"),
        "ass_events": (record.get("ass_events") or {}).get("intervals")
        if isinstance(record.get("ass_events"), dict) else None,
    }


def render_input(sidecar: dict, *, schema: str = FINGERPRINT_SCHEMA_V1) -> dict:
    """The recipe half of a sidecar, as the fingerprint sees it.

    The ONE definition, used by the export that writes the digest and by the
    audit that rechecks it. Two implementations would drift, and a fingerprint
    only means anything while both ends agree on what it covers.
    """
    keys = _KEYS_FOR.get(schema)
    if keys is None:
        raise ValueError(f"unknown fingerprint schema: {schema!r}")
    payload: dict[str, Any] = {"schema": schema}
    for key in keys:
        payload[key] = (caption_identity(sidecar) if key == "caption_identity"
                        else sidecar.get(key))
    return payload


def input_fingerprint(sidecar: dict, *,
                      schema: str = FINGERPRINT_SCHEMA_V1) -> str:
    """The digest of `render_input`, using the canonical function from
    `reasoning_trace` — not a second algorithm that would have to be kept in
    step with it.

    v2 falls back to a local digest ONLY if the canonical one refuses the
    payload, which it can: `caption_identity` may hold values `reasoning_trace`
    was never asked to serialise. The fallback is deterministic and covers the
    same object, and it is not silent — the digest is prefixed so a reader can
    never mistake one for the other.
    """
    payload = render_input(sidecar, schema=schema)
    try:
        return reasoning_trace.fingerprint(payload)
    except Exception:
        return "alt:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str)
            .encode("utf-8", "replace")).hexdigest()
