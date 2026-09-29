# AI Stream Clipper — the map

**Read this first.** Every file in the clipper, what it is for, and which
document holds the reasoning behind it. Verified against the tree on
2026-08-28.

It exists because four sessions in a row began by grepping the codebase to
rediscover where things live, and because the old map in `CLAUDE.md` listed
five files that do not exist.

> **When you add a file, add it to the right part of the map in the same commit.** A map that is only
> mostly true is worse than none: the next session trusts it and greps anyway.

---

## Where to start

| you want | read |
|---|---|
| the current state of the world, known problems, traps | `handover/areas/clipper/CURRENT.md` |
| what every file is (this document) | you are here |
| why the reasoning works the way it does | `story-engine.md` |
| planul auditat pentru Reasoning v2 | `plans/ai-stream-clipper-reasoning-v2.md` |
| următorul plan: selecție + montaj content-aware până la activarea în produs | `plans/ai-stream-clipper-production-engine-v1.md` |
| ce anume din acel plan a fost verificat în cod, și cu ce dovadă | `plans/ai-stream-clipper-reasoning-v2-review.md` |
| which brief requirement is built, section by section | `story-engine-spec-status.md` |
| ground truth for the detectors — what each source actually is | `source-labels.md` |
| how to run the pipeline by hand | `ai-stream-clipper-runbook.md` |
| the measured recipe behind the multi-shot edit | `dynamic-edit-recipe.md` |
| local reaction-source probes, what was decoded and what remains unverified | `refs/clipper-reaction-local-2026-09-13.md` |
| ce fac alte aplicații de clipping și ce merită construit mai departe (planul CA1–CA16) | `research/clipping-apps-survey-2026-09-29.md` |
| scorare, judge LLM, ranker și selecție: ce spune cercetarea (planul SL1–SL12) | `research/clipper-scoring-selection-2026-09-29.md` |
| testul în cloud pe un VOD Twitch: board vs chat, timpi, exportul blocat pe ffmpeg < 8, reluarea pe PC | `research/clipper-cloud-test-2026-09-29.md` (scripturile, în folderul cu același nume) |
| stream-uri de reacție: vocea streamerului vs conținut, decalajul și emote-urile din chat, logistica (planul RS1–RS8) | `research/clipper-reaction-signals-2026-09-29.md` |
| OpusClip desfăcut din surse publice: pipeline, modele de curare, stack probabil, protocolul black-box pe VOD-ul de test | `research/opusclip-reverse-engineering-2026-09-29.md` |
| Eklipse desfăcut din surse publice: motorul Gameplay Intelligence, semnale (HUD, audio pe trei piste, chat, comandă vocală), limite, ce merită luat | `research/eklipse-reverse-engineering-2026-09-29.md` |
| what is already on disk and can be skipped | `../data/clipper/MANIFEST.md` |

Older handoffs are history, superseded but not wrong about the code they
describe: `handover/archive/clipper/handoff-clipper-session-3.md`,
`handover/archive/clipper/handoff-clipper-session-2.md`,
`handover/archive/clipper/handoff-dynamic-edit.md`.

---

## The pipeline, in order

Six job types, registered in `workers/clipper_pipeline.py`. Each writes its
output to disk before the next starts, so a crash resumes rather than
re-downloading.

```
ingest  →  transcribe  →  analyze  →  score  →  export / preview
```

| stage | handler | writes |
|---|---|---|
| `clipper_ingest` | `clipper_pipeline.handle_ingest` | `source/`, `proxy/proxy.mp4`, `audio/speech.wav`, `meta` |
| `clipper_transcribe` | `clipper_pipeline.handle_transcribe` | the `transcripts` row |
| `clipper_analyze` | `clipper_pipeline.handle_analyze` | ONE generation `analysis/generations/<job>-a<claimed attempt>/` (`signals`, `faces`, `regions`, `regions_by_segment`, `frames_pts`, `frames/`, `generation.json` last); published only by `clipper_analysis_publish.publish_protected`, which sets `projects.analysis_generation` and schedules the score in one transaction |
| `clipper_score` | `clipper_build.handle_score` | `segments`, `atoms`, `promises`, `threads`, `graph`, `anchors`, `segment_types`, `candidates`, `reasoning_run`, `selection_trace`, the `clips` rows |
| `clipper_export` | `clipper_render_jobs.handle_export` | `exports/<clip>.mp4` + a `.json` sidecar |
| `clipper_preview` | `clipper_render_jobs.handle_preview` | `previews/<clip>.mp4` |

---

## The rest of the map

Each part below is a table moved here verbatim; this page keeps only the entry points.

| part | what it lists |
|---|---|
| [`clipper-map/services.md`](clipper-map/services.md) | `server/services/clipper/` — the logic, DB-free and unit-testable |
| [`clipper-map/workers-and-routers.md`](clipper-map/workers-and-routers.md) | `server/workers/` and `server/routers/` |
| [`clipper-map/frontend-and-scripts.md`](clipper-map/frontend-and-scripts.md) | the frontend and the scripts |
| [`clipper-map/tests.md`](clipper-map/tests.md) | the test files |
| [`clipper-map/disk-and-switches.md`](clipper-map/disk-and-switches.md) | a project on disk, and the per-project switches |
