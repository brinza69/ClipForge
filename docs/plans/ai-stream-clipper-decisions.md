# AI Stream Clipper — Decision Log

Format: **D-n · decision · alternatives considered · why · consequence.**
Append-only. Newest decisions go at the bottom.

---

### D-1 · Revive `ClipModel` instead of creating a new candidate table
**Alternatives:** (a) new `clipper_clips` table with a clean schema; (b) disk-JSON project like
doodle/tiktok; (c) reuse the existing `clips` table.
**Why (c):** `clips` already has `start_time`/`end_time`/`duration`, six score columns, a
`ClipStatus` enum with exactly the states we need, `transcript_segments`, `hook_text`,
`explanation`, `reframe_mode`/`reframe_data` and export paths. `ProjectStatus` already has
`scoring`/`ready`; `JobType` already has `score`/`export`/`full_pipeline`. It is an orphaned
first attempt at this exact feature. Brief §3 says explicitly not to rewrite working systems for
cleanliness. **Consequence:** we inherit ~40 legacy style columns we do not use, and the
duplicate-column wart in §3 of the audit. Accepted — they are nullable and inert.

### D-2 · Hybrid persistence: SQL rows for entities, disk JSON for analysis artifacts
**Alternatives:** everything in SQLite; everything on disk.
**Why:** `CLAUDE.md` rule 4 names both patterns. Candidates need sorting/filtering/joining, so
they are rows. Signal timelines, face tracks and word lists are megabyte-scale blobs that would
bloat every `SELECT *` and are read as whole files anyway — so they are files, mirroring
`services/doodle/storage.py` and `services/tiktok_transform/storage.py`.
**Consequence:** two places to clean up on delete; `storage.delete_project()` handles the disk
side and the router deletes rows.

### D-3 · Do **not** add an auth/ownership layer
**Alternatives:** add users + ownership columns to satisfy brief §27's "never expose one user's
media to another user".
**Why:** ClipForge has *no* auth anywhere — no user table, no sessions, CORS bound to
`localhost:3000`, a local SQLite file. Adding ownership columns with no authentication in front
of them is security theatre: it would *look* like access control while enforcing nothing.
**Consequence:** documented explicitly in the audit (§9) and the runbook as an accepted property
of a single-user local studio. If ClipForge ever becomes multi-user, this is a prerequisite, not
an afterthought — noted as such.

### D-4 · Add SSRF protection even though there is no auth
**Alternatives:** skip it (it is a local single-user app).
**Why:** the *absence* of auth makes it worse, not better. The backend binds `0.0.0.0:8420` and
happily fetches any URL handed to it. Without a guard it is an open proxy into the user's LAN
and into cloud metadata endpoints. This is the one genuine security hole the feature would
otherwise add. **Consequence:** `urlguard.py` resolves the host and rejects every private /
loopback / link-local / CGNAT / multicast / reserved address, restricts scheme and port, and
caps redirects. Direct-IP private URLs are refused with a clear message rather than silently
failing.

### D-5 · Transcription: add an opt-in `keep_punctuation` flag rather than a second transcriber
**Alternatives:** (a) a separate clipper-only transcription function; (b) change `_clean_text`
globally; (c) post-hoc punctuation restoration.
**Why (a is duplication, b breaks every existing caption)**: `transcriber._clean_text()` strips
all punctuation and lowercases — deliberate for the caption path, fatal for a clipper that needs
sentence boundaries and question detection. A default-`False` keyword argument threaded through
`transcribe()` → worker → segment builder preserves byte-identical behaviour for all existing
callers while giving the clipper real text. **Consequence:** one additive parameter in a shared
module; existing tests unaffected; the flag is covered by a new unit test.

### D-6 · Heuristic scoring first, learned ranking only when it earns its place
**Alternatives:** ship a learned model immediately; ship only heuristics.
**Why:** brief §29 says outright not to claim a trained model without a real dataset. On day one
there is no feedback. So: transparent, weighted heuristics per content-type profile, every
sub-score exposed, plus an append-only feedback store. The learned ranker trains on demand and
is used **only** if ≥40 labelled clips exist *and* it beats the heuristic on held-out NDCG@5.
**Consequence:** the score is honest and explainable from the first run; the UI states which
ranker produced it.

### D-7 · Pure-numpy logistic regression instead of scikit-learn / XGBoost
**Alternatives:** add scikit-learn (~30 MB + scipy) or xgboost.
**Why:** the venv already carries torch+CUDA, transformers, kokoro, easyocr and opencv with
pinned versions (`requirements.txt` documents a fragile transformers/tokenizers/hub pin). Adding
scipy/sklearn into that for one small model is real dependency risk for little gain. A pairwise
logistic ranker over ~20 features is ~120 lines of numpy. **Consequence:** no new heavy
dependency; `numpy` promoted from transitive to explicitly declared. Gradient-boosted ranking is
noted as a future upgrade if the dataset ever justifies it.

### D-8 · Lexical similarity for dedupe, not embeddings
**Alternatives:** sentence-transformers embeddings.
**Why:** the duplicates we must catch are *near-identical transcript windows over the same
moment* — token Jaccard × trigram cosine catches those reliably. Semantic-but-differently-worded
duplicates are rarer and the cost (a new model download + inference pass) is high.
**Consequence:** documented as a known limitation; embedding similarity is the named upgrade
path in brief §14 terms.

### D-9 · Pass D (LLM) is optional and bounded
**Alternatives:** require an LLM; run it on every candidate.
**Why:** brief §32 says not to block implementation because an optional provider is unavailable,
and §8 says never run the expensive model over everything. **Consequence:** Pass D runs on at
most `clipper_top_n_llm` (default 8) candidates, reuses the **existing** `transcript_cleaner`
engine abstraction (ollama/openai/anthropic), and is skipped entirely when no engine is
configured. Headlines then come from a deterministic extractive fallback. The pipeline never
fails because an LLM is absent.

### D-10 · OpenCV Haar cascades for face/webcam detection, not a DNN
**Alternatives:** YOLO/RetinaFace/mediapipe.
**Why:** `opencv-python` is already a declared dependency and ships the Haar cascade XMLs inside
the wheel (`cv2.data.haarcascades`) — **zero** new dependencies and **zero** model downloads. We
run it on sparse sampled frames from a small proxy, where its weaknesses (small faces, profile
views) matter least, and we already treat detection as advisory with a manual override.
**Consequence:** lower recall than a DNN on hard frames. Mitigated by temporal aggregation
across samples and by the user override. Documented as an upgrade path.

### D-11 · One fused ffmpeg encode per export
**Alternatives:** crop pass → caption pass (simpler to write).
**Why:** `remix_pipeline._stage_match_and_caption` already establishes this rule in-repo with an
explicit comment: a second encode adds a generation of compression loss. **Consequence:**
`build_filtergraph()` is more complex, so it is a **pure function returning a string/argv** and
is unit-tested without running ffmpeg.

### D-12 · Analysis job types join the light queue lane
**Alternatives:** put everything in the heavy lane.
**Why:** `job_queue.DOODLE_LANE_TYPES` exists precisely because GPU-bound video work starves
API/CPU-light work, making the UI look frozen. Clipper analysis/scoring/preview are exactly that
shape. **Consequence:** `clipper_analyze`, `clipper_score`, `clipper_preview` join the light
lane; `clipper_ingest`, `clipper_transcribe`, `clipper_export` stay heavy.

### D-13 · Reuse the existing SSE job stream instead of adding WebSockets
**Alternatives:** a new WebSocket channel.
**Why:** `GET /api/jobs/{id}/stream` already exists, already emits on change, already closes on
terminal states, and the sidebar badge already consumes the jobs API. Brief §26 says to use
existing conventions. **Consequence:** no new transport, no new client library, progress
survives reload for free.

### D-14 · Branch `claude/ai-stream-clipper`, not `feature/ai-stream-clipper`
**Why:** every branch on the remote uses `claude/<topic>` and PRs merge to `main`. The brief
says to follow repository convention over its own suggestion. Base is
`claude/portable-setup` (a strict superset of `main`: 8 ahead, 0 behind), so the PR diff shows
only this work.

### D-15 · Score is presented as a within-source ranking, never as a validated prediction
**Why:** brief §15 forbids calling it scientifically proven, and public reviews of the closest
comparable product report its score is an unreliable absolute predictor. **Consequence:** the UI
labels it "Rank score" with the sub-score breakdown always one click away, and the docs say
plainly that it has not been validated against real performance data.

### D-16 · Do not build campaign/creator/team organisation
**Why:** the reference product the brief cites for these concepts turned out to be a per-view
clipper *marketplace*, not a workflow tool for a single creator (see the competitive analysis
§0). Building multi-tenant campaign management for a single-user local studio is exactly the
speculative abstraction `CLAUDE.md` rule 8 forbids. **Consequence:** the performance-metric
intake (brief §30) is built, because that is the useful part; the marketplace around it is not.
