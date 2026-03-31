"""
ClipForge — Momentum Score Engine
Multi-signal scoring system for viral clip candidate detection.

Analyzes transcript segments using weighted heuristics to find
the most engaging, hook-worthy moments in long-form content.
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
    "secret", "hidden", "exposed", "revealed", "shocking", "insane", "wild",
    "crazy", "unbelievable", "terrifying", "disturbing", "mysterious", "strange",
    "bizarre", "impossible", "banned", "forbidden", "classified", "conspiracy",
    "cover-up", "truth", "proof", "evidence", "theory", "ancient", "discovery",
    "breakthrough", "warning", "urgent", "dangerous", "powerful", "massive",
    "incredible", "stunning", "mind-blowing", "game-changer", "revolutionary",
    "catastrophic", "unprecedented", "nightmare", "apocalyptic", "extinction",
    "nibiru", "planet", "cosmic", "galactic", "alien", "ufo",
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
    1. Sliding window segmentation
    2. Candidate generation
    3. Multi-signal scoring
    4. Ranking
    5. Deduplication
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

    # Step 1: Generate candidate windows using sliding window
    candidates = _generate_windows(segments, min_dur, max_dur, target_dur)
    logger.info(f"Generated {len(candidates)} raw candidate windows")

    if not candidates:
        return []

    # Step 2: Score each candidate
    for candidate in candidates:
        _score_candidate(candidate, all_hook_patterns, all_boost_words, segments)

    # Step 3: Sort by momentum score
    candidates.sort(key=lambda c: c.momentum_score, reverse=True)

    # Step 4: Deduplicate (remove overlapping candidates)
    deduplicated = _deduplicate(candidates, settings.overlap_threshold)
    logger.info(f"After deduplication: {len(deduplicated)} candidates")

    # Step 5: Take top N
    top_candidates = deduplicated[:max_count]

    # Step 6: Generate titles
    for candidate in top_candidates:
        candidate.title = _generate_title(candidate)

    logger.info(f"Final candidates: {len(top_candidates)}")
    for i, c in enumerate(top_candidates):
        logger.info(
            f"  #{i+1}: [{c.start_time:.0f}s-{c.end_time:.0f}s] "
            f"Score={c.momentum_score:.1f} Hook={c.hook_strength:.1f} "
            f'"{c.title}"'
        )

    return top_candidates


def _generate_windows(
    segments: List[Dict],
    min_dur: float,
    max_dur: float,
    target_dur: float,
) -> List[ClipCandidate]:
    """Generate candidate clip windows using sliding window approach."""
    candidates = []
    n = len(segments)

    # Sliding window with different step sizes for coverage
    step_sizes = [3, 5, 8]

    for step in step_sizes:
        for i in range(0, n, step):
            # Try to build a window starting at segment i
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

                # Create a candidate at this point
                candidate = ClipCandidate(
                    start_time=start_time,
                    end_time=seg["end"],
                    duration=current_duration,
                    transcript_text=" ".join(window_text_parts),
                    transcript_segments=[s.copy() for s in window_segments],
                )
                candidates.append(candidate)

                # Prefer candidates near the target duration
                if current_duration >= target_dur:
                    break

    return candidates


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
    # Analyze the first ~15 words (first 3 seconds equivalent)
    first_words = " ".join(words[:15])
    hook_score = 0.0

    for pattern in hook_patterns:
        if re.search(pattern, first_words, re.IGNORECASE):
            hook_score += 30.0

    # Check if first sentence is a question (strong hook)
    first_sentence = text.split(".")[0] if "." in text else text[:100]
    for qp in QUESTION_PATTERNS:
        if re.search(qp, first_sentence, re.IGNORECASE):
            hook_score += 20.0

    # Check for bold opening words
    bold_openers = ["listen", "look", "okay", "so", "imagine", "picture", "think"]
    if words[0] in bold_openers:
        hook_score += 10.0

    # Starts with first-person or direct address
    if words[0] in ["i", "you", "we"]:
        hook_score += 5.0

    hook_score = min(hook_score, 100.0)

    # --- 2. Curiosity Score (20%) ---
    curiosity_score = 0.0
    curiosity_hits = sum(1 for w in words if w.strip(".,!?'\"") in boost_words)
    curiosity_density = curiosity_hits / max(word_count, 1)
    curiosity_score = min(curiosity_density * 800, 60.0)

    # Boost for information gaps / incomplete reveals
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

    # Exclamation marks and emphasis
    exclamation_count = text.count("!")
    emotion_score += min(exclamation_count * 5, 20.0)

    # ALL CAPS words (emphasis in transcript)
    caps_words = sum(1 for w in candidate.transcript_text.split() if w.isupper() and len(w) > 2)
    emotion_score += min(caps_words * 5, 15.0)

    # Contrast / contradiction patterns
    contrast_patterns = [r"\bbut\b", r"\bhowever\b", r"\bactually\b", r"\bin fact\b", r"\bcontrary\b"]
    for cp in contrast_patterns:
        if re.search(cp, text, re.IGNORECASE):
            emotion_score += 5.0

    emotion_score = min(emotion_score, 100.0)

    # --- 4. Narrative Completeness (15%) ---
    narrative_score = 0.0

    # Has a clear beginning (setup/context)
    setup_markers = [r"\bso\b", r"\bbasically\b", r"\bthe thing is\b", r"\bwhat happened\b"]
    for sm in setup_markers:
        if re.search(sm, first_words, re.IGNORECASE):
            narrative_score += 15.0
            break

    # Has development (middle content)
    if word_count > 30:
        narrative_score += 15.0

    # Has conclusion / punchline at end
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

    # Sentence count as proxy for structure
    sentence_count = len(re.split(r'[.!?]+', text))
    if 3 <= sentence_count <= 12:
        narrative_score += 15.0  # Good structure
    elif sentence_count > 12:
        narrative_score += 5.0  # Maybe too long / rambling

    # Bonus for complete thoughts
    if text.rstrip()[-1:] in ".!?" if text else False:
        narrative_score += 10.0

    narrative_score = min(narrative_score, 100.0)

    # --- 5. Speech Dynamics (10%) ---
    dynamics_score = 0.0

    # Analyze pauses between segments
    if len(segments) > 1:
        pauses = []
        for k in range(1, len(segments)):
            gap = segments[k]["start"] - segments[k-1]["end"]
            if gap > 0:
                pauses.append(gap)

        if pauses:
            avg_pause = sum(pauses) / len(pauses)
            # Strategic pauses (not too long, not too short)
            if 0.3 <= avg_pause <= 1.5:
                dynamics_score += 20.0
            # Dramatic pauses (1-3 seconds)
            dramatic_pauses = sum(1 for p in pauses if 1.0 <= p <= 3.0)
            dynamics_score += min(dramatic_pauses * 10, 30.0)

    # Speech rate variation
    if segments:
        rates = []
        for seg in segments:
            seg_dur = seg["end"] - seg["start"]
            seg_words = len(seg["text"].split())
            if seg_dur > 0:
                rates.append(seg_words / seg_dur)

        if len(rates) > 2:
            rate_variance = _variance(rates)
            # Some variation is good (emphasis changes)
            if rate_variance > 0.5:
                dynamics_score += 20.0

    # Duration proximity to target
    dur_ratio = candidate.duration / settings.target_clip_duration
    if 0.8 <= dur_ratio <= 1.2:
        dynamics_score += 20.0
    elif 0.6 <= dur_ratio <= 1.4:
        dynamics_score += 10.0

    dynamics_score = min(dynamics_score, 100.0)

    # --- 6. Caption Readability (10%) ---
    readability_score = 0.0

    # Average word length
    avg_word_len = sum(len(w) for w in words) / max(word_count, 1)
    if avg_word_len <= 6:
        readability_score += 30.0  # Short, punchy words
    elif avg_word_len <= 8:
        readability_score += 20.0

    # Average sentence length
    avg_sentence_len = word_count / max(sentence_count, 1)
    if avg_sentence_len <= 15:
        readability_score += 30.0
    elif avg_sentence_len <= 25:
        readability_score += 15.0

    # Has quotable phrases
    if re.search(r'"[^"]{10,}"', text):
        readability_score += 20.0

    # Word-level timestamps available
    has_word_timestamps = any(seg.get("words") for seg in segments)
    if has_word_timestamps:
        readability_score += 20.0

    readability_score = min(readability_score, 100.0)

    # --- 7. Topic Density (5% bonus) ---
    topic_bonus = 0.0

    # Named entities proxy: capitalized words
    caps_entities = sum(1 for w in candidate.transcript_text.split()
                       if w[0:1].isupper() and len(w) > 2 and w not in {"The", "And", "But", "This", "That"})
    if caps_entities > 3:
        topic_bonus += 10.0

    # Numbers / statistics
    numbers = re.findall(r'\b\d+[%,.]?\d*\b', text)
    if numbers:
        topic_bonus += min(len(numbers) * 5, 15.0)

    topic_bonus = min(topic_bonus, 100.0)

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

    candidate.momentum_score = round(min(weighted_score, 100.0), 1)

    # Confidence is based on data quality
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

    # Take first sentence
    first_sentence = re.split(r'[.!?]', text)[0].strip()

    # Truncate if too long
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
        # Too short, use more text
        words = text.split()[:10]
        return " ".join(words) + ("..." if len(text.split()) > 10 else "")

    return first_sentence
