"""
ClipForge — Momentum Score Engine (v2)
Multi-signal scoring system for viral clip candidate detection.

v2 improvements:
  - Timestamp-aware clip boundaries (snap to sentence boundaries, avoid mid-word cuts)
  - Dead-time removal (detect and skip silences/weak pauses within clips)
  - Enforced 60-90 second target clip length
  - Stronger first-seconds optimization for retention
  - Better sliding window with pause-aware segmentation
"""

import logging
import re
import math
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict

from config import settings

logger = logging.getLogger("clipforge.scorer")


# ============================================================================
# Hook Patterns — configurable phrase patterns that indicate strong openings
# ============================================================================

HOOK_PATTERNS = [
    # Romanian patterns (common for Romanian speech content)
    r"\bvă (?:spun|arăt|zic)\b",
    r"\bnimeni nu (?:știe|vorbește|spune)\b",
    r"\badevărul (?:este|despre)\b",
    r"\bniciodată nu (?:am|vei)\b",
    r"\bascultați (?:asta|bine)\b",
    r"\bstați să (?:vă|vedeți)\b",

    # Mystery / Curiosity
    r"\bwhat (?:nobody|no one) tells you\b",
    r"\bthe (?:real|actual|true) reason\b",
    r"\bthe weird(?:est)? (?:part|thing)\b",
    r"\bthis changes everything\b",
    r"\bmost people (?:don'?t|miss|ignore|overlook)\b",
    r"\bhere'?s (?:the|what'?s) (?:thing|crazy|insane|wild)\b",
    r"\byou'?re not (?:going to|gonna) believe\b",
    r"\bwhat (?:they|people) don'?t (?:want you to|realize)\b",
    r"\blet me (?:tell|explain|show) you\b",
    r"\bthe truth (?:is|about)\b",

    # Revelation / Surprise
    r"\bturns out\b",
    r"\bplot twist\b",
    r"\bit gets (?:worse|better|crazier|weirder)\b",
    r"\bwait (?:for it|till you hear)\b",
    r"\b(?:but |and )?here'?s (?:the|where it gets)\b",
    r"\bi (?:just )?found out\b",
    r"\bthe craziest (?:part|thing)\b",

    # Bold Claims
    r"\bthis is (?:the )?(?:biggest|worst|best|most important)\b",
    r"\beveryone is wrong about\b",
    r"\bnobody is talking about\b",
    r"\bthe (?:government|media|they) (?:doesn'?t|don'?t) want\b",
    r"\bhidden (?:truth|secret|agenda)\b",

    # Engagement
    r"\b(?:think about|consider) this\b",
    r"\bask yourself\b",
    r"\bwhy (?:does|would|is) (?:nobody|no one)\b",
    r"\bhow (?:is|does) (?:this|that) (?:even|possible)\b",

    # Narrative
    r"\bso (?:basically|essentially|here'?s what happened)\b",
    r"\blong story short\b",
    r"\bthe backstory is\b",
]

# Curiosity / engagement trigger words
CURIOSITY_WORDS = {
    # English
    "secret", "hidden", "exposed", "revealed", "shocking", "insane", "wild",
    "crazy", "unbelievable", "terrifying", "disturbing", "mysterious", "strange",
    "bizarre", "impossible", "banned", "forbidden", "classified", "conspiracy",
    "cover-up", "truth", "proof", "evidence", "theory", "ancient", "discovery",
    "breakthrough", "warning", "urgent", "dangerous", "powerful", "massive",
    "incredible", "stunning", "mind-blowing", "game-changer", "revolutionary",
    "catastrophic", "unprecedented", "nightmare", "apocalyptic", "extinction",
    "nibiru", "planet", "cosmic", "galactic", "alien", "ufo",
    # Romanian
    "secret", "adevărul", "incredibil", "șocant", "periculos", "imposibil",
    "interzis", "misterios", "descoperire", "dovadă", "urgent", "catastrofă",
    "apocalipsă", "cosmic", "extraterestru", "conspirație", "revelație",
}

# Emotional intensity markers
EMOTION_WORDS = {
    "angry", "furious", "disgusted", "heartbroken", "devastated", "terrified",
    "ecstatic", "thrilled", "shocked", "horrified", "amazed", "outraged",
    "passionate", "desperate", "obsessed", "fascinated", "overwhelmed",
    "love", "hate", "fear", "hope", "rage", "grief", "joy", "panic",
    "absolutely", "literally", "completely", "totally", "utterly", "genuinely",
    "seriously", "honestly", "fundamentally", "profoundly",
}

# Rhetorical question patterns
QUESTION_PATTERNS = [
    r"\b(?:why|how|what|when|where|who) (?:do|does|did|would|could|is|are|was|were)\b.*\?",
    r"\bhave you (?:ever|noticed|wondered|thought about)\b",
    r"\bdoes (?:anyone|anybody|that|this) (?:even|really|actually)\b",
    r"\bisn'?t (?:it|that|this) (?:crazy|wild|insane|weird|strange)\b",
]

# ---------------------------------------------------------------------------
# Dead-time / silence detection thresholds
# ---------------------------------------------------------------------------
DEAD_PAUSE_THRESHOLD = 1.5   # Seconds of silence that counts as "dead time"
WEAK_PAUSE_THRESHOLD = 0.8   # Pauses that feel slow but aren't dead
MAX_INTERNAL_DEAD_TIME = 5.0 # Max total dead time allowed inside a clip


@dataclass
class ClipCandidate:
    """A scored clip candidate from the transcript."""
    start_time: float
    end_time: float
    duration: float
    transcript_text: str
    transcript_segments: List[Dict]

    # Score breakdown
    momentum_score: float = 0.0
    hook_strength: float = 0.0
    narrative_completeness: float = 0.0
    curiosity_score: float = 0.0
    emotional_intensity: float = 0.0
    caption_readability: float = 0.0
    confidence: float = 0.0

    # Auto-generated
    title: str = ""
    hook_text: str = ""
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def generate_clip_candidates(
    segments: List[Dict],
    min_duration: float = None,
    max_duration: float = None,
    target_duration: float = None,
    max_candidates: int = None,
    custom_hook_patterns: Optional[List[str]] = None,
    custom_boost_words: Optional[set] = None,
) -> List[ClipCandidate]:
    """
    Generate and score clip candidates from transcript segments.

    Pipeline:
    1. Sliding window segmentation with pause-aware boundaries
    2. Dead-time analysis and boundary snapping
    3. Multi-signal scoring
    4. Ranking and deduplication
    """
    min_dur = min_duration or settings.min_clip_duration
    max_dur = max_duration or settings.max_clip_duration
    target_dur = target_duration or settings.target_clip_duration
    max_count = max_candidates or settings.default_clip_count

    if not segments:
        logger.warning("No segments provided for scoring")
        return []

    total_duration = segments[-1]["end"] if segments else 0
    logger.info(f"Generating candidates from {len(segments)} segments ({total_duration:.0f}s)")

    # Merge hook patterns
    all_hook_patterns = HOOK_PATTERNS.copy()
    if custom_hook_patterns:
        all_hook_patterns.extend(custom_hook_patterns)

    all_boost_words = CURIOSITY_WORDS.copy()
    if custom_boost_words:
        all_boost_words.update(custom_boost_words)

    # Step 1: Detect sentence boundaries and pauses in the transcript
    boundaries = _detect_boundaries(segments)

    # Step 2: Generate candidate windows using boundary-aware sliding window
    candidates = _generate_windows(segments, boundaries, min_dur, max_dur, target_dur)
    logger.info(f"Generated {len(candidates)} raw candidate windows")

    if not candidates:
        return []

    # Step 3: Snap clip boundaries to strong sentence boundaries
    for candidate in candidates:
        _snap_boundaries(candidate, segments, boundaries)

    # Step 4: Analyze and penalize dead time
    for candidate in candidates:
        _analyze_dead_time(candidate)

    # Step 5: Score each candidate
    for candidate in candidates:
        _score_candidate(candidate, all_hook_patterns, all_boost_words, segments)

    # Step 6: Sort by momentum score
    candidates.sort(key=lambda c: c.momentum_score, reverse=True)

    # Step 7: Deduplicate
    deduplicated = _deduplicate(candidates, settings.overlap_threshold)
    logger.info(f"After deduplication: {len(deduplicated)} candidates")

    # Step 8: Take top N
    top_candidates = deduplicated[:max_count]

    # Step 9: Generate metadata
    for candidate in top_candidates:
        candidate.title = _generate_title(candidate)
        candidate.hook_text = _generate_hook_text(candidate)
        candidate.explanation = _generate_explanation(candidate)

    logger.info(f"Final candidates: {len(top_candidates)}")
    for i, c in enumerate(top_candidates):
        logger.info(
            f"  #{i+1}: [{c.start_time:.0f}s-{c.end_time:.0f}s] "
            f"({c.duration:.0f}s) Score={c.momentum_score:.1f} Hook={c.hook_strength:.1f} "
            f'"{c.title}"'
        )

    return top_candidates


# ============================================================================
# Boundary detection — find sentence boundaries and pauses
# ============================================================================

def _detect_boundaries(segments: List[Dict]) -> List[Dict]:
    """
    Detect natural boundaries in the transcript:
    - Sentence endings (., !, ?)
    - Significant pauses between segments
    - Topic shift indicators

    Returns list of boundary dicts with:
      time: float, type: str ("sentence"|"pause"|"strong_pause"), strength: float
    """
    boundaries = []

    for i, seg in enumerate(segments):
        text = seg.get("text", "").strip()

        # Check segment text for sentence endings
        if text and text[-1] in ".!?":
            boundaries.append({
                "time": seg["end"],
                "type": "sentence",
                "strength": 0.8 if text[-1] == "." else 1.0,  # ! and ? are stronger
                "seg_index": i,
            })

        # Check for pause between this segment and the next
        if i < len(segments) - 1:
            gap = segments[i + 1]["start"] - seg["end"]
            if gap >= DEAD_PAUSE_THRESHOLD:
                boundaries.append({
                    "time": seg["end"],
                    "type": "strong_pause",
                    "strength": min(1.0, gap / 3.0),
                    "gap_duration": gap,
                    "seg_index": i,
                })
            elif gap >= WEAK_PAUSE_THRESHOLD:
                boundaries.append({
                    "time": seg["end"],
                    "type": "pause",
                    "strength": 0.4,
                    "gap_duration": gap,
                    "seg_index": i,
                })

    return boundaries


def _generate_windows(
    segments: List[Dict],
    boundaries: List[Dict],
    min_dur: float,
    max_dur: float,
    target_dur: float,
) -> List[ClipCandidate]:
    """
    Generate candidate clip windows using sliding window approach.
    Prefers starting at sentence boundaries and strong pauses.
    """
    candidates = []
    n = len(segments)

    # Collect good start indices: sentence boundaries and pauses make good clip starts
    good_starts = set()
    for b in boundaries:
        idx = b.get("seg_index", 0)
        # The segment AFTER this boundary is a good start
        if idx + 1 < n:
            good_starts.add(idx + 1)

    # Also add regular interval starts for coverage (but fewer, to avoid noise)
    step_sizes = [5, 10]
    for step in step_sizes:
        for i in range(0, n, step):
            good_starts.add(i)

    # Sort starts chronologically
    start_indices = sorted(good_starts)

    for i in start_indices:
        start_time = segments[i]["start"]
        window_segments = []
        window_text_parts = []

        for j in range(i, n):
            seg = segments[j]
            window_segments.append(seg)
            window_text_parts.append(seg["text"])

            current_duration = seg["end"] - start_time

            if current_duration < min_dur:
                continue

            if current_duration > max_dur:
                break

            # Create a candidate
            candidate = ClipCandidate(
                start_time=start_time,
                end_time=seg["end"],
                duration=current_duration,
                transcript_text=" ".join(window_text_parts),
                transcript_segments=[s.copy() for s in window_segments],
            )
            candidates.append(candidate)

            # Once we reach the target duration, we can stop extending
            # (but we already captured the candidate at the ideal length)
            if current_duration >= target_dur:
                break

    return candidates


# ============================================================================
# Boundary snapping — improve clip start/end points
# ============================================================================

def _snap_boundaries(
    candidate: ClipCandidate,
    segments: List[Dict],
    boundaries: List[Dict],
):
    """
    Snap clip start/end to nearby sentence boundaries for cleaner cuts.

    Rules:
    - Move start forward to skip dead time at the beginning (up to 3s)
    - Move start forward to begin at a sentence boundary if one is within 2s
    - Move end to the nearest sentence boundary within 3s
    - Never extend beyond max duration
    - Prefer ending on a sentence boundary (period, exclamation, question mark)
    """
    max_dur = settings.max_clip_duration
    min_dur = settings.min_clip_duration

    # --- Snap start: skip initial dead time ---
    # Look for the first word/speech in the first 3 seconds
    first_seg = candidate.transcript_segments[0] if candidate.transcript_segments else None
    if first_seg:
        # If there's a gap between clip start and first speech, skip it
        speech_start = first_seg["start"]
        if speech_start > candidate.start_time + 0.5:
            # Snap to 0.2s before first speech (small lead-in)
            new_start = max(candidate.start_time, speech_start - 0.2)
            shift = new_start - candidate.start_time
            if shift <= 3.0:  # Only snap if within 3s
                candidate.start_time = round(new_start, 3)
                candidate.duration = round(candidate.end_time - candidate.start_time, 3)

    # --- Snap end: prefer sentence boundary ---
    # Look for a sentence boundary near the end (within 3s before current end)
    best_end_boundary = None
    for b in boundaries:
        if b["type"] in ("sentence", "strong_pause"):
            bt = b["time"]
            # Look within 3s before current end, and at least min_dur from start
            if (candidate.end_time - 3.0) <= bt <= candidate.end_time:
                if (bt - candidate.start_time) >= min_dur:
                    if best_end_boundary is None or bt > best_end_boundary:
                        best_end_boundary = bt

    if best_end_boundary is not None:
        candidate.end_time = round(best_end_boundary, 3)
        candidate.duration = round(candidate.end_time - candidate.start_time, 3)

        # Trim transcript_segments to match new end
        candidate.transcript_segments = [
            s for s in candidate.transcript_segments
            if s["start"] < candidate.end_time
        ]
        candidate.transcript_text = " ".join(
            s["text"] for s in candidate.transcript_segments
        )


# ============================================================================
# Dead-time analysis
# ============================================================================

def _analyze_dead_time(candidate: ClipCandidate):
    """
    Analyze internal dead time (silences/pauses) within a clip.
    Penalizes candidates with excessive dead time.
    Stores dead_time_total on the candidate for scoring.
    """
    segments = candidate.transcript_segments
    total_dead = 0.0
    dead_count = 0

    for i in range(1, len(segments)):
        gap = segments[i]["start"] - segments[i - 1]["end"]
        if gap >= DEAD_PAUSE_THRESHOLD:
            total_dead += gap
            dead_count += 1
        elif gap >= WEAK_PAUSE_THRESHOLD:
            total_dead += gap * 0.5  # Half-weight for weak pauses

    # Store for use in scoring
    candidate.__dict__["_dead_time"] = total_dead
    candidate.__dict__["_dead_count"] = dead_count


def _score_candidate(
    candidate: ClipCandidate,
    hook_patterns: List[str],
    boost_words: set,
    all_segments: List[Dict],
):
    """Score a single candidate across all signals."""
    text = candidate.transcript_text.lower()
    segments = candidate.transcript_segments
    words = text.split()
    word_count = len(words)

    if word_count == 0:
        return

    # --- 1. Hook Strength (25%) ---
    first_words = " ".join(words[:15])
    hook_score = 0.0

    for pattern in hook_patterns:
        if re.search(pattern, first_words, re.IGNORECASE):
            hook_score += 30.0

    first_sentence = text.split(".")[0] if "." in text else text[:100]
    for qp in QUESTION_PATTERNS:
        if re.search(qp, first_sentence, re.IGNORECASE):
            hook_score += 20.0

    bold_openers = ["listen", "look", "okay", "so", "imagine", "picture", "think"]
    if words[0] in bold_openers:
        hook_score += 10.0

    if words[0] in ["i", "you", "we"]:
        hook_score += 5.0

    hook_score = min(hook_score, 100.0)

    # --- 2. Curiosity Score (20%) ---
    curiosity_score = 0.0
    curiosity_hits = sum(1 for w in words if w.strip(".,!?'\"") in boost_words)
    curiosity_density = curiosity_hits / max(word_count, 1)
    curiosity_score = min(curiosity_density * 800, 60.0)

    gap_patterns = [
        r"\bbut (?:what|here'?s|the)\b",
        r"\bthe (?:problem|issue|question) is\b",
        r"\bwhat (?:if|about)\b",
        r"\bexcept\b",
        r"\bhowever\b",
        r"\bbut nobody\b",
    ]
    for gp in gap_patterns:
        if re.search(gp, text, re.IGNORECASE):
            curiosity_score += 10.0

    curiosity_score = min(curiosity_score, 100.0)

    # --- 3. Emotional Intensity (15%) ---
    emotion_score = 0.0
    emotion_hits = sum(1 for w in words if w.strip(".,!?'\"") in EMOTION_WORDS)
    emotion_density = emotion_hits / max(word_count, 1)
    emotion_score = min(emotion_density * 600, 50.0)

    exclamation_count = text.count("!")
    emotion_score += min(exclamation_count * 5, 20.0)

    caps_words = sum(1 for w in candidate.transcript_text.split() if w.isupper() and len(w) > 2)
    emotion_score += min(caps_words * 5, 15.0)

    contrast_patterns = [r"\bbut\b", r"\bhowever\b", r"\bactually\b", r"\bin fact\b", r"\bcontrary\b"]
    for cp in contrast_patterns:
        if re.search(cp, text, re.IGNORECASE):
            emotion_score += 5.0

    emotion_score = min(emotion_score, 100.0)

    # --- 4. Narrative Completeness (15%) ---
    narrative_score = 0.0

    setup_markers = [r"\bso\b", r"\bbasically\b", r"\bthe thing is\b", r"\bwhat happened\b"]
    for sm in setup_markers:
        if re.search(sm, first_words, re.IGNORECASE):
            narrative_score += 15.0
            break

    if word_count > 30:
        narrative_score += 15.0

    last_words = " ".join(words[-15:])
    conclusion_markers = [
        r"\band that'?s\b", r"\bso (?:yeah|that'?s)\b", r"\bthat'?s (?:why|how|what)\b",
        r"\bthe point is\b", r"\bbottom line\b", r"\bin conclusion\b",
        r"\bthink about that\b", r"\blet that sink in\b",
    ]
    for cm in conclusion_markers:
        if re.search(cm, last_words, re.IGNORECASE):
            narrative_score += 20.0
            break

    sentence_count = len(re.split(r'[.!?]+', text))
    if 3 <= sentence_count <= 12:
        narrative_score += 15.0
    elif sentence_count > 12:
        narrative_score += 5.0

    if text and text.rstrip() and text.rstrip()[-1] in ".!?":
        narrative_score += 10.0

    narrative_score = min(narrative_score, 100.0)

    # --- 5. Speech Dynamics & Pacing (10%) ---
    dynamics_score = 0.0

    if len(segments) > 1:
        pauses = []
        for k in range(1, len(segments)):
            gap = segments[k]["start"] - segments[k-1]["end"]
            if gap > 0:
                pauses.append(gap)

        if pauses:
            avg_pause = sum(pauses) / len(pauses)
            if 0.3 <= avg_pause <= 1.5:
                dynamics_score += 20.0
            dramatic_pauses = sum(1 for p in pauses if 1.0 <= p <= 3.0)
            dynamics_score += min(dramatic_pauses * 10, 30.0)

    if segments:
        rates = []
        for seg in segments:
            seg_dur = seg["end"] - seg["start"]
            seg_words = len(seg["text"].split())
            if seg_dur > 0:
                rates.append(seg_words / seg_dur)

        if len(rates) > 2:
            rate_variance = _variance(rates)
            if rate_variance > 0.5:
                dynamics_score += 20.0

    # Duration scoring — reward content in the 45-90s sweet spot, accept 30-120s
    dur = candidate.duration
    if 55 <= dur <= 90:
        dynamics_score += 25.0  # Sweet spot (TikTok/Reels ideal)
    elif 45 <= dur < 55 or 90 < dur <= 105:
        dynamics_score += 18.0  # Good range
    elif 30 <= dur < 45 or 105 < dur <= 120:
        dynamics_score += 8.0   # Acceptable
    # else: 0 — outside range

    dynamics_score = min(dynamics_score, 100.0)

    # --- 6. Caption Readability (10%) ---
    readability_score = 0.0

    avg_word_len = sum(len(w) for w in words) / max(word_count, 1)
    if avg_word_len <= 6:
        readability_score += 30.0
    elif avg_word_len <= 8:
        readability_score += 20.0

    avg_sentence_len = word_count / max(sentence_count, 1)
    if avg_sentence_len <= 15:
        readability_score += 30.0
    elif avg_sentence_len <= 25:
        readability_score += 15.0

    if re.search(r'"[^"]{10,}"', text):
        readability_score += 20.0

    has_word_timestamps = any(seg.get("words") for seg in segments)
    if has_word_timestamps:
        readability_score += 20.0

    readability_score = min(readability_score, 100.0)

    # --- 7. Topic Density (5% bonus) ---
    topic_bonus = 0.0

    caps_entities = sum(1 for w in candidate.transcript_text.split()
                       if w[0:1].isupper() and len(w) > 2 and w not in {"The", "And", "But", "This", "That"})
    if caps_entities > 3:
        topic_bonus += 10.0

    numbers = re.findall(r'\b\d+[%,.]?\d*\b', text)
    if numbers:
        topic_bonus += min(len(numbers) * 5, 15.0)

    topic_bonus = min(topic_bonus, 100.0)

    # --- 8. Dead-time penalty ---
    dead_time = candidate.__dict__.get("_dead_time", 0.0)
    dead_count = candidate.__dict__.get("_dead_count", 0)
    dead_penalty = 0.0

    if dead_time > MAX_INTERNAL_DEAD_TIME:
        # Heavy penalty for excessive dead time
        dead_penalty = min(20.0, (dead_time - MAX_INTERNAL_DEAD_TIME) * 4.0)
    elif dead_time > 2.0:
        # Mild penalty for noticeable dead time
        dead_penalty = (dead_time - 2.0) * 2.0

    # --- 9. Retention strength: penalize weak openings ---
    retention_penalty = 0.0
    if hook_score < 10.0:
        # No hook at all — check if the first 3 seconds have speech
        if segments:
            first_seg_delay = segments[0]["start"] - candidate.start_time
            if first_seg_delay > 1.5:
                # Dead air at the start — strong penalty
                retention_penalty = min(15.0, first_seg_delay * 5.0)

    # --- Calculate weighted Momentum Score ---
    candidate.hook_strength = round(hook_score, 1)
    candidate.curiosity_score = round(curiosity_score, 1)
    candidate.emotional_intensity = round(emotion_score, 1)
    candidate.narrative_completeness = round(narrative_score, 1)
    candidate.caption_readability = round(readability_score, 1)

    weighted_score = (
        hook_score * 0.25 +
        curiosity_score * 0.20 +
        emotion_score * 0.15 +
        narrative_score * 0.15 +
        dynamics_score * 0.10 +
        readability_score * 0.10 +
        topic_bonus * 0.05
    )

    # Apply penalties
    weighted_score -= dead_penalty
    weighted_score -= retention_penalty

    candidate.momentum_score = round(max(0.0, min(weighted_score, 100.0)), 1)

    # Confidence
    avg_confidence = 0.5
    confidences = [seg.get("confidence", 0.5) for seg in segments if "confidence" in seg]
    if confidences:
        avg_confidence = sum(confidences) / len(confidences)

    candidate.confidence = round(avg_confidence * 100, 1)


def _deduplicate(candidates: List[ClipCandidate], overlap_threshold: float) -> List[ClipCandidate]:
    """Remove candidates that overlap too much with higher-scored ones."""
    result = []

    for candidate in candidates:
        is_duplicate = False
        for existing in result:
            overlap = _calc_overlap(candidate, existing)
            if overlap > overlap_threshold:
                is_duplicate = True
                break

        if not is_duplicate:
            result.append(candidate)

    return result


def _calc_overlap(a: ClipCandidate, b: ClipCandidate) -> float:
    """Calculate overlap ratio between two time ranges."""
    overlap_start = max(a.start_time, b.start_time)
    overlap_end = min(a.end_time, b.end_time)
    overlap_duration = max(0, overlap_end - overlap_start)

    shorter_duration = min(a.duration, b.duration)
    if shorter_duration == 0:
        return 0.0

    return overlap_duration / shorter_duration


def _variance(values: List[float]) -> float:
    """Calculate variance of a list of numbers."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _generate_title(candidate: ClipCandidate) -> str:
    """Auto-generate a clip title from the transcript content."""
    text = candidate.transcript_text.strip()

    first_sentence = re.split(r'[.!?]', text)[0].strip()

    if len(first_sentence) > 60:
        words = first_sentence.split()
        title = ""
        for w in words:
            if len(title) + len(w) + 1 > 55:
                title += "..."
                break
            title += (" " if title else "") + w
        return title

    if len(first_sentence) < 10:
        words = text.split()[:10]
        return " ".join(words) + ("..." if len(text.split()) > 10 else "")

    return first_sentence


def _generate_hook_text(candidate: ClipCandidate) -> str:
    """
    Generate a curiosity-driven hook from the clip content.

    Strategy (ordered by priority):
      1. If the opening matches a known hook pattern, extract that phrase.
      2. If there's a strong question in the first ~30 words, use it.
      3. If there's a bold claim or revelation phrase, reframe it.
      4. Fallback: extract the first compelling sentence fragment.
    """
    text = candidate.transcript_text.strip()
    words = text.split()
    if not words:
        return ""

    # --- 1. Check for hook pattern match in the opening ---
    first_30 = " ".join(words[:30]).lower()

    # Try to extract a clean phrase around a hook pattern
    for pattern in HOOK_PATTERNS:
        m = re.search(pattern, first_30, re.IGNORECASE)
        if m:
            # Grab the matched phrase + context to fill ~8-12 words
            match_start = m.start()
            # Find word index closest to match
            char_count = 0
            word_idx = 0
            for wi, w in enumerate(words[:30]):
                if char_count >= match_start:
                    word_idx = wi
                    break
                char_count += len(w) + 1

            # Extract 6-10 words starting from match
            hook_words = words[word_idx:word_idx + 10]
            hook = " ".join(hook_words)
            # Trim at sentence boundary if present
            for sep in ".!?":
                pos = hook.find(sep)
                if 15 < pos < len(hook):
                    hook = hook[:pos + 1]
                    break
            hook = hook[:1].upper() + hook[1:] if hook else hook
            if len(hook) > 70:
                hook = hook[:67].rstrip() + "..."
            return hook

    # --- 2. Use a question from the first ~20 words ---
    first_20 = " ".join(words[:20])
    q_match = re.search(r'[^.!?]*\?', first_20)
    if q_match:
        q = q_match.group(0).strip()
        if 10 < len(q) <= 70:
            return q[:1].upper() + q[1:]

    # --- 3. Look for curiosity/emotion-loaded opening ---
    first_8 = set(w.lower().strip(".,!?'\"") for w in words[:8])
    curiosity_hit = first_8 & CURIOSITY_WORDS
    emotion_hit = first_8 & EMOTION_WORDS
    if curiosity_hit or emotion_hit:
        # Use first sentence or first 10 words
        first_sentence = re.split(r'[.!?]', text)[0].strip()
        if len(first_sentence) > 70:
            first_sentence = " ".join(first_sentence.split()[:10]) + "..."
        return first_sentence[:1].upper() + first_sentence[1:] if first_sentence else ""

    # --- 4. Fallback: first sentence fragment, capped ---
    first_sentence = re.split(r'[.!?]', text)[0].strip()
    if len(first_sentence) > 5:
        if len(first_sentence) > 65:
            # Cut at a natural word boundary
            cut = first_sentence[:62].rstrip()
            last_space = cut.rfind(" ")
            if last_space > 30:
                cut = cut[:last_space]
            first_sentence = cut + "..."
        return first_sentence[:1].upper() + first_sentence[1:]

    # Ultra-fallback
    hook_phrase = " ".join(words[:8])
    if len(words) > 8:
        hook_phrase += "..."
    return hook_phrase[:1].upper() + hook_phrase[1:] if hook_phrase else ""


def _generate_explanation(candidate: ClipCandidate) -> str:
    """Generate an explanation of why this clip was picked."""
    explain = []
    if candidate.hook_strength >= 50:
        explain.append("Strong opening hook.")
    if candidate.curiosity_score >= 50:
        explain.append("Creates an information gap.")
    if candidate.emotional_intensity >= 50:
        explain.append("High emotional intensity/energy.")
    if candidate.narrative_completeness >= 50:
        explain.append("Complete narrative loop.")

    dead_time = candidate.__dict__.get("_dead_time", 0.0)
    if dead_time < 1.0:
        explain.append("Tight pacing, no dead time.")

    if 60 <= candidate.duration <= 90:
        explain.append("Ideal clip length.")

    if not explain:
        explain.append("Good momentum and pacing.")
    return " ".join(explain)
