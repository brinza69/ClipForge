"""
ClipForge — AI Stream Clipper.

Turns a long video (VOD, podcast, interview, gaming stream) into ranked
vertical short-form candidates.

The modules here are deliberately DB-free and queue-free: they take paths and
plain dicts and return plain dicts, so every one of them is unit-testable
without a database, a job queue or a network. All the stateful work
(persistence, progress, cancellation) lives in `routers/clipper.py` and
`workers/clipper_pipeline.py`.

Pipeline shape (see docs/plans/ai-stream-clipper-architecture.md):

    ingest   → download/copy, build a 480p analysis proxy + 16 kHz mono audio
    signals  → Pass A: audio energy/peaks/silence, scene cuts, motion, faces
    segmentation → Pass B: semantic windows on sentence/pause/scene boundaries
    candidates   → Pass C: candidate windows + boundary refinement
    scoring      → sub-scores per content-type profile + a plain-English reason
    dedupe       → collapse near-duplicates, keep them as alternatives
    content_type → gaming/podcast/… detection + webcam/gameplay/chat/HUD regions
    layout       → 9:16 layout plan + one fused ffmpeg filtergraph
    captions     → word-synced caption plan that dodges faces and HUD
    headline     → optional 3-10 word context hook
    render       → one encode, 1080x1920
    feedback / ranker → learn a better ordering from what the user actually keeps

The cardinal rule: never run an expensive model over every frame of a
multi-hour stream. Pass A and B read only the proxy and the transcript; the
full-resolution source is opened once per exported clip.
"""

ANALYSIS_VERSION = "1"
