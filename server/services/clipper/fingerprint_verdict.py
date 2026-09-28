"""The fingerprint verdict: whether an export sidecar's `input_fingerprint` still describes the record.

Split out of `edit_quality` at its 500-line limit (SCB2r, codex-verdict-next-22 Q2) and re-exported
there, so `edit_quality.fingerprint_verdict`, `fingerprint_status` and the FINGERPRINT_* states keep
working for every caller. Moved whole: the states, the contract and their comments are unchanged.
"""

from __future__ import annotations

from typing import Any

from services.clipper.render_input import input_fingerprint

# `edit_quality.UNAVAILABLE`, the same string by value, spelled here so this module does not import
# the one that re-exports it.
UNAVAILABLE = "unavailable"

FINGERPRINT_VALID = "valid"
FINGERPRINT_MISMATCH = "mismatch"
#: The digest recomputes under v1, which does not cover the caption policy. It
#: is a real result and a bounded one, and it needs its own name: calling it
#: `valid` would let a record that cannot answer the policy question stand in
#: for one that can.
FINGERPRINT_VALID_V1 = "valid_v1_policy_not_covered"
#: The record declares a schema this code does not implement. No other version
#: is tried — "try them all and take whichever matches" is a route around the
#: policy check for anything that claims to be something else.
FINGERPRINT_UNKNOWN_SCHEMA = "unknown_fingerprint_schema"
#: A v3 record whose source-treatment identity is missing or malformed. Its own
#: row, never `none`, never valid — even when the digest recomputes over it.
FINGERPRINT_TREATMENT_UNREADABLE = "source_treatment_unreadable"


def fingerprint_verdict(sidecar: Any) -> dict:
    """`{state, schema, assumed_legacy, covers_caption_policy}`.

    THE CONTRACT, and each row of it exists because the obvious shortcut is
    wrong in a way that hides:

      * a record with no `fingerprint_schema` is read with the v1 formula
        exactly, and `assumed_legacy` says so — an assumption applied silently
        makes a green row unreadable;
      * a declared v1 is read with v1 and carries the same limit;
      * a declared v2 is read with v2 and ONLY v2. A v2 mismatch stays a
        mismatch even where the v1 formula would match, because a fallback is
        exactly the route by which the policy check gets skipped;
      * a schema nobody implements is refused. Not tried against the versions
        that do exist.

    Recomputing is what makes any of it a check: copying the stored digest into
    the report would let a plan edited after the render carry a stale
    fingerprint and pass.
    """
    from services.clipper.render_input import (FINGERPRINT_SCHEMA_V2, FINGERPRINT_SCHEMA_V3,
                                               TREATMENT_UNREADABLE, declared_schema,
                                               treatment_identity_state)

    out = {"state": UNAVAILABLE, "schema": None, "assumed_legacy": False,
           "covers_caption_policy": False}
    if not isinstance(sidecar, dict):
        return out
    schema, assumed = declared_schema(sidecar)
    out["schema"], out["assumed_legacy"] = schema, assumed
    if schema is None:
        out["state"] = FINGERPRINT_UNKNOWN_SCHEMA
        out["declared"] = sidecar.get("fingerprint_schema")
        return out
    out["covers_caption_policy"] = schema in (FINGERPRINT_SCHEMA_V2, FINGERPRINT_SCHEMA_V3)
    if schema == FINGERPRINT_SCHEMA_V3 and treatment_identity_state(sidecar) == TREATMENT_UNREADABLE:
        out["state"] = FINGERPRINT_TREATMENT_UNREADABLE
        return out
    stored = sidecar.get("input_fingerprint")
    if not isinstance(stored, str) or not stored:
        return out
    if stored != input_fingerprint(sidecar, schema=schema):
        out["state"] = FINGERPRINT_MISMATCH
        return out
    out["state"] = (FINGERPRINT_VALID if out["covers_caption_policy"]
                    else FINGERPRINT_VALID_V1)
    return out


def fingerprint_status(sidecar: dict) -> str:
    """`fingerprint_verdict`'s state alone, for the reports that print one word.

    Kept because six call sites want a string, and pointed at the verdict so
    there is one implementation of the contract rather than two.
    """
    return fingerprint_verdict(sidecar)["state"]
