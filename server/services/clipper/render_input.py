"""The render recipe, and the digest taken over it.

Split out of `edit_quality.py` when that file crossed 500 lines. It is a
separate subject anyway: everything here is about WHAT an export was made from,
while the rest of the audit is about what the edit turned out like. Re-exported
from `edit_quality` so callers did not have to move.

The one definition, used by both ends. `clipper_render_jobs` computes the digest
from the sidecar it is about to write; `audit_clipper_exports` recomputes it
from the file on disk and compares. Two implementations would drift, and a
fingerprint only means anything while both ends agree on what it covers.
"""

from __future__ import annotations

from typing import Any

from services.clipper import reasoning_trace


#: What the render fingerprint is taken over. The recipe — everything the
#: export was made FROM — and nothing that only labels the result: no title, no
#: scores, no review, no ids, because two reruns of the same recipe must
#: fingerprint the same or the digest says nothing.
#:
#: NOT a claim that this is everything that changes the image. The output size
#: is decided by defaults inside the two renderers with no shared authority to
#: read it from, so it is absent here until one exists. See the production plan.
FINGERPRINT_SCHEMA = "clipper_render_input_v1"
FINGERPRINT_KEYS: tuple[str, ...] = (
    "source", "source_start", "source_end", "layout_plan", "dynamic_plan",
    "caption_plan", "caption_y", "drop_spans", "render_version", "render",
)

def render_input(sidecar: dict) -> dict:
    """The recipe half of a sidecar, as the fingerprint sees it.

    The ONE definition, used by the export that writes the digest and by the
    audit that rechecks it. Two implementations would drift, and a fingerprint
    only means anything while both ends agree on what it covers.
    """
    payload: dict[str, Any] = {"schema": FINGERPRINT_SCHEMA}
    for key in FINGERPRINT_KEYS:
        payload[key] = sidecar.get(key)
    return payload


def input_fingerprint(sidecar: dict) -> str:
    """The digest of `render_input`, using the canonical function from
    `reasoning_trace` — not a second algorithm that would have to be kept in
    step with it."""
    return reasoning_trace.fingerprint(render_input(sidecar))
