# AI Stream Clipper map — a project on disk, and the per-project switches

Part of the clipper map; start at [`docs/clipper-map.md`](../clipper-map.md).

## A project on disk

```
data/clipper/<project_id>/
  source/      the original
  proxy/       proxy.mp4 — EVERY analysis pass reads this, never the original
  audio/       speech.wav, 16 kHz mono
  frames/      sampled JPEGs, capped at clipper_max_sampled_frames
  analysis/    signals, faces, regions, regions_by_segment, segments, atoms,
               promises, threads, graph, anchors, segment_types, candidates,
               meta, transcript.json, reasoning_run, selection_trace
               generations/<job>-a<attempt>/  one analysis attempt (OW1); the project reads the one projects.analysis_generation names
  exports/     <clip>.mp4 + <clip>.json sidecar + <clip>.ass
  previews/    low-res renders
  thumbs/      poster frames
```

`data/clipper/MANIFEST.md` lists every project and which stages are already
done. Regenerate it with `scripts/export_clipper_state.py`.

---

## Switches

Per-project keys in `clipper_settings`, each falling back to a `config.py`
default. Full table in `handover/archive/clipper/handoff-clipper-session-4.md`.

| switch | default | turns on |
|---|---|---|
| `dynamic_edit` | **ON** since 2026-08-17 | multi-shot export instead of one static split screen |
| `trim_silence` | off | dead-air removal from inside a window (§15) |
| `vision_review` | off | a vision model judges the rendered clip — the only part of the pipeline that spends money (~0.4 cents/clip on gpt-5.6-terra) |
| `auto_export` | 0 (off) | render the top N as soon as scoring finishes, instead of stopping at the board. With the source form's "don't wait for me" box, a pasted link becomes finished files with no second visit |
| `reasoning_mode` | resolved from config, `legacy` on a stock rig | which reasoning engine runs. Selectable: `legacy`, `llm_nominate`, `story_v1`, `story_v2_shadow`. Shadow computes and persists `shadow_rank` but keeps the delivered board legacy. `story_v2` is known but REFUSED until the golden review proves it better. Replaces `llm_select` + `reasoning_version`, still read for old projects; the form omits the key unless the user picks one, so it does not override server config |

`dynamic_edit` being on is what makes the rest of the multi-shot work reachable
— cuts on speech pauses, the wide gameplay framing, Pass D and the audio
ceiling all live on that path and nowhere else. It still falls back to the
static layout on a missing proxy, a plan the PLANNER built from fewer than two
shots, or any exception. Since R1 that threshold reads
`shot_count_before_merge`, not the surviving count: a clip whose only fault was
an invisible cut merges down to one shot, and counting that would have moved it
onto the static renderer — different crop, different captions, different
renderer version.

The current dynamic planner is **not production-approved** merely because v3
fixed geometry. The 58-export audit found 1,341 shots (29.3/minute), 116 exact
`fit → fit` no-op cuts, aggressive boundaries and source-awareness defects. R0
turned that audit into a gate and R1 removed the 116 from the planner — the
rendered exports still carry them until the pilots are re-rendered.
Implementation resumes at production-engine **Batch R2**, not by tuning
constants ad hoc.
