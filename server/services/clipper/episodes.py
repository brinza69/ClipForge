"""What the stream has been about, up to any point in it. §2.

The gap this fills: anchor detection runs chunk by chunk, so a candidate at
hour seven sees its own local transcript, the promises still open, and its own
thread — and nothing about the six hours before it. It cannot know that the
boss it is fighting has been the subject for forty minutes, or that the
argument it is in the middle of started long before the chunk begins.

BUILT WITH NO MODEL, on purpose. §29 forbids paying per moment, and anchor
detection already spends one call per chunk; doubling that to summarise what a
cheaper structure already knows would be the expensive way to learn nothing
new. `threads.build` has already grouped the stream into arcs, and the atoms
already carry what was said. An episode is a fixed stretch of the clock, the
arcs running through it, and the words that tell it apart from the others.

  atoms  ->  threads  ->  episodes  ->  the line handed to a prompt

Both halves of that were got wrong first and corrected against the real
4-hour source; the failures are recorded at `build` and `_label` because each
one produced working code that summarised nothing.

WHO READS IT. `llm_select.anchor_prompt` puts the episodes that closed before
the current chunk in front of the model as "what has happened so far". That is
the only consumer, and it is the reason this file exists — six times now this
codebase has built a structure nothing read, and the way to not make it seven
is to write the consumer in the same change.
"""

from __future__ import annotations

import math
from typing import Sequence

from services.clipper.candidate_terms import _num

EPISODE_VERSION = "episodes_v1"

# An arc under this long is a moment, not a stretch of the stream. Threads
# already drop singletons; this drops the ones that are technically two atoms
# eight seconds apart.
MIN_THREAD_SPAN_S = 20.0
# How long an episode covers. Fixed rather than derived from where the arcs
# start and stop, and that is the correction the real source forced.
#
# The first version cut a new episode wherever no thread had been running for
# two minutes. On the 4-hour stream that produced TWO episodes, one of them
# spanning minute 8 to minute 240 — the whole thing. Threads on a dense source
# do not run in sequence, they overlap constantly, so there is no quiet moment
# to cut at and everything joins into one blob. A clock does not have that
# problem, and 20 minutes is short enough to say something specific while
# leaving a 4-hour stream with a summary of twelve lines.
EPISODE_S = 20 * 60.0
# How many words identify an episode.
EPISODE_KEYWORDS = 6
# Below this a word is a one-off, and a mis-transcription said once would
# otherwise make a perfect label: it is rare everywhere else by definition.
MIN_MENTIONS = 3


def _usable(threads: Sequence[dict]) -> list[dict]:
    out = []
    for t in threads or []:
        if not isinstance(t, dict):
            continue
        start, end = _num(t.get("start"), -1.0), _num(t.get("end"), -1.0)
        if start < 0 or end <= start or end - start < MIN_THREAD_SPAN_S:
            continue
        out.append(t)
    return sorted(out, key=lambda t: _num(t.get("start")))


def _words_in(atoms: Sequence[dict] | None, start: float,
              end: float) -> dict[str, int]:
    """Word counts for one stretch, from what was actually said in it."""
    from services.clipper.segmentation import norm_token

    counts: dict[str, int] = {}
    for a in atoms or []:
        if not isinstance(a, dict):
            continue
        if _num(a.get("end")) <= start or _num(a.get("start")) >= end:
            continue
        for raw in str(a.get("text") or "").split():
            word = norm_token(raw)
            if word:
                counts[word] = counts.get(word, 0) + 1
    return counts


def _label(counts: dict[str, int], buckets_with: dict[str, int],
           n_buckets: int) -> list[str]:
    """The words that tell one stretch apart from the others.

    TF-IDF across the episodes, on the words actually spoken. Two labelling
    rules were tried first and both are recorded here because each failed in a
    way that looked like working code:

      - "the words the most ARCS share" returned "don't, it's, right, can't,
        both, eight" on the 4-hour source. The words every arc has are the ones
        that identify nothing; the rule was exactly backwards.
      - "the RAREST words across the stream" left half the episodes with an
        empty label and the rest alphabetical. Thread keywords are already
        nearly unique, so almost every word tied at the maximum score and the
        sort fell through to the tiebreak.

    The words spoken inside the episode are the input that has enough mass for
    a frequency to mean something — hundreds per bucket rather than twelve —
    and a document frequency over the buckets kills the filler for free: a word
    in every episode has no discriminating power and scores zero, which is what
    the two failed rules were each trying to express by hand.
    """
    if not counts or n_buckets <= 0:
        return []
    total = sum(counts.values()) or 1
    scored = []
    for word, n in counts.items():
        if n < MIN_MENTIONS or len(word) < 4:
            continue
        seen_in = buckets_with.get(word, 1)
        if seen_in >= n_buckets:
            continue                      # said everywhere; says nothing
        idf = math.log(n_buckets / seen_in)
        scored.append((-(n / total) * idf, -n, word))
    scored.sort()
    return [word for _score, _n, word in scored[:EPISODE_KEYWORDS]]


def build(threads: Sequence[dict],
          atoms: Sequence[dict] | None = None) -> list[dict]:
    """Contiguous stretches of the stream, each with what it was about.

    Returns `[{start, end, keywords, threads, size, version}]`, in time order.
    Empty when the source is too short to have a structure — a twelve-minute
    clip has no episodes and should not be given fake ones.

    Measured on the 4-hour source: 123 threads and 1536 atoms become 12
    stretches, and the labels line up with what is known to be in it — the
    opening reads "control, failure, push, workout" over the gym segment, and
    minute 203-223 reads "drown, process, grown, sense, bubbles" over the
    argument the two best clips were cut from.
    """
    usable = _usable(threads)
    if not usable:
        return []

    first = min(_num(t.get("start")) for t in usable)
    last = max(_num(t.get("end")) for t in usable)
    if last - first < EPISODE_S / 2.0:
        return []          # too short to have a structure; do not invent one

    # One pass over the atoms per bucket, then the document frequencies across
    # buckets — both needed before any episode can be labelled.
    edges: list[tuple[float, float]] = []
    edge = first
    while edge < last:
        edges.append((edge, min(edge + EPISODE_S, last)))
        edge = min(edge + EPISODE_S, last)

    per_bucket = [_words_in(atoms, a, b) for a, b in edges]
    buckets_with: dict[str, int] = {}
    for counts in per_bucket:
        for word in counts:
            buckets_with[word] = buckets_with.get(word, 0) + 1

    out: list[dict] = []
    for (edge, stop), counts in zip(edges, per_bucket):
        # An arc belongs to every bucket it OVERLAPS. A forty-minute fight is
        # what two consecutive episodes are both about, and pinning it to
        # whichever one it happened to start in would leave the second
        # describing itself by whatever small talk ran alongside.
        inside = [t for t in usable
                  if _num(t.get("end")) > edge and _num(t.get("start")) < stop]
        if inside:
            out.append({
                "start": round(edge, 2),
                "end": round(stop, 2),
                "keywords": _label(counts, buckets_with, len(edges)),
                "threads": [str(t.get("id")) for t in inside if t.get("id")],
                "size": sum(int(_num(t.get("size"))) for t in inside),
                "version": EPISODE_VERSION,
            })
    return out


def before(episodes: Sequence[dict], t: float, *, limit: int = 8) -> list[dict]:
    """The episodes already finished at `t`, most recent last.

    Bounded: a 12-hour stream can produce dozens, and the point of a summary is
    that it is shorter than what it summarises. The most RECENT are kept, since
    what happened twenty minutes ago is likelier to be what a moment refers to
    than what happened nine hours ago.
    """
    done = [e for e in episodes or [] if _num(e.get("end")) <= t]
    return done[-limit:] if limit > 0 else done


def to_lines(episodes: Sequence[dict]) -> str:
    """The summary as a model reads it. Empty string when there is nothing."""
    rows = []
    for e in episodes or []:
        start_m = _num(e.get("start")) / 60.0
        end_m = _num(e.get("end")) / 60.0
        words = ", ".join(e.get("keywords") or []) or "unclear"
        rows.append(f"  [{start_m:.0f}-{end_m:.0f} min] {words}")
    return "\n".join(rows)
