"""Deterministic request keys for active pipeline deduplication."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def job_idempotency_key(job_type: str, metadata: dict[str, Any]) -> str:
    """Fingerprint the actual pipeline inputs/config, not the display title."""
    payload = json.dumps(
        {"type": job_type, "metadata": metadata},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
