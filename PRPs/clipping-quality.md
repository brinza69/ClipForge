# PRP: Clipping Quality (Batch 3)

## Goal
Improve the quality of auto-generated clips:
1. Dead-time reduction — skip long pauses (>0.8s) at clip boundaries
2. Smarter contiguous extraction — prefer clips that don't cut mid-sentence
3. Better hook text — more viral/punchy, uses actual opening words
4. Smoother transitions — trim leading/trailing silence within a clip

## Why
- Current clips often start/end in the middle of silence, causing awkward cuts
- Hook text is often generic; should use the first striking phrase from the clip
- Dead time at start hurts viewer retention for short-form content

## What

### Dead-time reduction (`services/scorer.py`)
After scoring selects clip boundaries, trim leading/trailing silence:
- Read `clip.transcript_segments` words
- Find first word that starts >= clip.start_time → trim clip start to (word.start - 0.1s) clamped to 0
- Find last word that ends <= clip.end_time → trim clip end to (word.end + 0.15s) clamped to duration
- Skip if word data unavailable

### Better hook text (`services/scorer.py`)
In the hook-text generation prompt:
- Extract the first 2-3 sentences from the clip's transcript
- Use them as context for the hook (currently uses full transcript)
- Add instruction: "Write as if you're the person in the video speaking the first line"
- Shorter, more punchy: max 8 words

### Smarter contiguous extraction
- When a clip boundary falls mid-word, extend to next sentence boundary
- Use transcript segment `.end` timestamps rather than arbitrary scoring windows

## Files to Modify

```
server/services/scorer.py          — dead-time trim, better hook prompt, sentence-boundary snap
```

## Key Functions in scorer.py
- `_score_clips()` or equivalent — where clip start/end are finalized
- Look for the section that sets `start_time` and `end_time` on ClipModel

## Gotchas
- Don't trim if word timestamps are missing (older transcripts may lack word-level timing)
- Keep dead-time trim small — too aggressive causes cut-off words
- Hook text LLM call: check if it uses OpenAI or local model. If local, keep prompt short.
