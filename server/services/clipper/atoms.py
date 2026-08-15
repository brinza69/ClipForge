"""
ClipForge — AI Stream Clipper: event atoms.

The smallest unit the reasoning layers can point at: one natural utterance,
2–8 seconds, with every signal that already exists attached to it. A stream
becomes a list of these instead of a wall of transcript, and everything above
— anchors, promises, threads, retrieval — gets to work on units that already
carry their evidence.

CHEAP BY CONSTRUCTION, and that is a requirement, not a preference. A 12-hour
stream is roughly 8,600 atoms; the upgrade spec's own cost rule (§29) forbids
an LLM call per two seconds of video. So the physical half is built from the
transcript and the Pass A signals with no model involved, and the semantic
half is heuristic. Models work over AGGREGATIONS of atoms, never over atoms
one at a time.

BOUNDARIES ARE SENTENCES, not a fixed grid. A clock-aligned window cuts
through the middle of the one utterance that mattered; a sentence is the unit
a speaker actually produced. Long sentences are split and short ones merged,
so an atom stays inside 2–8s either way.

FEATURES AS EVIDENCE (§16). The ~60-key feature vector is not replaced by
this. An atom carries the same measurements at a finer grain, so a language
model reading a stream can see "he said this AND the room got loud AND the
picture cut" as one fact rather than inferring it from three separate series.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from services.clipper.candidate_terms import (
    _CUES, _EMOTION, _HOOK, _LAUGH, _clamp01, _mean, _num,
)
from services.clipper.segmentation import (
    norm_token, points_in, sentences_from_words, series_slice, signal_view,
    word_list,
)

logger = logging.getLogger("clipforge.clipper.atoms")

ATOMS_VERSION = "atoms_v1"

MIN_ATOM_S = 2.0
MAX_ATOM_S = 8.0
# How far a fragment may push its neighbour past the bound. A 0.2s atom is
# worse than an 8.5s one, but without a ceiling a run of fragments chains into
# one 79-second atom, which is what happened on the real source.
MERGE_CEILING_S = MAX_ATOM_S + MIN_ATOM_S

# Words that state something about what is ABOUT to happen. The one event type
# worth detecting cheaply, because it is what `promises` looks for and what a
# callback pays off.
#
# Unambiguous single words only. "going", "about", "if" and "next" were in
# here and turned "nothing much going on here" into a prediction — they are
# forward-looking only in a pair, which _FORWARD_PAIRS handles.
_FORWARD = frozenset((
    "will", "gonna", "finna", "bet", "promise", "guarantee",
    "promit", "pariez", "jur",
))
_FORWARD_PAIRS = frozenset((("going", "to"), ("about", "to"), ("have", "to"),
                            ("o", "sa"), ("am", "sa")))


def _kind(tokens: Sequence[str], text: str) -> str:
    """A coarse event type from words alone. Deliberately few and cheap."""
    if any(t in _LAUGH for t in tokens):
        return "laughter"
    if text.rstrip().endswith("?"):
        return "question"
    if any(t in _FORWARD for t in tokens):
        return "prediction"
    if any(pair in _FORWARD_PAIRS for pair in zip(tokens, tokens[1:])):
        return "prediction"
    if any(t in _EMOTION for t in tokens):
        return "reaction"
    if any(t in _HOOK for t in tokens):
        return "hook"
    return "speech"


def _importance(kind: str, energy: float, motion: float, cues: int,
                words: int) -> float:
    """How much this atom is likely to matter, 0..1.

    A ranking hint for retrieval and aggregation, not a score. Kept crude on
    purpose: anything subtle here would be a second scorer competing with the
    real one, and the point of an atom is to be cheap.
    """
    weight = {"laughter": 0.30, "reaction": 0.25, "prediction": 0.22,
              "question": 0.18, "hook": 0.20}.get(kind, 0.10)
    return round(_clamp01(weight + 0.35 * energy + 0.20 * motion
                          + 0.10 * min(1.0, cues / 2.0)
                          + 0.05 * min(1.0, words / 20.0)), 3)


def _words_of(sent: dict, words: Sequence[dict]) -> list[dict]:
    """The words a sentence spans.

    `sentences_from_words` reports an index range (`i0`, `i1`) rather than
    carrying the word dicts — reading a `words` key here silently produced
    empty atoms for every sentence in the source.
    """
    if isinstance(sent.get("words"), list) and sent["words"]:
        return [w for w in sent["words"] if isinstance(w, dict)]
    i0 = int(_num(sent.get("i0"), -1))
    i1 = int(_num(sent.get("i1"), -1))
    if 0 <= i0 <= i1 < len(words):
        return list(words[i0:i1 + 1])
    return []


def _split_long(sentences: Sequence[dict], all_words: Sequence[dict]) -> list[dict]:
    """Break a sentence that runs past MAX_ATOM_S on its own word gaps."""
    out: list[dict] = []
    for sent in sentences:
        words = _words_of(sent, all_words)
        start, end = _num(sent.get("start")), _num(sent.get("end"))
        if end - start <= MAX_ATOM_S or len(words) < 4:
            out.append({"start": start, "end": end, "words": words})
            continue
        current: list[dict] = []
        first = start
        for word in words:
            # Cut BEFORE the word that would cross the bound, not after it —
            # appending first let an atom run a whole word past MAX_ATOM_S.
            if current and _num(word.get("end")) - first > MAX_ATOM_S:
                out.append({"start": first, "end": _num(current[-1].get("end")),
                            "words": list(current)})
                first, current = _num(current[-1].get("end")), []
            current.append(word)
        if current:
            out.append({"start": first, "end": end, "words": current})
    return out


def _merge_short(units: Sequence[dict]) -> list[dict]:
    """Fold a sub-2s unit into a neighbour rather than emit a fragment.

    Backwards when there is something behind it, forwards otherwise — a
    "Yeah." opening the source has no previous atom to join, and leaving it
    alone would emit exactly the fragment this exists to prevent.
    """
    out: list[dict] = []
    pending: dict | None = None
    for unit in units:
        unit = dict(unit)
        if pending is not None:
            unit["start"] = pending["start"]
            unit["words"] = pending["words"] + unit["words"]
            pending = None
        short = unit["end"] - unit["start"] < MIN_ATOM_S
        # A fragment folds even when it pushes the neighbour a little past
        # MAX_ATOM_S — a 0.2s atom is worse than an 8.5s one. But only a
        # little: dropping the ceiling entirely let a run of fragments chain
        # into a single 79-second atom on the real source.
        if short and out and unit["end"] - out[-1]["start"] <= MERGE_CEILING_S:
            out[-1]["end"] = unit["end"]
            out[-1]["words"] = out[-1]["words"] + unit["words"]
            continue
        if short and not out:
            pending = unit
            continue
        out.append(unit)
    if pending is not None:
        out.append(pending)
    return out


def build(transcript: Any, signals: Any) -> list[dict]:
    """Every atom in the source, in time order.

    Pure: takes the transcript and the signals blob, touches no disk and no
    network, so the whole segmentation is testable without decoding anything.
    """
    words = word_list(transcript) if isinstance(transcript, dict) else []
    if not words:
        return []
    sv = signal_view(signals)
    units = _merge_short(_split_long(sentences_from_words(words), words))

    atoms: list[dict] = []
    for index, unit in enumerate(units):
        start, end = unit["start"], unit["end"]
        inside = unit["words"]
        text = " ".join(str(w.get("word", "")).strip() for w in inside).strip()
        if not text:
            continue
        tokens = [norm_token(w.get("word", "")) for w in inside]
        tokens = [t for t in tokens if t]

        rms = series_slice(sv["rms"], sv["rms_hop"], start, end)
        motion = series_slice(sv["motion"], sv["motion_hop"], start, end)
        ui = series_slice(sv.get("ui") or [], sv["motion_hop"], start, end)
        peaks = points_in(start, end, sv["peaks"])
        scenes = points_in(start, end, sv["scenes"])
        cues = sum(1 for t in tokens if t in _CUES)
        kind = _kind(tokens, text)
        energy = _clamp01(_mean(rms))
        motion_mean = _clamp01(_mean(motion))

        atoms.append({
            "i": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text[:400],
            "audio": {"energy": round(energy, 3),
                      "peaks": len(peaks),
                      "laughter": 1.0 if kind == "laughter" else 0.0},
            "visual": {"motion": round(motion_mean, 3),
                       "scene_change": bool(scenes),
                       "ui": round(_clamp01(_mean(ui)), 3) if ui else 0.0},
            "semantic": {"kind": kind,
                         "cues": cues,
                         "words": len(inside),
                         "importance": _importance(kind, energy, motion_mean,
                                                   cues, len(inside))},
        })

    logger.info("atoms: %d over %.0fs (%s)", len(atoms),
                atoms[-1]["end"] if atoms else 0.0, ATOMS_VERSION)
    return atoms


# Words too common to identify anything. Not a full stopword list — just the
# ones that would otherwise dominate an overlap score.
_TOO_COMMON = frozenset((
    "the", "and", "that", "this", "with", "have", "there", "what", "just",
    "like", "your", "they", "them", "then", "here", "gonna", "really", "bro",
    "chat", "yeah", "okay", "know", "going", "about", "because", "sunt",
    "este", "care", "pentru", "acum", "foarte",
))


def search(atoms: Sequence[dict], query: str, *, before: float,
           limit: int = 3, min_score: float = 0.12) -> list[dict]:
    """Earlier atoms that best match `query`, most relevant first.

    Token overlap weighted by rarity across the stream — no embeddings, no
    index, no new dependency, which is what §25 asks for. It answers the one
    question that matters here: when a clip says "remember what he said", is
    the thing it refers to anywhere in this stream, and how far back?

    `before` is a hard bound, not a preference: a back-reference points
    backwards, and matching something that has not happened yet would invent
    a referent rather than find one.
    """
    pool = [a for a in atoms or [] if _num(a.get("start"), 1e18) < before]
    if not pool:
        return []

    wanted = {t for t in (norm_token(w) for w in str(query or "").split())
              if t and len(t) > 3 and t not in _TOO_COMMON}
    if not wanted:
        return []

    # How many atoms each word appears in — a word used everywhere in this
    # stream identifies nothing in it.
    seen: dict[str, int] = {}
    tokens_by_atom: list[set[str]] = []
    for atom in pool:
        toks = {t for t in (norm_token(w) for w in atom["text"].split())
                if t and len(t) > 3 and t not in _TOO_COMMON}
        tokens_by_atom.append(toks)
        for t in toks & wanted:
            seen[t] = seen.get(t, 0) + 1

    total = float(len(pool))
    scored: list[tuple[float, dict]] = []
    for atom, toks in zip(pool, tokens_by_atom):
        hit = toks & wanted
        if not hit:
            continue
        # Rarity weight: 1.0 for a word in one atom, falling as it spreads.
        weight = sum(1.0 - (seen.get(t, 1) - 1) / total for t in hit)
        scored.append((weight / float(len(wanted)), atom))

    scored.sort(key=lambda s: (-s[0], -_num(s[1].get("start"))))
    return [dict(a, match=round(s, 3)) for s, a in scored[:limit] if s >= min_score]


def _notable(atoms: Sequence[dict]) -> dict[str, float]:
    """Per-source bars for what counts as notable.

    Absolute thresholds mark everything on a source that is always busy. This
    one runs about one audio peak every three seconds, so `peaks >= 1` tagged
    94% of lines — the marks became the noise they exist to cut through. Same
    lesson as `audio_peak_ratio` and `game_ui_ratio`: compare a moment against
    its own stream.
    """
    def bar(values: list[float], q: float, floor: float) -> float:
        if not values:
            return floor
        ordered = sorted(values)
        return max(floor, ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))])

    return {
        "energy": bar([a["audio"]["energy"] for a in atoms], 0.80, 0.35),
        "peaks": bar([float(a["audio"]["peaks"]) for a in atoms], 0.80, 1.0),
        "motion": bar([a["visual"]["motion"] for a in atoms], 0.80, 0.35),
    }


def to_lines(atoms: Sequence[dict], limit: int = 600_000) -> str:
    """Atoms as prompt lines, each carrying the evidence for its own moment.

    This is what makes atoms load-bearing rather than an unread artifact. The
    transcript alone tells a model what was said; an atom line also tells it
    the room got loud, the picture cut, or the game went into a menu — the
    same measurements the feature vector holds, at the grain of one utterance.

    Only what stands out FOR THIS SOURCE is printed. Marking every line is the
    same as marking none.
    """
    out: list[str] = []
    total = 0
    bars = _notable(list(atoms or []))
    for atom in atoms or []:
        marks = []
        audio, visual, semantic = atom["audio"], atom["visual"], atom["semantic"]
        if audio["energy"] >= bars["energy"]:
            marks.append("LOUD")
        if audio["peaks"] > bars["peaks"]:
            marks.append(f"peak x{audio['peaks']}")
        if visual["scene_change"]:
            marks.append("cut")
        if visual["motion"] >= bars["motion"]:
            marks.append("motion")
        if visual["ui"] >= 0.5:
            marks.append("menu")
        # "hook" is not printed: its vocabulary is "wait", "look", "why",
        # "how" — common enough in ordinary speech that it tagged 22% of
        # atoms, which is dilution rather than evidence. It stays on the atom
        # as data; it just is not worth a model's attention in a prompt.
        if semantic["kind"] not in ("speech", "hook"):
            marks.append(semantic["kind"])
        tail = f"  <{', '.join(marks)}>" if marks else ""
        line = f"[{int(atom['start'])}] {atom['text']}{tail}"
        total += len(line) + 1
        if total > limit:
            break
        out.append(line)
    return "\n".join(out)
