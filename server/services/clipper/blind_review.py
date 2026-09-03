"""
ClipForge — AI Stream Clipper: the blind review that Batch 10 turns on.

NOT `review.py`, which is Pass D — the automated look at a clip's PICTURE before
it ships. This is the human comparison of two BOARDS, and the two never meet.

WHAT IT DECIDES. Shadow mode computes a v2 board and ships legacy's. On the
pilot's four sources the two disagreed on 7, 6, 8 and 8 of 8 winners, so the
question is not whether v2 reorders — it is whether v2 is right, and no artefact
answers that. A person has to watch the clips.

WHY IT HAS TO BE BLIND. With that much disagreement an unblinded review measures
what the reviewer believes about v2, not what the clips are worth. So membership
never leaves this module: the client is handed an opaque `review_item_id` and
the clip's own neutral data, and which board asked for a clip is resolved only
when an answer comes back.

WHY THE ORDER IS PERSISTED RATHER THAN DERIVED. Recomputing a shuffle from a
seed on every load is reproducible only while the item list, the sort and the
shuffle implementation all stay identical — and a session that renumbers itself
halfway through silently reassigns answers to different clips. The order is
written down once, at creation, next to the seed that produced it.

ONE GLOBAL SHUFFLE, not one per project. A per-project order lets the reviewer
learn a source's rhythm and carry it into the next clip; interleaving sources
costs the ability to stop cleanly at a source boundary, which is the cheaper
loss.

NOT A VERDICT ON A CLIP. The event these answers become is `reviewed`, and it is
deliberately outside `feedback._DECISIVE`: a person answering an evaluation
question about a clip they did not ask for has not decided to publish it. The
ranker must not read it as one — that confusion is what once gave
`training_rows()` 43 rows every one of which was labelled 1.0.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Iterable, Sequence

#: Bumped when the QUESTIONS change. An answer is only comparable to another
#: answer to the same question, and a session that outlives a rubric edit would
#: otherwise pool two different instruments into one number.
RUBRIC_VERSION = "blind_eval_v1"

#: Bumped when the stored SHAPE changes, independently of the questions.
SCHEMA_VERSION = 2

LEGACY = "legacy"
SHADOW = "shadow"
BOTH = "both"

#: The six questions, in the order they are asked. Short and mandatory rather
#: than many and optional: an optional field's missingness correlates with
#: reviewer fatigue and with how hard the clip is to judge, which is exactly the
#: signal the review exists to measure, arriving as a hole in the data.
QUESTIONS: tuple[dict[str, Any], ...] = (
    {"key": "worth_exporting", "type": "choice",
     "options": ("yes", "no", "unsure")},
    {"key": "self_contained", "type": "choice", "options": ("yes", "no")},
    {"key": "hook", "type": "choice",
     "options": ("weak", "acceptable", "strong")},
    {"key": "start_boundary", "type": "choice",
     "options": ("early", "correct", "late")},
    {"key": "end_boundary", "type": "choice",
     "options": ("early", "correct", "late")},
    {"key": "technical_problem", "type": "choice", "options": ("yes", "no")},
)

#: Required when the verdict is `no`. "It was bad" is not a finding; which of
#: these it was tells you whether to change selection, boundaries or rendering.
REJECT_REASONS: tuple[str, ...] = (
    "no_payoff", "needs_context", "weak_hook", "boundary",
    "visual", "audio", "duplicate", "other",
)

_ANSWERABLE = {q["key"]: set(q["options"]) for q in QUESTIONS}


def item_id(session_id: str, clip_id: str) -> str:
    """An opaque handle for one clip inside one session.

    Opaque on purpose. The clip id leaks nothing by itself, but it is the key
    the reviewer's own board is addressed by, and a review page that shows it
    invites a glance at the board to see whether the clip is ranked — which is
    the blind broken by a URL.
    """
    raw = f"{session_id}|{clip_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _membership(legacy_rank: Any, shadow_rank: Any) -> str:
    has_legacy = legacy_rank is not None
    has_shadow = shadow_rank is not None
    if has_legacy and has_shadow:
        return BOTH
    return LEGACY if has_legacy else SHADOW


def build_items(rows: Iterable[dict]) -> list[dict]:
    """One entry per clip either board picked, with who picked it.

    A clip both boards chose appears ONCE, as `both`. Listing it twice would
    make the reviewer watch it twice, and their two answers would disagree often
    enough to matter — the second viewing is not the same instrument as the
    first.
    """
    items: list[dict] = []
    for row in rows:
        clip_id = str(row.get("clip_id") or "")
        if not clip_id:
            continue
        legacy_rank = row.get("rank_position")
        shadow_rank = row.get("shadow_rank")
        if legacy_rank is None and shadow_rank is None:
            continue
        items.append({
            "clip_id": clip_id,
            "project_id": str(row.get("project_id") or ""),
            "membership": _membership(legacy_rank, shadow_rank),
            "legacy_rank": legacy_rank,
            "shadow_rank": shadow_rank,
            "run_id": row.get("selection_run_id"),
            "shadow_run_id": row.get("shadow_run_id"),
            "media": row.get("media"),
        })
    return items


def create(session_id: str, rows: Iterable[dict], *, seed: int | None = None) -> dict:
    """A session, with its order fixed at the moment it is created."""
    items = build_items(rows)
    seed = int(seed) if seed is not None else random.randrange(2 ** 31)

    order = [item_id(session_id, item["clip_id"]) for item in items]
    random.Random(seed).shuffle(order)
    for item in items:
        item["review_item_id"] = item_id(session_id, item["clip_id"])

    media_complete = bool(items) and all(isinstance(i.get("media"), dict) for i in items)
    versions = sorted({i["media"]["render_version"] for i in items
                       if isinstance(i.get("media"), dict)
                       and i["media"].get("render_version")})
    version_complete = media_complete and all(i["media"].get("render_version") for i in items)
    return {
        "schema_version": SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        # One label only when EVERY observed sidecar declares that version.
        # Installed renderer code cannot identify already-rendered bytes.
        "render_version": versions[0] if version_complete and len(versions) == 1 else None,
        "render_versions": versions,
        "media_snapshot_complete": media_complete,
        "selection_identity_complete": bool(items) and all(i.get("run_id") for i in items),
        "session_id": session_id,
        "seed": seed,
        "items": items,
        "order": order,
        "answers": {},
    }


def _item_of(session: dict, review_item_id: str) -> dict | None:
    return next((i for i in session.get("items") or ()
                 if i.get("review_item_id") == review_item_id), None)


def next_item(session: dict) -> str | None:
    """The next unanswered handle, in the session's own recorded order."""
    answered = session.get("answers") or {}
    return next((h for h in session.get("order") or ()
                 if h not in answered), None)


def progress(session: dict) -> dict:
    total = len(session.get("order") or ())
    done = len(session.get("answers") or {})
    return {"answered": done, "total": total,
            "remaining": max(0, total - done)}


def validate(answer: dict) -> str:
    """Empty string when the answer is usable, otherwise why it is not.

    Validated here rather than at the edge because the rubric lives here: a
    route that accepted a value outside a question's options would store a
    label nothing downstream can interpret, and it would be found only when the
    review was already over.
    """
    if not isinstance(answer, dict):
        return "answer must be an object"
    for key, allowed in _ANSWERABLE.items():
        value = answer.get(key)
        if value is None:
            return f"missing answer: {key}"
        if value not in allowed:
            return f"{key}: {value!r} is not one of {sorted(allowed)}"

    reasons = answer.get("reject_reasons") or []
    if answer.get("worth_exporting") == "no" and not reasons:
        return "a rejected clip needs at least one reason"
    unknown = [r for r in reasons if r not in REJECT_REASONS]
    if unknown:
        return f"unknown reject reasons: {unknown}"
    return ""


def record(session: dict, review_item_id: str, answer: dict) -> dict:
    """Attach one answer. Raises for an unknown handle or an invalid answer.

    Stores the PRESENTATION POSITION alongside the answer. Order effects are
    real — the tenth clip of a session is judged by a different reviewer than
    the first — and a position recovered later from `order` is only correct
    while the order has never been rebuilt.
    """
    item = _item_of(session, review_item_id)
    if item is None:
        raise KeyError(f"no such review item: {review_item_id}")
    problem = validate(answer)
    if problem:
        raise ValueError(problem)

    order = list(session.get("order") or ())
    session.setdefault("answers", {})[review_item_id] = {
        **{k: answer[k] for k in _ANSWERABLE},
        "reject_reasons": list(answer.get("reject_reasons") or []),
        "note": str(answer.get("note") or "")[:2000],
        "watch_fraction": _fraction(answer.get("watch_fraction")),
        "position": (order.index(review_item_id) + 1
                     if review_item_id in order else None),
        "rubric_version": session.get("rubric_version") or RUBRIC_VERSION,
    }
    return session["answers"][review_item_id]


def _fraction(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return round(min(1.0, max(0.0, out)), 3)


def tally(session: dict) -> dict:
    """The comparison the session exists to produce.

    `unsure` counts as neither. Folding it into `no` would report a reviewer's
    hesitation as a defect of the clip, and the two are worth telling apart:
    many `unsure` on one board is itself a finding.

    A clip BOTH boards picked counts for both, which is the honest reading —
    agreement is not evidence for either side, and dropping it would compare the
    boards only where they differ, inflating whichever one wins the arguments.
    """
    out = {LEGACY: _empty(), SHADOW: _empty()}
    for handle, answer in (session.get("answers") or {}).items():
        item = _item_of(session, handle)
        if item is None:
            continue
        sides = ([LEGACY, SHADOW] if item["membership"] == BOTH
                 else [item["membership"]])
        for side in sides:
            bucket = out[side]
            bucket["reviewed"] += 1
            verdict = answer.get("worth_exporting")
            bucket[verdict] = bucket.get(verdict, 0) + 1
            if answer.get("self_contained") == "yes":
                bucket["self_contained"] += 1
            if (answer.get("start_boundary") == "correct"
                    and answer.get("end_boundary") == "correct"):
                bucket["boundaries_correct"] += 1

    for side in (LEGACY, SHADOW):
        decided = out[side]["yes"] + out[side]["no"]
        out[side]["precision"] = (round(out[side]["yes"] / decided, 3)
                                  if decided else None)
    return out


def _empty() -> dict:
    return {"reviewed": 0, "yes": 0, "no": 0, "unsure": 0,
            "self_contained": 0, "boundaries_correct": 0, "precision": None}


def public_item(session: dict, review_item_id: str, clip: dict) -> dict:
    """What the CLIENT is allowed to see for one item.

    The allowlist is the blind. Returning the clip row and trusting the page not
    to render `shadow_rank` would put membership in the DOM, one devtools panel
    away — and the reviewer only has to slip once for the session to be worth
    nothing.
    """
    item = _item_of(session, review_item_id)
    if item is None:
        raise KeyError(f"no such review item: {review_item_id}")
    order = list(session.get("order") or ())
    return {
        "review_item_id": review_item_id,
        "position": (order.index(review_item_id) + 1
                     if review_item_id in order else None),
        "total": len(order),
        "start": clip.get("start_time"),
        "end": clip.get("end_time"),
        "duration": clip.get("duration"),
        # The EXPORT's readiness, because that is what the review serves. A
        # preview is capped at 12 seconds, so reporting it as ready is how a
        # session gets run on truncated clips.
        "preview_ready": bool(clip.get("export_path")),
        # Shown only AFTER the verdict, by the page. Carried here because a
        # second request for it would be a second chance to leak which board
        # asked — this one is the same for every item.
        "transcript": (clip.get("transcript_text") or "")[:4000],
    }


def reveal(session: dict, review_item_ids: Sequence[str] | None = None) -> list[dict]:
    """Membership, for reading the results — never for rendering the review."""
    wanted = set(review_item_ids or ())
    return [
        {"review_item_id": i["review_item_id"], "clip_id": i["clip_id"],
         "project_id": i["project_id"], "membership": i["membership"],
         "legacy_rank": i["legacy_rank"], "shadow_rank": i["shadow_rank"]}
        for i in (session.get("items") or ())
        if not wanted or i["review_item_id"] in wanted
    ]
