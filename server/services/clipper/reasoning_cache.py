"""Versioned envelopes for reasoning artifacts.

A JSON file existing on disk is not evidence that it answers the question the
current run is asking.  Every cacheable reasoning artifact therefore carries:

* the artifact name and envelope version;
* separate source and transcript fingerprints;
* the exact artifact-specific inputs (service, prompt/model/temperature,
  chunking, parameters and upstream artifacts);
* a fingerprint of both those inputs and the stored payload.

The storage layer deliberately stays generic.  Only callers that understand a
reasoning artifact may validate and reuse it; wrapping every JSON read would
silently change unrelated artifacts and would make legacy files look trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


ENVELOPE_VERSION = "reasoning_artifact_v1"

# Closed because accepting a typo writes a perfectly valid file that no reader
# will ever find.  Judge joins this list before its cache is materialised so the
# common contract cannot later grow a private one-off envelope.
ARTIFACTS: frozenset[str] = frozenset({
    "atoms", "promises", "threads", "episodes", "anchors", "judge",
    "segment_types",
})

HIT = "hit"
LEGACY_OR_MALFORMED = "legacy_or_malformed"
MALFORMED_ENVELOPE = "malformed_envelope"
WRONG_ARTIFACT = "wrong_artifact"
INPUT_CHANGED = "input_changed"
DATA_CHANGED = "data_changed"


@dataclass(frozen=True)
class CacheRead:
    """The payload or the named reason it was not trusted."""

    hit: bool
    data: Any = None
    reason: str = LEGACY_OR_MALFORMED


def fingerprint(payload: Any) -> str:
    """Full SHA-256 of a strict JSON projection, stable across dict order.

    A cache identity must not manufacture a representation for an object that
    the artifact writer cannot persist.  ``default=str`` would make sets,
    paths and arbitrary objects look cacheable, while NaN/Infinity would put
    non-JSON numbers into an otherwise JSON-shaped contract.
    """
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def base_inputs(source: Any, transcript: Any) -> dict[str, str]:
    """The two identities shared by every reasoning artifact.

    They stay separate.  A single opaque "input" digest cannot say whether a
    miss came from replacing the media or correcting the transcript, and that
    distinction is what targeted invalidation needs.
    """
    return {
        "source_fingerprint": fingerprint(source),
        "transcript_fingerprint": fingerprint(transcript),
    }


def artifact_inputs(
    base: Mapping[str, Any], *, service_version: str,
    prompt_version: str | None = None, model: Any = None,
    temperature: float | None = None, engines: Any = None,
    chunk_config: Any = None, upstream: Mapping[str, Any] | None = None,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the common, explicit input projection for one artifact.

    Upstream values are fingerprinted here rather than trusting every caller to
    remember.  Parameters remain readable because they are usually the small
    constants an invalidation audit needs to name.
    """
    return {
        "source_fingerprint": str(base.get("source_fingerprint") or ""),
        "transcript_fingerprint": str(base.get("transcript_fingerprint") or ""),
        "service_version": str(service_version),
        "prompt_version": prompt_version,
        "model": model,
        "temperature": temperature,
        "engines": list(engines) if engines is not None else None,
        "chunk_config": chunk_config,
        "upstream": {
            str(name): fingerprint(value)
            for name, value in sorted((upstream or {}).items())
        },
        "parameters": dict(parameters or {}),
    }


def envelope(artifact: str, inputs: Mapping[str, Any], data: Any) -> dict:
    """Wrap one payload.  Unknown names are programming errors, not files."""
    if artifact not in ARTIFACTS:
        raise ValueError(f"unknown reasoning artifact: {artifact}")
    projected = dict(inputs)
    return {
        "envelope_version": ENVELOPE_VERSION,
        "artifact": artifact,
        "inputs": projected,
        "input_fingerprint": fingerprint(projected),
        "data_fingerprint": fingerprint(data),
        "data": data,
    }


def inspect(blob: Any, artifact: str,
            expected_inputs: Mapping[str, Any] | None = None) -> CacheRead:
    """Validate an envelope and, optionally, the inputs of the current run."""
    if not isinstance(blob, dict) or blob.get("envelope_version") != ENVELOPE_VERSION:
        return CacheRead(False, reason=LEGACY_OR_MALFORMED)
    if blob.get("artifact") != artifact:
        return CacheRead(False, reason=WRONG_ARTIFACT)
    inputs = blob.get("inputs")
    input_fp = blob.get("input_fingerprint")
    data_fp = blob.get("data_fingerprint")
    if (not isinstance(inputs, dict) or not isinstance(input_fp, str)
            or not isinstance(data_fp, str) or "data" not in blob):
        return CacheRead(False, reason=MALFORMED_ENVELOPE)
    try:
        if fingerprint(inputs) != input_fp:
            return CacheRead(False, reason=MALFORMED_ENVELOPE)
        if fingerprint(blob["data"]) != data_fp:
            return CacheRead(False, reason=DATA_CHANGED)
        # Compare the canonical JSON identities, not Python container types.
        # Tuples become arrays on disk; requiring object equality made a valid
        # segment_types envelope miss after every real JSON round trip.
        if (expected_inputs is not None
                and fingerprint(dict(expected_inputs)) != input_fp):
            return CacheRead(False, reason=INPUT_CHANGED)
    except (TypeError, ValueError):
        return CacheRead(False, reason=MALFORMED_ENVELOPE)
    return CacheRead(True, data=blob["data"], reason=HIT)
