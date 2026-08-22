# Handover — AI Stream Clipper

## Scop

Transformă un VOD sau un videoclip lung într-o listă de clipuri verticale candidate, clasificate și exportabile.

## Flux funcțional

```text
source → ingest → proxy/audio → transcription → analysis → scoring → candidates → preview/export
```

## Frontend

- `src/app/ai-stream-clipper/page.tsx` — lista proiectelor și creare proiect.
- `src/app/ai-stream-clipper/[id]/page.tsx` — pagina proiectului și polling status.
- `src/components/clipper/source-form.tsx` — preview URL, upload, creare proiect și start analysis.
- `src/components/clipper/analysis-progress.tsx` — SSE cu fallback polling.
- `src/components/clipper/clip-editor.tsx` — editare settings și regenerare.
- `src/components/clipper/reasoning-mode-field.tsx` — selectorul de mod; trimite `reasoning_mode`
  doar când utilizatorul alege explicit, altfel config-ul rig-ului ar fi suprascris.
- `src/components/clipper/candidate-grid.tsx` — afișare și acțiuni candidate.

## Backend/API

- `server/routers/clipper.py` — preview și upload source, create/list/get/delete project.
- `server/routers/clipper_settings.py` — contractul de settings: `_default_settings`,
  `_normalise_settings` și modurile refuzate. Fără rute; ambele intrări HTTP trec prin el.
- `server/routers/clipper_runs.py` — start/cancel/retry analysis, artifacts, presets,
  ranker status/train. Montat sub același prefix; niciun URL nu s-a schimbat.
- `server/routers/clipper_clips.py`
  - get/patch clip;
  - approve/reject/regenerate/export;
  - preview/export file;
  - feedback, performance și events;
  - list clips.

## Worker și servicii

- `server/workers/clipper_pipeline.py` — orchestration pipeline.
- `server/workers/clipper_build.py` — build candidate/clip data.
- `server/workers/clipper_judging.py` — rundele de judge peste pool-uri de momente.
- `server/workers/clipper_finalize.py` — layout, captions, headlines, trace, `clips` rows, auto-export.
- `server/workers/clipper_cache.py` — ce a lăsat o rulare anterioară pe disc și dacă mai e de încredere.
- `server/workers/clipper_render_jobs.py` — preview și export jobs.
- `server/workers/clipper_render_plan.py` — planuri de render.
- `server/services/clipper/` — logică DB-free, testabilă unitar.
- `server/services/clipper/storage.py` — artefacte, paths și cleanup.
- `server/services/clipper/ffmpeg_tools.py` — probe și comenzi FFmpeg.

## Persistență și output

- DB: proiecte, job-uri, clips, transcript și feedback.
- Disc: source, proxy, transcript, analysis, scoring, previews și exports.

## Stare, 2026-08-22

**Reasoning v2 este implementat până la Batch 6 inclusiv.** Rulează în `story_v2_shadow`, care
calculează ordinea v2 și o înregistrează, dar livrează în continuare ordinea legacy. `story_v2` este
**refuzat de API** — regula funcționează, dar nu a fost comparată orb pe corpus, ceea ce este Batch 10.

Ce s-a livrat, cu măsurătoarea care justifică fiecare. **Fiecare rând este snapshot-ul de la
momentul batch-ului respectiv, nu o singură rulare** — numărul de candidați diferă de la un batch la
altul (909 → 943 → 946) fiindcă fiecare batch a schimbat ce se produce. Starea artefactului curent e
mai jos.

| batch | ce repară | snapshot la data batch-ului, `gateslice4h` |
|---|---|---|
| 1 | `reasoning_mode`, o singură setare | story engine-ul era **inaccesibil din API**; ambele chei vechi erau aruncate tăcut |
| 2a | `origin` la feedback | 43 de rânduri de antrenare, **toate cu eticheta 1.0**; acum 0 |
| 0 | `reasoning_run.json`, `selection_trace.json` | acoperire și fallback-uri, înainte nemăsurabile |
| 3 | `story_evidence`, remeasure | payoff semantic vs mecanic: mediană 5–7s, maxim 61s; metrici stale acum **0** |
| 4 | chunking pe ceas | 2 chunk-uri (3h21m + 38m) → **6 de ~45m**, zero goluri; cel mai timpuriu payoff 2.95h → **0.11h** |
| 5 | momente, nu variante | 943 variante → **295 grupuri de momente**; variante story care poartă verdict **1 → 47 din 61** |
| 2b | board = ce a ales judge-ul | 7/10 câștigători legacy erau `not_evaluated`; sub regula v2, **0** — v2 nu are backfill |
| 6 | patru scale de scor, eligibilitate separată | exact **80** din 946 au `overall != heuristic_score`, și aceia sunt pool-ul judecat |

## Starea artefactului curent

Recalculat din `analysis/selection_trace.json`, run `99105dc0fd5f` — **acestea sunt cifrele de
comparat cu o rulare nouă**, nu cele din tabelul de mai sus:

**Atenție: sunt două grupări diferite și nu trebuie amestecate.** `dedupe_group` din
`selection_trace.json` grupează **tot câmpul**; `judge_pool_moments` din `reasoning_run.json` este
shortlist-ul **plafonat** trimis la judge. De aceea 304 > 80 — plafon, nu propagare. Propagarea
explică altceva: de ce 109 grupuri conțin un verdict deși numai 80 au mers la judge.

| metrică | valoare | sursă |
|---|---|---|
| candidați (variante) | 946 | `selection_trace` |
| `dedupe_group` distincte | 304 | `selection_trace` |
| dintre ele, cu cel puțin o variantă judecată | 109 | `selection_trace` |
| momente trimise la judge | 80, din care 19 story | `reasoning_run.judge_pool_moments` |
| variante judecate | 365 (114 `selected`, 251 `not_selected_in_judged_pool`) | `selection_trace` |
| variante story | 64, din care 50 cu verdict | `selection_trace` |
| runde de pool | 1 | `reasoning_run.counts` |
| câștigători | 10, `eliminated=0` | `selection_trace` |

Deosebirea variantă/grup contează: „47 din 61" din tabelul Batch 5 numără **variante** care poartă
verdict, nu momente distincte.

`pool_rounds` se numără de la unu (`clipper_judging` notează `round_index + 1`): **0 = judge-ul nu a
rulat deloc**, 1 = pool-ul a fost acceptat după prima rundă, 2 = a fost nevoie de a doua. Nu citi 0 ca
„acceptat din prima".

`selection_trace.json` de pe disc raportează `pool_rounds: 0` pentru această rulare judecată — este
**greșit**, un default care nu era transmis; rularea a avut 1 rundă, așa cum scrie `reasoning_run.json`.
Reparat, dar artefactul existent păstrează cifra veche: la o comparație, ia `pool_rounds` din
`reasoning_run`.

## Ce NU este închis

- **Grounding coverage — metrică fără prag, și nu are voie să fie confundată cu context coverage.**
  Sunt două lucruri diferite și numai unul are gate:
  - *context coverage* întreabă dacă fereastra aleasă **conține** momentele de context necesare.
    Gate-ul Batch 3 e pe ea și a **trecut**: 8/8 și 15/15, ≥95%.
  - *grounding coverage* întreabă dacă afirmațiile pot fi **legate de transcript**. Din 48 de
    afirmații distincte pe `gateslice4h`: 27 grounded strict, 11 ar fi dacă promptul ar cere
    `atom_ids`, 5 potriviri relaxate încă nevalidate, iar **5 (10%) nu au nicio potrivire locală
    în ±120s**. Rândurile spun **ce a găsit matcher-ul, nu de ce**: „fără potrivire locală" nu
    înseamnă „inventat", iar cele 11 nu înseamnă „prompt greșit" până nu trece resolver-ul
    determinist — sunt cauze pe care măsurătoarea actuală nu le poate separa.
  Nu recalibra pragul de 95% pe cifra de 27/48 — măsoară altceva.

  Cele 11 **nu** sunt „atomi numiți greșit": `matched_by` este `timestamp` pentru toate cele 111
  afirmații, adică modelul nu a numit niciun atom și `atom_ids` sunt completate de noi din fereastră.
  Sunt citate exacte, găsite local, dar nelegate canonic de un atom. Ordinea de reparare, în ordinea
  asta: resolver determinist (caută citatul în tokeni întregi, salvează `matched_t` și driftul,
  potrivirile multiple rămân `ambiguous`) → remăsurare pe aceleași ancore cached → A/B de prompt cu
  `atom_ids` **doar dacă mai rămâne nerezolvat** → prag lexical calibrat pe holdout.
  Vezi `scripts/measure_grounding.py`.
- **`eligibility` nu e citită de nimeni.** Se scrie și se înregistrează; nicio decizie nu depinde de
  ea, iar `ineligible` nu s-a declanșat niciodată pe acest corpus (0 din 946).
- **Inerția lui shadow e parțială.** `apply_ranking` mută `overall` pentru pool-ul de 80 —
  comportament pre-2b, identic în `story_v1`. Ca `overall` să devină alias, trebuie decis dacă
  `dedupe` se mută pe `heuristic_score`; el își alege liderii după `overall` și exact asta
  retrograda candidații judecați.
- **Batch 8 e blocat pe date.** `training_rows()` întoarce 0 de la 2a încoace, corect: un set cu o
  singură clasă e mai periculos decât niciunul. Ranker-ul rămâne dormant.
- **Batch 7, 9 și 10 nu au început.** 10 cere etichetare umană.

## Riscuri de urmărit

- două worker-e pe aceeași DB pot revendica sau finaliza greșit un job;
- retry-ul trebuie să invalideze artefactele vechi corect;
- cleanup-ul nu trebuie să șteargă exporturi valide;
- uploadul prin proxy are o limită diferită de limita backend-ului;
- job-urile de export trebuie să fie idempotente;
- **clonele `gate2d3375` și `gateslice4h` sunt singurele proiecte cu trace** și deci baza de
  comparație pentru orice batch următor. Se șterg cu
  `scripts/clone_clipper_project.py --drop <id>`.

## Documente asociate

- [`docs/clipper-map.md`](../../../clipper-map.md)
- [`docs/ai-stream-clipper-runbook.md`](../../../ai-stream-clipper-runbook.md)
- [`Reasoning v2 — audit și plan de consolidare`](../../../plans/ai-stream-clipper-reasoning-v2.md)
- [`docs/refs/reasoning-baseline-2026-08-21.json`](../../../refs/reasoning-baseline-2026-08-21.json) — baseline-ul de comparație
- [`handoff-clipper-session-4.md`](../../archive/clipper/handoff-clipper-session-4.md) — istoric detaliat
