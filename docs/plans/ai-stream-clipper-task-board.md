# AI Stream Clipper — Task Board

Status: `todo` · `wip` · `done` · `deferred`.
"Owner" is the agent role from brief §31 that carries the task; the lead agent reviews and
integrates every patch.

Legend for dependencies: a task cannot start until everything in *Depends on* is `done`.

---

## Phase 1 — Research & audit

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 1.1 | Git + repo audit | Repository Architect | done | — | `docs/plans/…repository-audit.md` | Baseline test run recorded (22 pass / 2 pre-existing fail) |
| 1.2 | Competitive research | Repository Architect | done | — | `docs/research/…competitive-analysis.md` | Sources cited; OBS/ASM/CF tagged |
| 1.3 | Architecture + contracts | Repository Architect | done | 1.1 | `docs/plans/…architecture.md` | 6 Mermaid diagrams; every module signature fixed |
| 1.4 | Decision log | Repository Architect | done | 1.3 | `docs/plans/…decisions.md` | 16 decisions with alternatives |
| 1.5 | Feature branch | Lead | done | 1.1 | — | `claude/ai-stream-clipper` off `claude/portable-setup` |

## Phase 2 — Core data & jobs

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 2.1 | Project + clip columns, `ClipFeedbackModel`, 6 `JobType`s | Backend | done | 1.3 | `server/models.py` | `init_db()` clean on an existing DB |
| 2.2 | Idempotent migrations | Backend | done | 2.1 | `server/database.py` | Re-run twice, no error |
| 2.3 | Config block | Backend | done | 1.3 | `server/config.py` | Settings import |
| 2.4 | Light-lane registration | Backend | done | 2.1 | `server/job_queue.py` | Lane membership asserted in tests |
| 2.5 | Disk artifact store | Backend | done | 1.3 | `services/clipper/storage.py` | Unit tests incl. atomic write + allowlist |

## Phase 3 — Ingestion & transcription

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 3.1 | URL policy / SSRF guard | Test & Security | done | 1.3 | `services/clipper/urlguard.py` | Unit tests: metadata IP, RFC1918, CGNAT, IPv6 loopback, creds, port, scheme |
| 3.2 | Ingestion (download/copy, proxy, audio, thumbs, frames) | Video Pipeline | done | 3.1, 2.5 | `services/clipper/ingest.py` | Disk-space + duration guards unit-tested |
| 3.3 | Punctuation-preserving transcription flag | Video Pipeline | done | — | `services/transcriber.py` | Existing callers byte-identical; new flag tested |

## Phase 4 — Candidate generation

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 4.1 | Pass-A signals | ML & Ranking | done | 3.2 | `services/clipper/signals.py` | Pure helpers unit-tested |
| 4.2 | Semantic windows | ML & Ranking | done | 4.1, 3.3 | `services/clipper/segmentation.py` | No fixed-size chunks; boundary reasons asserted |
| 4.3 | Candidates + boundary refinement | ML & Ranking | done | 4.2 | `services/clipper/candidates.py` | Never starts mid-word; duration bounds respected |
| 4.4 | Sub-scores + profiles + reason | ML & Ranking | done | 4.3 | `services/clipper/scoring.py` | 16 sub-scores; 0–100 clamp; profile weights sum-normalised |
| 4.5 | Dedupe + diversity | ML & Ranking | done | 4.4 | `services/clipper/dedupe.py` | Near-duplicates collapse; alternatives retained |

## Phase 5 — Layout & captions

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 5.1 | Content-type + gaming detection | Vision & Gaming | done | 4.1 | `services/clipper/content_type.py` | Overridable; confidence + evidence returned |
| 5.2 | Region detection (webcam/gameplay/chat/HUD) | Vision & Gaming | done | 5.1 | `services/clipper/content_type.py` | Returns `None` cleanly when absent |
| 5.3 | Layout planner + smoothing + filtergraph | Vision & Gaming | done | 5.2 | `services/clipper/layout.py` | Empty-face-band impossible; fallback tested |
| 5.4 | Caption plan + safe-area collision | Frontend Editor / Backend | done | 5.3 | `services/clipper/captions.py` | Collision nudge unit-tested |
| 5.5 | Headline + deterministic fallback | ML & Ranking | done | 4.4 | `services/clipper/headline.py` | Works with no LLM configured |

## Phase 6 — Review & editor (backend surface)

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 6.1 | Router | Backend | done | 2.*, 5.* | `server/routers/clipper.py` | Integration tests on every route |
| 6.2 | Worker pipeline + staged progress + retry | Backend | done | 6.1 | `server/workers/clipper_pipeline.py` | Stage names match architecture §3 |
| 6.3 | Wiring | Backend | done | 6.2 | `server/main.py` | App boots; smoke tests green |

## Phase 7 — Rendering & export

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 7.1 | Render spec (pure argv builder) | Video Pipeline | done | 5.3 | `services/clipper/render.py` | argv asserted without invoking ffmpeg |
| 7.2 | Export + preview jobs | Video Pipeline | done | 7.1, 6.2 | `workers/clipper_pipeline.py` | Single-encode assertion |

## Phase 8 — Feedback & learning

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 8.1 | Feedback event store | ML & Ranking | done | 2.1 | `services/clipper/feedback.py` | 15 event types persisted |
| 8.2 | Baseline ranker + eval + versioning | ML & Ranking | done | 8.1 | `services/clipper/ranker.py` | NDCG/precision maths unit-tested |

## Phase 9 — Frontend

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 9.1 | Types | Frontend Editor | done | 6.1 | `src/types/clipper.ts` | `tsc --noEmit` clean |
| 9.2 | Nav entry | Frontend Editor | done | — | `src/components/layout/sidebar.tsx` | Active-state matching |
| 9.3 | Project list + source form | Frontend Editor | done | 9.1 | `src/app/ai-stream-clipper/page.tsx`, `components/clipper/source-form.tsx` | Metadata preview before analysis; simplified to 3 above-the-fold decisions |
| 9.4 | Progress screen (SSE + poll fallback) | Frontend Editor | done | 9.3 | `components/clipper/analysis-progress.tsx` | Stage list derived from the job message; survives reload |
| 9.5 | Candidate dashboard: sort, filter, batch | Frontend Editor | done | 9.4 | `components/clipper/candidate-grid.tsx`, `candidate-card.tsx`, `score-breakdown.tsx` | Sort/filter/reveal-alternatives/bulk-approve |
| 9.6 | Clip editor: trim, captions, crop, headline | Frontend Editor | **not built** | 9.5 | `clip-editor.tsx`, `crop-editor.tsx`, `caption-controls.tsx` | **Backend is complete and reachable** (`PATCH /clips/{id}`, `/regenerate`, `/preview-frame`); the UI for it is not written. Editing is currently API-only. |

## Phase 10 — Hardening & delivery

| # | Task | Owner | Status | Depends on | Files | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| 10.1 | Unit + integration tests | Test & Security | done | all | `server/tests/test_clipper_*.py` | 8 files; 208 passing incl. 15 API integration tests |
| 10.2 | `.env.example` + runbook + CLAUDE.md update | Docs & Release | done | all | root + `docs/` | Every new env var documented |
| 10.3 | Quality gates | Lead | done | 10.1 | — | pytest 212 passed / 2 pre-existing failures · `tsc --noEmit` clean · eslint 0 errors · `next build` compiled in 71s with both routes emitted |
| 10.4 | Commit, push, draft PR | Lead | done | 10.3 | — | Pushed on `claude/ai-stream-clipper`. PR #27 merged 2026-08-10; PR #29 open since 2026-08-16 with everything after it. |

---

## Deferred (explicitly not built — see architecture §9)

| Item | Reason |
| --- | --- |
| Speaker diarisation + interview split-screen | Needs a diarisation model; not in the venv. |
| Natural-language moment search | Needs an embedding index + retrieval pass. |
| OCR timelines (killfeed / scoreboard) | Per-game fragility; `easyocr` cost per frame. |
| Live chat-replay ingestion | Needs per-platform chat APIs. |
| Intro/outro templates, full brand kit | Larger asset-management feature. |
| Multi-tenant campaigns / payouts | Out of scope for a single-user local studio (D-16). |
| Automated platform metric scraping | Against platform terms (brief §30). |
| Gradient-boosted ranker | Only justified once a real dataset exists (D-7). |
