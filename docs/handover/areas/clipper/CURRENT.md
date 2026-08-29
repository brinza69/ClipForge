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

## Stare, 2026-08-28

**Reasoning v2 este implementat până la Batch 6 inclusiv.** Rulează în `story_v2_shadow`, care
calculează ordinea v2 și o înregistrează, dar livrează în continuare ordinea legacy. `story_v2` este
**refuzat de API** — regula funcționează, dar review-ul existent a fost contaminat de defectele de
randare și nu poate deschide gate-ul Batch 10. Shadow este acum inert: verdictul judge-ului merge în
`selection_score`, ordinea livrată rămâne cea euristică, iar alegerile v2 se păstrează separat prin
`shadow_rank` și `shadow_run_id`.

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

## Ce s-a schimbat după snapshot-ul Batch 6

### Pilot multi-gen și review orb

- `story_v2_shadow` a rulat pe patru surse diverse: talking-head EN, vlog IRL în română, interviu
  și Just Chatting, plus baseline-ul gaming. Motorul a produs momente story pe toate tipurile și
  româna nu a produs colaps. Acesta este un diagnostic de coverage, **nu dovadă de calitate**.
- Pagina `/clipper-review` compară legacy cu v2 fără a expune board-ul în payload înainte de verdict.
  Sesiunile sunt persistente și fiecare este ștampilată cu versiunea rendererului.
- Prima sesiune a fost invalidă: ruta servise preview-ul de maximum 12s în locul exportului complet.
  Ruta cere acum exportul și răspunde 409 dacă lipsește; sesiunea veche rămâne marcată invalid.
- Corpusul de review are 58 de clipuri: `pilotf81b` 15, `pilotee0e` 14, `pilot6b38` 15,
  `pilot2c8a` 14. Board-urile diferă aproape complet, deci review-ul nu este formalitate.
- Review-ul pe `render_v2_subject_aware` a raportat probleme tehnice la **45/58**. Orice precizie
  legacy/v2 calculată pe acele sesiuni amestecă selecția cu randarea și nu aprobă `story_v2`.

### Rendererul curent

`RENDER_VERSION = render_v3_letterbox`, rezultat din commiturile `24862b7` → `b6e7ea7`:

- shot-urile au `composition: crop | fit`;
- `fit` păstrează cadrul complet, cu o copie blurată în fundal în locul benzilor negre;
- `stable_track` ancorează familia face-cam pe clusterul fix al creatorului când dovada este
  decisivă;
- rendererul și planificarea au fost separate în `dynamic_cuts.py`, `dynamic_geometry.py` și
  `dynamic_subject.py`, iar fișierele de producție rămân sub 500 de linii;
- versiunea randării este ștampilată la crearea sesiunii de review, nu reconstruită retroactiv.

Toate cele 58 de exporturi au fost re-randate și verificate: **58/58 complete**, decalaj maxim între
durata DB și MP4 **0,030s**; pe șase clipuri cu `fit`, luminozitatea benzii este 53–183, deci fundal
blur, nu negru. Geometria este închisă; plannerul nu este.

### Audit critic al celor 58 de exporturi v3

| metrică | rezultat | ce demonstrează |
|---|---:|---|
| durată totală | 45m43s | corpusul randat |
| shot-uri | 1.341 | **29,3/minut**, aproximativ unul la 2,04s |
| cel mai scurt shot | 0,605s | aceeași gramatică agresivă pe toate tipurile |
| tăieturi `fit → fit` fără schimbare finală | **116** | dedupe-ul rulează înainte de compoziția finală |
| început fără lead-in | **58/58** | fiecare clip pornește exact pe primul cuvânt |
| final la ≤50ms după ultimul cuvânt | **22/58** | boundary fără aer; uneori propoziție incompletă |
| captions duble pe go ghost | **15/15** | sursa are deja subtitrări arse |
| UI/overlays în clipurile Moist | **14/14** | `stable_track` nu garantează singur creatorul |

Concluzia: v3 a reparat întinderea și letterbox-ul, dar motorul nu este încă production-ready.
Problemele dominante sunt montajul forțat, echivalența calculată prea devreme, regimul `crop|fit`
bazat pe orice față, boundaries agresive și captions fără source-awareness.

## Batch R0 — ÎNCHIS, 29 august 2026

Evaluatorul repetabil există și reproduce baseline-ul exact. Rulează-l înainte și după orice
modificare a plannerului:

```bash
python scripts/audit_clipper_exports.py pilotf81b pilotee0e pilot6b38 pilot2c8a
```

| metrică | valoare | unde |
|---|---:|---|
| clipuri | 58 | cele patru piloturi |
| shot-uri | 1.341 | 29,3349/min **pooled** |
| aceleași shot-uri, media celor patru surse | 29,5930/min | altă întrebare, nu alt răspuns |
| shot minim | 0,605s | |
| tăieturi `fit → fit` echivalente | 116 | echivalență **exactă**, nu perceptuală |
| granițe undecidable / necontigue | 0 / 0 | |
| clipuri care încep pe primul cuvânt | 58/58 | |
| clipuri cu ≤50ms după ultimul cuvânt | 22/58 | |

Ce trebuie știut înainte să te bazezi pe el:

- **Scriptul este un gate, nu un raport: iese cu 2.** Export incomplet, sidecar care numește alt clip
  sau alt proiect, artefact refuzat, shot-uri care nu se leagă, fingerprint care nu mai corespunde
  planului. `fingerprint: unavailable` NU pică — cele 58 de exporturi preced cheia.
- **Absent și corupt sunt lucruri diferite, peste tot.** `composition`, `duration` și `drop_spans`
  lipsă înseamnă necunoscut și nu pică gate-ul; prezente și imposibile sunt defecte. Un plan vechi
  este vechi, nu stricat, iar corpusul e plin de ele.
- **O măsurătoare lipsă este `unavailable`, niciodată 0**, iar un total care ar fi doar o limită
  inferioară se raportează ca `unavailable`, cu limita publicată separat.
- **`captions_duplicate_declared` iese `unavailable` pe tot corpusul, și e corect.** Nimic din
  sidecar nu declară că sursa avea deja subtitrări arse; cei 15/15 pe go ghost au fost o observație
  umană. R6 trebuie să producă semnalul.
- **Un trim refuză jumătatea bazată pe shot-uri.** `drop_spans` schimbă montajul, nu doar ceasul, iar
  evaluatorul marchează `trimmed_edit_not_reconstructed` în loc să ghicească. Reconstrucția secvenței
  livrate este **Batch R8**, un batch propriu — nu a lui R1, care este despre echivalență. Ceasul,
  lead-in-ul și tail-ul rămân exacte.
- Sidecar-ul poartă acum `render_version` (care renderer a rulat, static sau dinamic),
  `input_fingerprint`, `drop_spans`, `caption_y` și dimensiunea sursei. Nimic nu este ștampilat
  retroactiv.

Ce a rămas deliberat în afara R0: `width`/`height` nu sunt în sidecar, fiindcă nu există o autoritate
comună pentru dimensiunea de ieșire — ambele renderere o poartă ca default de parametru. Se rezolvă
cu o constantă comună transmisă explicit ambelor căi, într-un batch ulterior.

## Batch R1 — ÎNCHIS, 29 august 2026

O tăietură există numai dacă imaginea livrată se schimbă. Cheia nu mai este dreptunghiul planificat,
ci ce emite rendererul: timeline-ul de dimensiuni plus expresiile de poziție. Pe cele 58 de planuri,
**1.341 shot-uri devin 1.225** — exact cele 116 tăieturi invizibile, zero rămase:

```bash
python scripts/build_shot_merge_fixture.py
```

Trei lucruri de reținut:

- **Auditul R0 va raporta în continuare 116 pe exporturile existente.** El citește sidecar-urile
  randate, iar R1 a schimbat plannerul. Cifra devine 0 abia după re-randarea piloturilor.
- **Un shot care se mișcă nu se unește niciodată** — la tăietură mișcarea ar reporni. Un shake
  identic se unește: expresia folosește timpul absolut, deci continuă neîntreruptă peste joncțiune.
- **Merge-ul păstrează `shot_count_before_merge`.** `clipper_render_plan` respinge planurile cu mai
  puțin de două shot-uri și le randează static; fără provenență, un clip a cărui singură vină era o
  tăietură invizibilă și-ar fi schimbat rendererul, crop-ul și captions-urile.

## Punctul exact de reluare

Următoarea sesiune începe cu **Batch R2** — resolverul de profile și controlul din aplicație — din
[`ai-stream-clipper-production-engine-v1.md`](../../../plans/ai-stream-clipper-production-engine-v1.md),
acum versionat în repo.

Nu porni Batch R2–R8 în paralel și nu activa `story_v2`. Planul separă gate-ul de selecție de gate-ul
de randare tocmai fiindcă review-ul existent le-a amestecat.

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
    afirmații distincte pe `gateslice4h`: 27 grounded strict, 11 potriviri exacte locale fără
    legătură canonică, 5 potriviri relaxate încă nevalidate, **5 (10%) fără nicio potrivire locală
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
- **Gate-ul de activare rămâne deschis.** Shadow nu mai mută board-ul livrat, dar cele 58 de
  verdicte existente nu pot demonstra calitatea selecției cât timp 45 au fost evaluate cu defecte
  tehnice. `story_v2` rămâne refuzat până la review-ul separat din Batch S8.
- **Batch 8 e blocat pe date.** `training_rows()` întoarce 0 de la 2a încoace, corect: un set cu o
  singură clasă e mai periculos decât niciunul. Ranker-ul rămâne dormant.
- **Reasoning Batch 7, 9 și 10 nu sunt închise.** Noul plan le continuă ca S7/S8 după stabilizarea
  randării; nu se șterg și nu se consideră înlocuite.
- **Rendererul v3 nu este aprobat pentru publicare automată.** Geometria trece, dar auditul a găsit
  116 tăieturi invizibile, ritm de 29,3/min, boundaries fără padding, captions duble și browser UI.

## Riscuri de urmărit

- două worker-e pe aceeași DB pot revendica sau finaliza greșit un job;
- retry-ul trebuie să invalideze artefactele vechi corect;
- cleanup-ul nu trebuie să șteargă exporturi valide;
- uploadul prin proxy are o limită diferită de limita backend-ului;
- job-urile de export trebuie să fie idempotente;
- nu porni o nouă sesiune de review înainte ca exporturile și sesiunea să poarte aceeași
  `RENDER_VERSION`;
- nu folosi verdictul tehnic drept etichetă negativă pentru ranker-ul de selecție;
- **șase proiecte au perechea completă de trace**: `gate2d3375`, `gateslice4h` și cele patru
  piloturi `pilotf81b`, `pilotee0e`, `pilot6b38`, `pilot2c8a`. Gate-urile păstrează baseline-ul
  gaming; piloturile păstrează corpusul multi-gen și exporturile review-ului. Clonele se șterg cu
  `scripts/clone_clipper_project.py --drop <id>`.

## Documente asociate

- [`docs/clipper-map.md`](../../../clipper-map.md)
- `scripts/audit_clipper_exports.py` — gate-ul R0; metricile sunt în
  `server/services/clipper/edit_quality.py`
- [`docs/ai-stream-clipper-runbook.md`](../../../ai-stream-clipper-runbook.md)
- [`Reasoning v2 — audit și plan de consolidare`](../../../plans/ai-stream-clipper-reasoning-v2.md)
- [`Motor de selecție și montaj content-aware — plan de producție v1`](../../../plans/ai-stream-clipper-production-engine-v1.md) — **următorul plan de implementare**; pornește cu Batch R0 și păstrează reasoning-ul și randarea ca gate-uri separate
- [`docs/refs/reasoning-baseline-2026-08-21.json`](../../../refs/reasoning-baseline-2026-08-21.json) — baseline-ul de comparație
- [`handoff-clipper-session-4.md`](../../archive/clipper/handoff-clipper-session-4.md) — istoric detaliat
