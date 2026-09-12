# Handover — AI Stream Clipper

## Actualizare 12 septembrie 2026 — B1 verificat, detector nou încă neactivat

Codex a verificat lotul B1 implementat cu Claude Code (adaptor YuNet și comparație
pe cadre), inclusiv corecțiile raportării și probele independente prin main().
Suita completă: 2.053 teste trecute, aceleași două eșecuri TikTok 404 cunoscute
(excluderea cu separator `/` nu a selectat nodurile Windows, deci au rulat).
Predicțiile pe 40 de cadre coincid cu apelurile OpenCV salvate independent;
raportul păstrează un cadru incert, exit 2. Cele 390 de fișiere din exports sunt
neschimbate față de inventarul de la începutul acestui lot.
Detectorul din aplicație rămâne cel din lotul A.
Probe vizuale înghețate: 40 de cadre inițiale și 20 de confirmare la alte momente.
[Rezultatele și limitele lotului B](../../../refs/clipper-face-detector-b-2026-09-12.md).
Urmează B2: profilul explicit 2x/.75, pe aceeași cale de eșantionare, apoi probă
Speed separată. PRP-ul B2 permite probe, nu activarea implicită în aplicație.

Ordinea cerută de utilizator: se termină întâi partea de detecție începută,
folosind Claude Code; apoi se analizează modelul de montaj pentru live-ul xQc:
https://youtube.com/shorts/T1jsllUvrHA?is=Xfai9xR99IRBuIN-
Referința nu a fost încă analizată. La epuizarea cotei Claude se salvează
progresul și se așteaptă reluarea explicită a utilizatorului; fără reluare programată.

## Actualizare 12 septembrie 2026 — lot A de observații închis

Codex a terminat corecțiile începute de Claude: stări distincte pentru citire și
detecție, index/PTS verificabile în fereastra analizată, refuzul adreselor
indisponibile și al observațiilor contradictorii în plasarea captions.
Claude Code a făcut review static fără alte constatări; Codex a executat
2.005 teste trecute, probele de regresie și exportul Speed, identic ca octeți
cu proba acceptată anterior. Cele 301 fișiere existente din exports sunt intacte.
Detectorul Haar, pragurile și ratările lui nu s-au schimbat. Următorul lot este
îmbunătățirea detecției/urmăririi pe probe etichetate, nu reluarea lotului A.
[Contract, rezultate și limite](../../../refs/clipper-face-observations-2026-09-12.md).

## Actualizare 11 septembrie 2026 — captions în afara fețelor observate

Poziția automată poate evita fețele locale proiectate prin cropul rendererului;
rămâne constantă pe clip, respectă editarea manuală și nu se aplică stratului
suprimat. Proba Speed mută textul de pe gură pe piept în cadrele inspectate.
Este o euristică cu acoperire incompletă, nu un nou pass al porții de captions.
1.970 teste trec; corpusul existent este neschimbat.
[Probe, condiții și limite](../../../refs/clipper-caption-faces-2026-09-11.md).

## Actualizare 10 septembrie 2026 — subtitrări, timp și editor

Subtitrarea se arde după eliminarea pauzelor; și cuvintele evidențiate își mută
timestampurile. Editorul folosește planul și comenzile exportului, inclusiv
politica de captions, crop/fit și o grilă comună de cadre. Presetul salvat
schimbă stilul efectiv; o editare invalidează referința la exportul vechi fără
să șteargă fișierul. Probe sintetice și un MP4 Speed separat de corpus:
[raportul și limitele lotului](../../../refs/clipper-editor-captions-2026-09-10.md).
Subtitrarea nativă și calitatea editorială generală rămân deschise.

## Actualizare 9 septembrie 2026 — cropul care revenea pe perdea

Plannerul nu mai tratează automat mediana feței pe clip ca pe o cameră fixă.
Un conflict cu poziția locală poate lărgi încadrarea pentru a păstra ambele
propuneri, cu motiv și limite explicite. Ancora sursei și contraexemplul
Minecraft rămân protejate. Probe MP4 înainte/după, limite și verificări:
[raportul încadrării](../../../refs/clipper-face-framing-2026-09-09.md).
Lotul repară un defect concret; subtitrările native și încadrarea generală
nu sunt închise. Directorul `after/` din probe conține o încercare respinsă
pe Minecraft; candidatul final este în `final/`.

## Actualizare 9 septembrie 2026 — export comun implementat

Calea normală, re-planificarea, replay-ul planurilor stocate și probele camera/letterbox
folosesc acum `workers/clipper_render_output.py` pentru encode și sidecar v2.
Detalii, probe reale și limite: [raportul lotului](../../../refs/clipper-shared-export-2026-09-09.md).
Acest lot nu activează `story_v2` sau `content_aware` și nu certifică încadrarea ori
lizibilitatea. Cele 301 fișiere existente din exports au fost reverificate prin hash:
0 modificate, 0 adăugate. Probele noi sunt în `data/codex-analysis-20260909/shared-export/`.

Secțiunile istorice de mai jos conțin și constatări ulterior retrase în analiza
adnotărilor; nu reprezintă toate aceeași rulare și nu înlocuiesc artefactele curente.

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
- **Un trim este reconstruit pe ceasul livrat din R8.** Un shot eliminat complet dispare, unul tăiat
  prin mijloc devine două bucăți, iar saltul peste timpul eliminat intră în `trim_jumps`, nu în
  tăieturile planificate sau echivalente. Ceasul, lead-in-ul și tail-ul rămân exacte.
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

## Batch R2 — ÎNCHIS, 29 august 2026

Fiecare clip primește acum o gramatică potrivită tipului său — **rezolvată și înregistrată, aplicată
pe niciun clip**. Cele zece content types existente se mapează pe șase profile; nu există al doilea
clasificator.

Regula pe care se sprijină totul: **o clasificare slabă cumpără un montaj mai sigur, niciodată unul
mai agresiv.** Iar „mică" și „nemăsurată" sunt răspunsuri diferite:

| ce știm despre tip | profil | motiv |
|---|---|---|
| tip cunoscut, încredere ≥ 0,5 | al tipului | `type` |
| încredere sub prag | `conservative` | `low_confidence` — numărul se păstrează |
| încredere absentă | `conservative` | `missing_confidence` — nu se inventează din scorul sursei |
| încredere în afara lui 0..1 | `conservative` | `invalid_confidence` — un clasificator stricat nu e unul foarte sigur |
| tip necunoscut | `conservative` | `unknown_type` |
| tipul setat de om | al tipului | `override` — proveniența ține loc de număr |

Ce trebuie știut înainte să te bazezi pe el:

- **`content_aware` nu livrează nimic și nu poate.** `delivers_profile` verifică singur
  disponibilitatea, deci întoarce fals pentru toate modurile azi și devine adevărat de la sine în
  ziua în care gate-ul final adaugă modul în `SELECTABLE`. Nu există al doilea comutator de ținut minte.
- **Benzile de ritm sunt guardrail-uri ALESE, nu măsurate.** Codul o spune, testele refuză să le
  asserteze, iar UI-ul o scrie pe ecran. Nu le cita ca rezultate înainte de gate-ul uman din §7.
- **Shadow-ul e inert, demonstrat end-to-end:** `_decide_render` în ambele moduri dă același plan,
  crop, captions, trim, fps, watermark și fingerprint. Singura diferență e câmpul diagnostic.
- Profilul e în sidecar și în panoul de reasoning, rezolvat **în backend**. Frontendul doar îl
  afișează — o a doua mapare în TypeScript ar devia de la prima.
- Profilul NU e în fingerprint. În shadow nu schimbă imaginea; batch-ul care îl aplică îl adaugă.

Un bug găsit de testul de round-trip peste HTTP, fără legătură cu R2 dar reparat aici:
`patch_settings` normaliza dicționarul **parțial** primit, deci un PATCH pe orice altă cheie reseta
tăcut `edit_mode` și `reasoning_mode` la valorile rig-ului. Trecuse neobservat fiindcă browserul
trimite tot obiectul.

## Batch R3a — INSTRUMENTAT, gate vizual PENDING, 29 august 2026

**Nu este închis.** Codul e livrat și verificat automat; verdictul vizual nu a fost dat de nimeni,
iar formularea corectă până atunci este exact asta: instrumentare shadow implementată, gate vizual
pending.

Ce face: `anchored_track` elimină din track detectările care nu stau pe ancora fixă găsită de
`stable_track` — niciun eșantion nu dispare, doar cutiile lui, fiindcă timeline-ul e indexat
pozițional. Histerezisul a devenit **retrospectiv**: o absență confirmată se marchează de unde a
început, nu trei secunde mai târziu. Rezultatul intră în sidecar ca `creator_view`, **înregistrat și
niciodată aplicat** — planul livrat rămâne ce a înghețat R2.

```bash
python scripts/measure_creator_presence.py pilotf81b pilotee0e pilot6b38 pilot2c8a
```

**Cum se citește măsurătoarea, fiindcă e ușor de citit greșit:**

- `stable_track` găsește o ancoră pe **unul** din patru piloturi. Pe celelalte trei nu se filtrează
  nimic și track-ul e identic — cazul care deja funcționa.
- Pe Moist, 464 din 1.285 de eșantioane cu față sunt **compatibile cu ancora**. Asta NU înseamnă „464
  sunt creatorul": nimeni nu a etichetat cutiile, iar geometria e tot ce știe codul. Compatibilitatea
  e măsurată; identitatea e dedusă.
- **Fără ancoră, compatibilitatea e `null`, nu 100%.** Nu s-a comparat nimic cu nimic.
- `faces.json` are pe piloturi un pas median de **6,7s**, la care `ENTER_S` se rotunjește la un
  eșantion. Orice cifră despre timeline-ul de prezență calculată acolo măsoară alt algoritm decât cel
  care rulează în producție. Efectul asupra prezenței se poate măsura doar pe track-ul dens.
- Toleranța efectivă e `max(lățimea ancorei, 40px)`. Pe pilotul măsurat ancora are 25px, deci decide
  podeaua, nu lățimea feței.

**Ce trebuie să verifice un om înainte ca R3a să fie închis:** Moist — creatorul, nu fața din browser;
Jensen — diagramă lizibilă, vorbitor bine încadrat; vlog — obiectele în `fit`, persoana reală în
`crop`; go ghost — fără comutări false.

## Batch R3b — INSTRUMENTAT, gate vizual PENDING, 29 august 2026

**Nu este închis**, ca și R3a: propunerea există și e verificată automat, dar nimic nu a fost aplicat
și nimeni nu s-a uitat.

Fiecare stretch al unui clip primește un regim — `speaker`, `conversation`, `action`,
`visual_evidence`, `reaction`, `safe` — decis **pe eșantion**, nu pe shot, iar segmentele adiacente cu
aceeași cheie vizuală se unesc. Rezultatul intră în sidecar ca `regime_view`.

**De ce pe eșantion.** Un verdict ponderat peste un shot este votul majoritar pe care planul îl
interzice, și producea `reaction` din doi oameni care nu erau niciodată pe ecran împreună.
Coprezența e un fapt la nivel de eșantion.

**Ce refuză să ghicească, și de ce contează:**

- **Seria de mișcare e pe alt ceas.** Cadrele sunt întregi, deci un proxy de 10 FPS eșantionează la
  **0,2s**, nu 0,25. În plus indexul 0 e o santinelă — nu există cadru anterior — iar valoarea `j`
  descrie intervalul DINAINTE. Se resamplează pe bins-urile canonice înainte de orice.
- **Acoperire și variabilitate sunt axe independente.** O serie completă dar plată e o stare reală, un
  ecran static măsurat cap la cap; un singur cuvânt pentru ambele ascundea pe care dintre ele.
- **Lipsa timestamp-urilor de cuvinte nu e tăcere**, un track mai scurt decât clipul lasă
  `target_unknown`, iar evidența se mediază **doar peste eșantioanele măsurate** și e `None` unde nu
  s-a măsurat nimic — cu `evidence_coverage` alături, ca o medie peste două eșantioane să nu fie
  citită ca una peste douăzeci.
- **Nu orice graniță de regim e o tăietură.** Se unesc segmentele cu aceeași cheie vizuală, altfel
  s-ar reintroduce exact cele 116 tăieturi invizibile scoase de R1. Se raportează separat
  `regime_boundaries` și `treatment_boundaries`.

**Nimic nu spune „creator".** `stable_track` găsește un cluster geometric stabil, nu o persoană.
Cheile sunt `crop_anchor`, `crop_subject`, `fit_full`; sidecar-ul spune `target`, iar `target_basis`
spune dacă a fost `stable_anchor` sau `unanchored_face`. `crop_creator` poate exista abia după o
verificare reală de identitate sau după gate-ul vizual uman.

## Batch R4 — INSTRUMENTAT, gate PENDING, 29-30 august 2026

**Review Codex, șase runde, verdict final:** „Nu mai văd blocante. Consider R4 închis corect ca
**instrumentare shadow**, nu ca motor activ." Toate cele opt constatări au fost reparate; niciuna nu
era în regula batch-ului, toate erau în plasare — ce moment compari, la ce rezoluție, față de care
margine. Regula „motiv plus loc" a rezistat de la început.

**Nu este închis**, ca R3a și R3b — dar dintr-un motiv în plus. Gramatica există complet și e
verificată automat; nimic nu a fost aplicat, `legacy_dynamic` rămâne înghețat, iar propunerea se
scrie în sidecar ca `rhythm_view`. Gate-ul lui §6 („auditul uman nu mai descrie montajul drept
agitat, numărul de tăieturi scade fără cadre moarte lungi") nu poate fi nici măcar ÎNCERCAT până
când `content_aware` nu devine livrabil: cere piloturile re-randate cu gramatica aplicată. Până
atunci se poate compara doar propunerea cu cadența livrată, în sidecar, clip cu clip.

**Regula, și este tot batch-ul: o tăietură are nevoie de un MOTIV și de un LOC.** Motivul e o
schimbare declarată în ce trebuie văzut — `treatment_change` de la R3b, `source_scene_cut` când
sursa a tăiat ea însăși, `action_beat` doar într-un interval pe care R3b l-a măsurat ca acțiune.
Locul e o graniță unde tăietura nu cade în mijlocul unui cuvânt, luată din `_boundaries`, nu
reimplementată. **O pauză fără nimic în spate este exact tăietura pe care batch-ul o elimină.**

Măsurat prin funcția livrată, nu descris: pe același talking-head liniștit cu șase pauze naturale,
`_cut_times` taie de opt ori — ultimele două (16,8s și 18,6s) fără nicio graniță, pur pe ceas — iar
R4 taie de zero ori.

**Asimetria contează.** `treatment_change` e obligatoriu și taie chiar și fără graniță
(`placement: unsnapped`) și chiar peste `min_shot_s`, cu violarea înregistrată în
`min_shot_violations`. A ține un crop pe o ancoră goală ca să protejezi o lungime minimă schimbă un
defect vizibil pe o metrică invizibilă — și lista de violări e felul în care un timeline de prezență
care pâlpâie devine ceva ce i se poate arăta unui om.

**Patru corecții din review-ul Codex, toate găsite citind codul:** două schimbări obligatorii nu pot
fi satisfăcute de aceeași tăietură (la 5,0s și 5,2s ambele se agățau de pauza de la 5,1s și
tratamentul dintre ele dispărea complet — fără tăietură, fără `held`, fără violare); coada runt nu e
o violare ci un refuz (o tăietură la 9,9s dintr-un clip de 10s e un flash de 100ms și iese în
`required_conflicts`); acoperirea parțială nu e o partiționare (secundele nemăsurate intrau automat
în `quiet` și un minut nemăsurat ieșea `below`); `indeterminate` ascundea un `above` demonstrabil —
trei tăieturi în patru secunde sunt 45/min, iar durata scurtă nu face asta ambiguu. Plus atribuirea:
o tăietură la granița `action → speaker` e o **tranziție**, creditată niciunei benzi.

**Benzile din §4 se compară, nu se impun.** Nimic nu adaugă o tăietură ca să atingă o bandă, nimic nu
scoate una ca să rămână în ea. `action` e singurul profil judecat pe două benzi — secundele numite
`action` și restul; fără măsurătoarea de mișcare ambele partiții ies `unavailable`, niciodată
contopite într-un număr care ar judeca liniștea cu banda agitată. `indeterminate` e un verdict real:
sub `60 / lo` secunde propria podea a benzii nu așteaptă încă nicio tăietură.

**Ce lipsește, spus pe față.** `UNMEASURED` numește per profil ce cere §4 și nimic nu măsoară:
`talking_head` cere reframe „la o idee sau emoție clară", iar singurul lucru care există e un regex
de cuvinte-cheie. O listă cu „bro" și „lol" nu e o emoție. De aceea `talking_head` și `conversation`
ies `below` banda lor — golul e măsurătoarea care lipsește, nu un ritm care are nevoie de umplutură.

**`dynamic_cuts.py` și `dynamic_edit.py` nu au fost atinse.** Planul le lista; a le modifica ar
schimba planul livrat, pe care R2 l-a înghețat și de care depind gate-urile vizuale deschise.
`delivers_profile()` face comutarea singur în ziua în care `content_aware` intră în `SELECTABLE`.

**Două corecții suplimentare, tot din review, ambele „conflict fabricat de plasare":** un snap nu
mai poate trece dincolo de următoarea schimbare obligatorie (mutarea unei tăieturi peste ea păstrează
ordinea numerică și tot pierde tratamentul dintre cele două), și o schimbare obligatorie nu mai poate
face snap în zona cozii (una la 9,2s dintr-un clip de 10s se muta la 9,5s, era scoasă de walk-back și
raportată ca imposibilă, deși propriul ei moment lasă un shot legal de 0,8s). Plus: o tăietură scoasă
la coadă păstrează acum TOATE motivele pe care le răspundea, nu doar primul.

**Simetria, găsită punând întrebarea inversă:** un snap nu poate trece de o schimbare obligatorie
**în niciun sens**. Înainte pierde tratamentul dintre cele două; înapoi, tăietura care introduce
schimbarea asta se întâmplă înainte ca precedenta să fi început. Ordinea tăieturilor nu prinde
niciunul — o tăietură dinaintea schimbării precedente dar de după TĂIETURA precedentă e în ordine.
Marginea cozii e însă inclusivă: `duration - min_shot_s` e ultimul loc legal, iar un `<` strict
arunca exact singura graniță pe care regula o permite.

**Ultimele două, de precizie:** marginile testau timpul BRUT al graniței în timp ce tăietura se plasa
la cel rotunjit, deci o pauză la 5,0499996 trecea de un plafon de 5,05 și ateriza exact pe el — pe
chiar schimbarea pe care nu avea voie s-o traverseze. Și egalitatea de moment compara `existing["t"]`,
adică unde a ajuns tăietura după snap, nu momentul cererii; se compară acum cu `requests`. Două
motive împart o tăietură doar dacă au fost CERUTE în același moment.

**Split nou:** `services/clipper/dynamic_rhythm_vocab.py` (listele închise),
`services/clipper/dynamic_rhythm_pace.py` (benzile), `tests/test_clipper_rhythm_place.py` (plasarea,
unde e fiecare constatare din review) și `workers/clipper_shadow_views.py` — tot ce o randare ÎNREGISTREAZĂ despre montajul pe
care nu l-a făcut (R3a + R3b + R4). `clipper_render_plan.py` trecuse de 500 de linii.

## Batch R5 — INSTRUMENTAT, gate NEMĂSURAT, 30 august 2026

Fiecare fereastră aleasă primește un **verdict de completitudine** în `candidates.json`, ca
`boundary_view`. Nimic nu se aplică: `eligible` nu decide nimic, fiindcă planul însuși condiționează
asta de trecerea corpusului.

**De ce era nevoie de un verdict, nu de încă o mutare.** `refine_boundaries` mută deja ambele margini
și `extract_features` le scorează deja. Un scor e un număr după care sortezi — nu poate spune cu voce
tare „clipul ăsta se oprește în mijlocul unui cuvânt". De aceea `candidate_boundaries.py` și
`story_evidence.py` nu au fost atinse, deși planul le lista.

**Gaura închisă:** `ends_on_sentence = 1.0 if text.endswith(".!?…")` e 0.0 pentru fiecare clip tăiat
vreodată dintr-un transcript fără punctuație — iar 0.0 acolo nu înseamnă „se termină la mijlocul
propoziției", înseamnă că nimeni nu a putut ști. `transcriber._clean_text` scoate punctuația implicit.
Acum verificările de propoziție ies `unavailable` și `eligible` e `None`; o tăietură în mijlocul unui
cuvânt rămâne măsurabilă oricum, fiindcă nu depinde de punctuație sau de limbă.

**Nouă defecte, patru blocante:** `start_inside_word`, `end_inside_word`, `end_mid_sentence`,
`orphan_tail`. `start_mid_sentence` nu blochează — un hook deschide legitim la mijloc, iar gate-ul
cere un om. `clipped_release` nu blochează — se repară prin padding, nu prin refuzarea momentului. E
pragul `TAIL_TIGHT_S` importat din `edit_quality`, ca partea de selecție și cea de randare să numere
ACELAȘI defect (22 din 58 la baseline).

**O singură reparație, bounded:** extinderea finalului la următoarea graniță de propoziție, mărginită
de durata maximă, de mediu și de prima fereastră care începe DUPĂ aceasta. Ce rămâne după reparație se
măsoară pe fereastra reparată, nu se presupune.

**Gate-ul E măsurabil, prin `--recompute`.** Toate cele 22 de proiecte de pe disc au fost scorate
înainte de R5, deci nu poartă verdict; `scripts/audit_clipper_boundaries.py` le numește
`predates_r5_rescore_needed` și tipărește numitorul înaintea numărătorii („0 din 144 candidați poartă
un verdict"). Cu `--recompute` încarcă transcriptul din DB și rulează ACEEAȘI funcție canonică
(`boundary_completion.attach`) peste ferestrele stocate, fără re-score și fără să atingă vreun scor
sau board. Măsoară REGULA, nu pipeline-ul — un verde acolo lasă integrarea end-to-end nedemonstrată.

**PRIMA MĂSURĂTOARE, pe tot corpusul (6.762 de candidați, 13 proiecte, toți cu verdict): 261 de
ferestre se termină în interiorul unui cuvânt și ZERO încep așa.** Gate-ul cere zero. Asimetria
261/0 e chiar demonstrația cauzei — `refine_boundaries` aplică `_snap` pe început și niciodată pe
finalul final. Verificat manual pe patru cazuri:
`_reaction_end` întoarce `min(w1, limit)`, iar când reacția lovește plafonul `REACTION_MAX_S` la
mijlocul unui cuvânt tăietura cade acolo; `_snap` nu se aplică niciodată pe finalul final, iar `_fit`
snapează doar la depășirea maximului. Toate patru poartă `reaction_kept`. **Reparat în R5a** — vezi mai jos.

**`--recompute` rulează regula de AZI peste ferestre produse de codul de atunci**, deci o cifră
agregată amestecă generații. Verificat: artefactul lui `slice4h00test` e din 14 august, iar
`_keep_release` a intrat a doua zi în `4a136de` — de acolo vin cele 880 din 920 de `clipped_release`
ale lui: pad-ul nu a rulat niciodată pentru el. Pe ferestrele lui vechi, implementarea de azi ar
adăuga padding la 748 din 880, deci proiectul demonstrează lipsa de proveniență a artefactelor, nu
că funcția curentă ar fi ocolită. Cele patru piloturi sunt din 22 august și **nu au
scuza asta**, deci trunchierea de acolo e defect viu, nu artefact vechi.

*(Am raportat întâi 171. Însumasem liniile unui raport trecut printr-un `tail`, adică o vedere
trunchiată — 261 e cifra din JSON-ul complet. Merită păstrat ca avertisment: agregarea unei vederi
parțiale arată exact ca o măsurătoare.)*

Restul, din JSON-ul complet: `end_mid_sentence` 3.681, `clipped_release` 1.855,
`start_mid_sentence` 573, `start_on_continuation` 432, `end_inside_word` 261, `orphan_tail` 176,
`required_context_outside` 76, `dead_tail` 55. Coada: median 0,40s / p90 0,40s pe 6.742 de ferestre.

## Batch R5a — finalul aterizează în afara unui cuvânt, 30 august 2026

**O singură modificare:** `_fit` snapează acum și finalul, ultimul lucru pe care îl face, mărginit de
maxim și de mediu, cu pull-back refuzat când ar coborî sub minim. Docstring-ul promitea „staying off
words" de la început; era adevărat doar pentru început, iar asimetria 261/0 e chiar demonstrația.

**Măsurat înainte/după cu `scripts/measure_boundary_snap.py`: 261 → 0**, zero refuzate pentru minim,
maxim sau mediu, 260 împinse înainte și 1 trasă înapoi. Deplasare mediană 0,10s, p90 0,66s — dar
**120 din 261 depășesc 0,15s**, deci presupunerea inițială („mutare deterministă sub 0,15s") era
greșită. Trei cazuri depășesc 3s, pe transcripte care conțin un token de 5,72s, unul de 5,14s și unul de
3,54s. **Nu am pus gardă pe durata cuvântului, și motivul e măsurat, nu de principiu:** p99 al
duratelor de token marchează 19 din cele 261 de mutări, p99.9 marchează exact cele trei extreme —
deci alegerea pragului s-ar face după ce vezi ce răspuns dă. Nici audio-ul nu tranșează: toate trei
extremele cad în intervale clasificate drept vorbire, cu RMS-ul activ, deci „adaugă secunde de
tăcere" nu e ceva ce a arătat cineva. Nici probabilitatea Whisper nu separă — 0,292 pentru „love" de
5,14s, dar 0,886 pentru un token de 3,54s și 0,997 pentru unul de 1,56s. O regulă statistică poate
spune că timestamp-ul e ciudat; nu poate spune unde se termină cuvântul.

`scripts/measure_boundary_snap.py` numește acum cele mai mari mutări per proiect, cu tokenul, durata
lui, percentila în distribuția transcriptului însuși și probabilitatea Whisper — ca un om să se poată
uita la ele, nu ca un prag să decidă în locul lui.

**Ce NU e măsurat, și e următorul pas:** efectul celor 261 de mutări asupra scorurilor de graniță,
dedupe-ului, shortlist-ului, judge-ului și board-ului. Cere un re-score end-to-end pe o **CLONĂ**, nu
pe proiectele-baseline. **Și nu doar pe cele patru piloturi:** cele trei mutări extreme sunt în
`39c89ae2e16e` și `43a509687a33`, deci clonele acelor două surse trebuie incluse, altfel gate-ul nu
testează chiar riscul tocmai descoperit. Baseline-ul R0 rămâne dovada despre cele 58 de fișiere vechi; rezultatele noi
se ștampilează cu versiunea nouă de boundary și nu se amestecă în aceeași comparație. S8 vine după.

## Batch R6 — detectorul de subtitrări arse, 30 august 2026

**Prima parte din R6 e livrată:** `source_captions.py` răspunde `present | absent | unknown` la
întrebarea dacă sursa are deja text ars. **4 din 4 împotriva etichetelor umane** —
`pilotf81b` (go ghost) `present`, celelalte trei `absent`. Nimic nu e aplicat: detectorul nu stinge
al doilea strat de captions.

**Două abordări au eșuat înainte și sunt scrise integral** în
[`../../../clipper-caption-detection.md`](../../../clipper-caption-detection.md) — euristici de
luminozitate pe benzi, iar a doua a pus un negativ peste pozitiv. Documentul există exact ca tabelul
celor patru abordări eșuate de facecam: ca să nu fie rerulate.

**Ce funcționează:** greutățile CRAFT ale lui `easyocr` sunt deja în cache local, deci detecția merge
offline, 5s pe sursă pe GPU. Dependența e OPȚIONALĂ — fără ea totul e `unknown` și analiza nu costă
nimic.

**Care încotro merg răspunsurile, fiindcă prima versiune le avea invers:** `present` = sursa are deja
captions → se **dezactivează** stratul ClipForge; `absent` → se **păstrează**; `unknown` → nu se
schimbă nimic. Deci `present` e răspunsul scump — unul greșit livrează un clip fără captions deloc —
și de aceea se obține din dovezile unei singure benzi, în timp ce `absent` cere toate benzile clar
negative.

**Discriminatorul e lățimea, și e contraintuitiv:** watermark-ul lui `pilot6b38` e în 13 din 14 cadre,
mai persistent decât adevăratul pozitiv, dar are 0,079 din lățimea cadrului față de 0,45. Persistența
singură ar fi clasificat greșit. Fiecare negativ pică pe altă axă.

**Trei corecții din review:** semantica inversată de mai sus; judecarea doar a benzii cu cele mai
multe cadre lăsa un watermark să mascheze pista reală de subtitrare; și un detector care aruncă pe
fiecare cadru ieșea `absent` — testul meu verifica benzile goale și nu verdictul. Numitorul e acum
ce s-a **analizat**, nu ce s-a eșantionat.

**Ce NU e demonstrat:** pragurile au fost alese cu răspunsul la vedere, pe patru surse. `calibrated:
false` călătorește cu fiecare verdict.

**A doua parte, livrată 30 august 2026:** `caption_placement.py` + `caption_placement_vocab.py`
(ce acoperă caption-ul ars, per shot, în pixeli de output) și `caption_choice.py` (de ce stă acolo:
poziția propusă, alternativele respinse, motivul). Nu mișcă niciun cadru.

Trei axe ținute separat — `conflicts` măsurat, `unavailable` nefurnizat, `refused` furnizat greșit.
`share` e reuniunea peste semnale și cutii, nu maximul, și e un plafon inferior când nu s-a măsurat
tot (`share_complete`); cu nimic măsurat e `None`, nu 0,0. Lista `COMPOSITIONS` e APLICATĂ: o
compoziție necunoscută e refuzată, nu tratată drept `crop`.

**Constatare vie, neremediată:** un `safe_zone` cu NaN trece de `captions._norm_rect` și se suprapune
peste caseta de caption la FIECARE poziție din scanare, ștergând căutarea de bandă și mutând
caption-ul livrat (măsurat 0,3092 → 0,4642). `caption_choice` o numără; nu o repară, fiindcă reparația
mută captions livrate.

**Browser chrome:** prima măsurătoare (easyocr, 20 de proxy-uri × 8 cadre uniforme) a dat 0
potriviri din 453 de tokeni citiți, și am concluzionat greșit că nu există pozitiv în corpus.
Review-ul uman v2 listează „browser UI" printre defectele rendererului v3 — pozitivul există, opt
instantanee dintr-o sursă de ore nu l-au atins. Se remăsoară pe EXPORTURILE randate, un cadru pe
secundă. Ce rămâne valabil: recognizer-ul citește prost conținutul care contează (`pilotf81b`,
singura sursă cu captions arse cunoscute, nu a dat niciun token peste confidence 0,5), deci recall-ul
e mic și `not_detected` nu poate însemna „curat".

**Rulate pe corpusul real** (`scripts/audit_caption_placement.py`, 101 sidecare): **27 din 27** de
clipuri cu shot `fit` și geometrie cunoscută au caption-ul pe banda de letterbox, deci contrastul e
starea fiecărui export letterboxat, nu un caz-limită. Și 8 din 99 de poziții nu pot fi produse de
regula de azi — exporturi anterioare scanării de bandă; unul singur stă peste un keep-out de față.

**Contrastul e aritmetic** (`caption_contrast.py`): un glif cu contur se separă de orice fundal prin
una dintre cele două culori ale lui, deci există o podea, cu formă închisă: rădăcina pătrată a
contrastului dintre cele două culori proprii ale glifului, `sqrt(21) = 4,58` pentru alb în negru. Un
sweep pe 256 de griuri o supraestima cu 0,025, fiindcă un pixel colorat are luminanța între nivelele
de gri. **Descoperirea e în paletă, nu pe letterbox:** toate
umpluturile trec, dar highlight-ul lui `Neon Pop` (2,33) și al lui `Viral Gradient` (2,72) nu pot
GARANTA 3,0:1. Citirea precisă: există o luminanță de fundal la care separarea coboară acolo, nu că
textul nu atinge niciodată 3:1. Ambele sunt presete livrabile azi, iar reparația schimbă o culoare
livrată — decizie pentru om, nu pentru un batch în umbră.

**`evidence_map.py` mapează dovezile în cadrul de output**, prin lanțul rendererului: proxy → sursă
(scări separate), `+ canvas_offset` fiindcă padding-ul precede cropul, fereastra de crop, apoi o
scalare. Refuză un shot al cărui crop se mișcă, unul care nu poate fi citit, dimensiuni proxy lipsă și
o cutie malformată; o cutie care ratează cropul e `off_frame`, nici refuz nici zero.

**Primele cifre pentru semnalul de față:** 2126 de shot-uri, **964 fără niciun eșantion în fereastra
lor** (detectorul eșantionează la ~2s, shot-urile au 1–4s), 1162 cu fețe mapate, 459 de cutii care
ratează cropul, și **26 din cele 99 de clipuri plasate au caption-ul peste o față detectată**. Cifra e
un PLAFON INFERIOR: cele 964 de shot-uri neeșantionate plus semnalele de UI și text-sursă, care nu au
detecție per shot, fac ca **zero clipuri să aibă cazul cel mai rău stabilit**.

**`source_chrome.py` detectează chrome-ul de player în exporturile randate.** 14 din 14 exporturi
Moist îl au (5–32 de cadre cu hit fiecare), față de UN singur hit în 27 de exporturi din alte zece
proiecte — deci pragul de 2 cadre separă complet, cu marjă de 5 la 1. Ipoteza cu URL-uri a picat:
39 de potriviri pe pozitive, toate la confidence 0,00–0,01, zero URL-uri reale. `not_detected` nu
înseamnă curat, și `is_a_warning()` o spune în cod.

**DEFECT LIVRAT, GĂSIT DE OM ȘI REPARAT (31 august 2026):** pe shot-urile `fit` caption-ul nu era
desenat deloc. `pad=...:color=black@0` face barele TRANSPARENTE ca să se vadă blur-ul, iar arderea
subtitrărilor peste cadrul acela scria textul în planurile de culoare și lăsa alfa pe zero — deci
`overlay` îl compunea afară. Pe `crop` cadrul e opac și caption-ul supraviețuia; pe `fit` caption-ul
stă în bară prin construcție și dispărea. **331 de secunde din 27 dintre cele 88 de clipuri stocate,
7,7% din corpus, un clip mut pe 88% din lungime.** `caption_placement` raportase acele 27 ca „caption-ul
cade pe banda de letterbox" — adevărat despre geometrie, și nu întreba niciodată dacă textul e desenat.
Reparat mutând `subtitles` după `overlay`; dovedit re-randând clipul și extrăgând același cadru.

**Ce a mai rămas din R6:** detecția per-shot pentru UI și pentru textul sursei — lipsește un DETECTOR
nu un mapper, fiindcă `regions.hud` are 8 dreptunghiuri în tot corpusul și toate sunt cutii fixe de
colț — și cablarea în sidecar. `panels_to_keep_out` nu e mapper generic: sare peste shot-urile de
față.

## DEFECT LIVRAT, GĂSIT DE OM ȘI REPARAT — 31 august 2026

**Pe shot-urile `fit` caption-ul nu era desenat deloc.** `pad=...:color=black@0` face barele de
letterbox TRANSPARENTE ca să se vadă copia blurată; arderea subtitrărilor peste cadrul acela scria
textul în planurile de culoare și lăsa alfa pe zero, deci `[bg][fg]overlay` îl compunea afară. Pe
`crop` cadrul e opac și caption-ul supraviețuia; pe `fit` caption-ul stă în bară prin construcție.

**331 de secunde din 27 dintre cele 88 de clipuri stocate, 7,7% din corpus.** `pilotee0e/a9f653576eb7`
era mut pe 88% din lungime, `pilot2c8a/ad1b8004ece9` pe 71%.

Reparat mutând `subtitles` după `overlay` în `dynamic_render`. Dovedit în trei pași: cadru din
exportul stocat fără text deși `.ass` are un eveniment activ; reproducere izolată cu două etichete,
una peste sursă și una peste bară, din care apărea doar prima; re-randare și același cadru cu textul
acolo.

**Toate cele 58 de exporturi ale piloturilor sunt re-randate** (`scripts/rerender_pilots.py`, 54 de
minute, zero refuzuri). Originalele sunt în `<proiect>/exports_pre_caption_fix/`, iar scriptul refuză
să pornească a doua oară peste ele. Verificat automat: banda de caption s-a schimbat pe 19 din 27; pe
celelalte 8 caption-ul era deja în cadrul sursă.

**Ce a arătat instrumentul, și de ce nu l-a prins:** `caption_placement.ON_LETTERBOX` raporta acele
27 de clipuri drept „caption-ul cade pe banda de letterbox". Adevărat despre GEOMETRIE, și nu întreba
niciodată dacă textul e desenat. O măsurătoare despre unde cade ceva nu e o măsurătoare că acel ceva
există.

## DOUĂ CIFRE CORECTATE, ambele găsite verificând reparația

**Poziția livrată e în `.ass`, nu în `caption_plan.y_pct`.** Sidecar-ul stochează PRESET-ul; `.ass`
poartă ce a decis `resolve_position` după keep-out-uri, și ăla se arde. Coincid pe 53 din 99 și
diferă pe 46, cu până la 933 de pixeli. `caption_corpus` citește acum din `.ass`, cu
`caption_y_source` care spune `ass` sau `caption_plan`. Corecția mută „clipuri cu caption peste o
față detectată" de la **26 la 38**.

**„Ar produce regula de azi poziția asta" NU se poate răspunde din sidecar.** `clipper_captions`
re-plasează caption-ul la randare cu keep-out-urile stocate PLUS `panels_to_keep_out(panels, shots)`,
iar `panels` nu e stocat nicăieri. Comparând `y_pct` a ieșit „8 din 99"; comparând poziția arsă cu
aceleași keep-out-uri incomplete a ieșit 54. **Niciunul nu era un fapt despre regulă.** E
`unavailable` acum, cu motivul numit, și nu pică rularea — e o proprietate a formatului stocat.

## Batch R7 — preflight de publicare, LIVRAT 31 august 2026, CORECTAT după review-ul Codex

**Defectul pe care îl repară e chiar în verdict**, și e declarat ca proprietate intenționată în
docstring-ul lui `review.py`: „a review that fails must not lose an export. It returns a verdict of
APPROVE with a warning, which is the same shape as a clean pass." Cinci căi de eșec întorc APPROVE.
**Măsurat: 12 din 101 de clipuri primesc APPROVE cu `sampled: 0`** — 18% din toate aprobările de pe
disc sunt aprobări ale unor clipuri pe care nu s-a uitat nimeni.

**Al patrulea cuvânt: `UNDECIDED`.** Nimic nu a picat și ceva nu a putut fi privit. Regula e că nu e
niciodată `APPROVE`; failure-safe se păstrează prin ce face în aval — nu blochează nimic, ca și azi —
nu prin a-l numi trecere. Cele două alternative sunt scrise în modul ca să nu fie reluate: a plia o
verificare imposibilă într-un `revise` pune două fapte sub un cuvânt, iar a păstra `APPROVE` cu un
steag `established` lângă lasă cuvântul care poartă decizia să mintă — forma lui
`changed_without_moving`.

### Cele opt defecte pe care le-a găsit Codex, și ce a arătat fiecare

Prima versiune trecea toate testele ei și era greșită în șapte locuri din opt. **Trei verificări
erau ORBITE STRUCTURAL** — fiecare citea un câmp care nu exista în ce primea, deci semnalul pe care
existau ca să-l poarte nu ajungea nicăieri, iar rezultatul arăta ca o măsurătoare curată:

1. **Boundary-ul se măsura pe `cand["words"]`** — adică pe lista `inside` a lui `_neighbourhood`,
   construită tocmai prin ARUNCAREA cuvântului care încalecă marginea, care e exact ce caută
   `start_inside_word` și `end_inside_word`. Docstring-ul lui `_straddled` o spune. Verificarea
   căuta în colecția făcută prin scoaterea a ceea ce caută. Pe transcriptul întreg: **12 exporturi
   livrate se termină în interiorul unui cuvânt.** Defectul pe care R5a l-a scos din 261 de
   ferestre, reintrodus la consumator.
2. **Captions căuta `lands_on`/`worst`/`refused`**, numele lui `placement_view` cu un strat mai jos,
   în timp ce `caption_corpus.measure` trimite `on_face`/`worst_share`/`placement_refused`. Deci
   `ON_FACE in placement["lands_on"]` era `in []` pe toate cele 101 clipuri. **Acum ajunge la 38.**
3. **Boundary citea `eligible`**, care e verdictul MOMENTULUI și pe care R5 îl lasă `True` pe baza
   unei reparații pe care `boundary_view` doar A PROPUS-O. Nimeni n-a aplicat-o exportului de pe
   disc. Aceeași familie cu `caption_plan.y_pct`. Citește acum DEFECTELE, și ia și axa `technical`
   pe care R5 a lăsat-o deliberat afară numind R7 drept proprietarul întrebării.
4. **Refuzurile coexistau cu APPROVE.** `refused` era calculat, tipărit și lăsat în afara
   verdictului — șapte verificări trecute plus o a opta verificare refuzată, sau plus a doua
   corecție, ieșeau `APPROVE` cu obiecția într-un câmp alături, iar auditul ieșea 0. Exact forma lui
   `changed_without_moving`. Acum orice refuz blochează `APPROVE` și pică rularea.
5. **Trei verificări treceau pe o singură jumătate a propriului nume.** `{"duration": 30}` fără
   nicio listă de shot-uri trecea `geometry_and_duration` — **12 din 101 de clipuri au forma asta**;
   zero tăieturi echivalente trecea toată „echivalență ȘI ritm" deși §4 însuși spune că ritmul nu se
   măsoară; iar o rețetă goală cu hash-ul ei corect trecea `provenance_complete`, fiindcă
   `render_input` completează cu `None` orice cheie absentă. Un hash valid nu atinge nicăieri
   fișierul livrat.
6. **Auditul crăpa pe un sub-record corupt.** `caption_plan: [1]` → `AttributeError` din mijlocul
   buclei, deci raportul acoperea clipurile dinainte și nu spunea niciodată unde s-a oprit.
7. **Cache-ul de OCR scria `<clip>.chrome.json` în `exports/`**, unde ȘASE locuri din repo citesc
   `*.json` ca sidecar de clip. Singurul fișier lăsat de rularea întreruptă făcea deja
   `audit_clipper_exports` să raporteze `orphan_sidecar` **și să iasă cu 2** — un cache care strica
   poarta de bază pentru care fusese colectat. Mutat în `<proiect>/chrome_cache/`, legat de
   dimensiunea și mtime-ul mp4-ului plus toată configurația detectorului; OCR-ul deja plătit e
   păstrat.
8. **Corecția nu-și impunea propria condiție.** Verificatorul era opțional, deci calea NEVERIFICATĂ
   era cea implicită, iar unul care întorcea `None` o trecea. Și citea NUMELE verificării: `captions`
   răspunde la trei întrebări și doar una e despre poziție, deci `highlight_floor_2.33` cumpăra o
   mutare de caption care nu repară o paletă. Acum citește motivul, față de `publish_checks.ON_A_FACE`,
   verificatorul e obligatoriu și numai `False` îl eliberează.

O corecție a mea, găsită rulând pe corpus: **31 de clipuri n-au `composition` pe niciun shot** —
sunt de dinaintea cheii. Prima versiune a verificării de subiect le numea „în afara listei închise",
adică 31 de înregistrări corupte. Verdictul e `unavailable` în ambele cazuri; motivul e ce citește
omul.

### Cele cinci module

- `publish_preflight.py` — vocabularul și verdictul. `APPROVE` doar când toate cele șapte trec. O
  listă goală e `UNDECIDED`. **Un refuz nu e un input indisponibil** și blochează `APPROVE`.
- `publish_checks.py` — șase verificări. **Ordinea e regula:** un defect MĂSURAT e `fail` orice
  altceva n-ar fi putut fi privit; abia apoi o jumătate nemăsurabilă îl face `unavailable`; un `pass`
  cere fiecare jumătate a propriului nume demonstrată.
- `publish_captions.py` — a șaptea, desprinsă la limita de 500 de linii. Singura care împacă patru
  surse.
- `publish_corpus.py` — unde stă fiecare semnal. Transcriptul e OBLIGATORIU, nu implicit.
- `bounded_correction.py` — singura corecție permisă și cele patru condiții, dintre care două nu erau
  de fapt impuse.

**Rezultatul pe corpus** (`scripts/audit_publish_preflight.py --with-source-captions
--with-chrome`, 101 clipuri, integritate curată, exit 0):

```
APPROVE 0    REVISE 59    REJECT 37    UNDECIDED 5

geometry_and_duration                  pass 58   fail  0   unavailable  43
cut_equivalence_and_profile_rhythm     pass  0   fail 23   unavailable  78
subject_present_when_required          pass  0   fail  0   unavailable 101
usable_frame_in_fit_and_no_chrome      pass  0   fail 16   unavailable  85
captions_not_duplicated_or_unreadable  pass  0   fail 64   unavailable  37
boundary_complete                      pass 44   fail 56   unavailable   1
provenance_complete                    pass  0   fail  0   unavailable 101
```

Defalcarea eșecurilor, fiindcă totalul singur nu spune nimic: captions = **37 duplicat de strat**
(rejectable) + **38 caption peste o față** (revisable, se suprapun pe 11 clipuri); boundary = 16
`clipped_release`, 16 `end_mid_sentence`, 12 `end_inside_word`+`end_mid_sentence`, 11
`clipped_release`+`end_mid_sentence`, 1 cu `orphan_tail`; frame = **16 exporturi cu chrome de
player**, restul `not_detected`, care nu e o trecere.

## Al doilea review Codex — cinci P1 și două P2, toate reparate

Prima versiune a reparațiilor trecea toate testele ei și era greșită în șapte locuri. Cea mai
usturătoare: **reparația mea de geometrie purta chiar defectul pe care îl repara.** `clip_report`
întoarce stringul `"unavailable"` doar când lista de shot-uri nu se poate citi; când SE poate, dă un
contor, iar un contor de necunoscute arată `{"unavailable": 19}`. Comparam tot câmpul cu santinela.
**31 din cele 89 de treceri aveau fiecare compoziție necunoscută** — de aceea cifra e acum 58, nu 89.
Un container nu e scalarul pe care îl conține.

- **De ce lipsește un input e TIPAT acum** — `absent` / `refused` / `unreadable`, decis de producător.
  Auditul sorta corupt de absent căutând fragmente în textul motivului, ceea ce nu e un contract:
  **cinci intrări corupte diferite treceau prin `main()` cu exit 0 și `integrity: true`** fiindcă
  producătorii formulau refuzul altfel. Refuzurile pistei de fețe nu ieșeau deloc din `faces`.
- **Populația e verificată, nu presupusă.** Un sidecar care declară alt `clip_id` sau alt
  `project_id` e refuzat, iar un mp4 fără sidecar e numit. Poarta R0 face deja ambele.
- **O BAZĂ E O METODĂ, NU O MĂSURĂTOARE.** `target_basis: stable_anchor` trecea verificarea de
  subiect pe șaisprezece eșantioane cu `evidence.target = 0.0` și pe zero eșantioane cu
  `target_covered: false`. Acum intersectează fiecare shot `crop` cu dovada per segment, iar un crop
  ținut peste un interval unde ținta e măsurat absentă e o constatare, nu o trecere. Se citește DOAR
  dovada, niciodată regimul propus alături — ăla ar compara un crop livrat cu un plan.
- **Nu mai există `False` pentru `own_layer`.** Un `.ass` lipsă însemna „exportul ăsta n-are strat
  propriu", și nu înseamnă: `.ass`-ul e instrucțiunea, mp4-ul e artefactul, iar ștergerea
  instrucțiunii după ardere nu scoate nimic din video — și absența aia e ce lăsa verificarea de
  duplicat să treacă.
- **Validarea containerelor mutase gaura cu un nivel mai jos:** `defects=[7]` e o listă, deci trecea
  verificarea de tip, nu se potrivea cu nimic din mulțimile închise, și ieșea curat. La fel un nume
  de defect scris greșit. La fel `contrast={}`, care intra în ramura „paletă lizibilă" fără nicio
  latură de obiectat.
- **Cache-ul se cheiază pe toate constantele detectorului**, enumerate din modul de către PRODUCĂTOR.
  Lista ținută de consumator era deja incompletă cu una: `SAMPLES_MIN` e citit de `classify` și nu
  era în cheie. Și identitatea mp4-ului se ia ÎNAINTE și DUPĂ detecție — un minut de OCR e destul ca
  o re-randare să aterizeze la mijloc.

## OCR-ul de chrome — rulat, și de ce n-a mers prima dată

**`server/.venv` avea `torch 2.13.0+cpu`.** `torch.cuda.is_available()` era `False`, deci
`source_chrome._reader()` încerca `gpu=True`, eșua și cădea tăcut pe CPU: **zero verdicte în 38 de
minute**. CUDA-ul care merge e al lui `ctranslate2` (whisper) și nu se transmite la torch.

Reparat cu `torch 2.13.0+cu126` — aceeași versiune, doar backend-ul. **Măsurat: 68s per export,
~115 minute pe corpus.** Estimarea de „~3 ore" era corectă pentru un GPU; venv-ul doar nu avea unul.

Cele 101 de verdicte sunt în `<proiect>/chrome_cache/`, migrate cu
`scripts/migrate_chrome_cache.py` ca să poarte `measured_with`. Migrarea e VERIFICATĂ: trei din cele
patru constante erau deja în fiecare verdict și scriptul refuză orice fișier ale cărui valori diferă
de ale modulului; a patra n-a fost schimbată niciodată de la `dbd1cc0`.

**Cele 37 de respingeri sunt defectul cu care s-a deschis R0**, dar acum fiecare se sprijină pe
AMBELE straturi: sursa are captions arse (`pilotf81b`, `39c89ae2e16e`, `43a509687a33`) **și** exportul
are `.ass`-ul lui. Un verdict despre proxy-ul proiectului nu e o propoziție despre un export anume,
iar direcția inversă — lipsa verdictului de sursă — putea ajunge `PASS` pe o verificare al cărei prim
cuvânt e „nedublat".

**Două contracte, ținute separat** (a doua decizie a lui Codex). Auditul DESCRIPTIV poate ieși verde
peste un corpus aproape integral `UNDECIDED` — că majoritatea celor șapte n-au intrare ESTE
constatarea, iar o poartă permanent roșie ar îngropa-o. POARTA DE PUBLICARE e celălalt contract:
numai `APPROVE`, fără refuzuri, cu dovezi actuale — și nimic nu e cablat la ea. Ce datorează totuși
rularea descriptivă e INTEGRITATEA, și de asta e codul de ieșire: sidecar necitibil, înregistrare care
a aruncat, corpus gol, orice refuz.

**Cerința de subiect vine din TRATAMENTUL LIVRAT** (prima decizie a lui Codex), nu dintr-o listă de
profile pe care nu a scris-o nimeni: un `crop` pune o fereastră 9:16 undeva fiindcă a spus o ancoră,
deci cere dovada țintei; un `fit` păstrează cadrul întreg, deci o diagramă sau un plan larg nu
datorează nicio față. Clipurile numai-`fit` TREC. Un crop cere `regime_view.target_basis`, iar
`unanchored_face` nu e destul — cazul Moist urmărește *o* față, cea din browser.

**De ce fiecare `unavailable` rămâne așa:**
- **subject** — cerința e derivată acum, dar `regime_view` nu e pe niciun sidecar stocat; apare la
  prima randare prin `clipper_shadow_views`. 58 de clipuri au shot-uri `crop`, 31 n-au `composition`
  deloc, 12 n-au listă de shot-uri.
- **frame** — RULAT. 16 exporturi au chrome de player; restul de 85 ies `not_detected`, care
  nu e o trecere (recall mic și nedemonstrat, iar modulul o spune). Istoric păstrat fiindcă e o
  capcană de rig: prima încercare a dat **zero verdicte în 38 de minute** fiindcă `server/.venv`
  avea `torch 2.13.0+cpu`. `torch.version.cuda` e `None` și
  `torch.cuda.is_available()` e `False`, deci `source_chrome._reader()` încearcă `gpu=True`, eșuează
  și cade tăcut pe `gpu=False`. Pornită pe 31 august, rularea a produs **zero verdicte în 38 de
  minute** — estimarea de „~3 ore" din handover-ul anterior presupunea GPU și era greșită. CUDA-ul
  care merge e al lui `ctranslate2` (whisper) și nu se transmite la torch. **Orice cifră din
  documente de forma „5s pe sursă pe GPU" pentru o cale easyocr nu a fost măsurată în venv-ul
  ăsta** — asta include `source_captions`. Se deblochează cu un wheel de torch cu CUDA (rig-ul e un
  RTX 2080 Super); până atunci OCR-ul e muncă de peste noapte, nu de o pauză de cafea.
- **provenance** — două motive acum, nu unul: sidecarele n-au `input_fingerprint`, și chiar când vor
  avea, un digest valid acoperă rețeta, nu fișierul livrat. Nu există azi drum către un `pass`.
- **captions** parțial (37) — `worst_share_complete` e fals fiindcă semnalele de UI și text-sursă
  n-au detecție per shot.
- **equivalence** (78) — 43 fără listă de shot-uri citibilă, 35 fiindcă ritmul nu se evaluează.

## GATE-UL VIZUAL UMAN — FĂCUT, 31 august 2026

Vezi [`docs/refs/human-gate-2026-08-31.md`](../../../refs/human-gate-2026-08-31.md) pentru verdicte
verbatim și diagnostic. 20 de clipuri alese pe dovadă, **11 bad / 9 ok**. Trei porți care stăteau de
patru sesiuni s-au închis — toate trei **PICAT**:

- **R3a PICAT.** Crop-ul chiar urmărește personajul din browser pe Moist. Confirmă ce refuzau
  comentariile din cod să afirme în vreo direcție: `stable_track` găsește un cluster geometric, nu o
  persoană.
- **R3b PICAT, 8 din 8.** Nota decisivă: „detecția pe fețe e bună, doar în momentul în care intră
  16:9 cam 2 secunde nu e tranziția bună."
- **R6 captions duble PICAT, 4 din 4**, pe corpusul RE-RANDAT. Întrebat direct a cui subtitrare se
  vede, omul a răspuns: **a SURSEI.** Prima confirmare umană pe care o primește vreunul dintre
  instrumentele astea, și validează trei lucruri deodată: `source_captions` nu dădea fals pozitiv,
  cele 37 de respingeri ale preflight-ului sunt DEMONSTRATE (exact ce refuza Codex să le acorde), iar
  cerința celor două straturi era corectă.
- **Caption peste față TRECUT, 4 din 4.** Semnalul e adevărat geometric și fără consecință vizuală pe
  eșantion. **Primul lucru pe care o măsurătoare umană l-a contrazis** — ca `REVISABLE` în preflight
  pare prea strict.

### Joncțiunea crop↔fit e un salt de scară de minim 3,16x, prin construcție

Pe clipul cu timpi exacți: patru schimbări de compoziție, **toate patru în cele patru ferestre
raportate, zero ratate.** Ipoteza concurentă e falsificată de aceleași date — salturile de ancoră de
960px, 844px și **1138px, cel mai mare din clip**, sunt neraportate.

Măsurat pe toate cele 84 de joncțiuni: **median 3,58x, max 13,52x, niciuna sub 3x.** Minimul e o
identitate a geometriei 16:9 (`crop` 1,78x, `fit` 0,5625x), nu un număr de reglat.

**Livrat:** `dynamic_geometry.absorb_brief_fit_islands` — o insulă `fit` sub 4s, doar interioară,
doar spre `crop`, înainte de merge. **Prima schimbare din tot batch-ul care modifică imaginea
livrată, nu doar înregistrează lângă ea.** Direcția vine din corpus: insulele `crop` sunt 18 cu
minimul la 3,6s, cele `fit` sunt 39 cu 15 sub 4s.

**Ce NU repară, scris și în cod:** elimină 30 din 84, și **niciuna dintre cele patru pe care le-a
cronometrat omul** — alea sunt secvențe lungi și câștigate (8,8s / 13,8s / 15,8s / 14,6s / 7,5s).

### Fereastra `fit` mai îngustă — sugestia mea, măsurată și moartă

48 din 50 de cadre `fit` folosesc TOATĂ lățimea sursei; zero încap în 70%; o fereastră 4:5 ar păstra
45%. Ar tăia conținut pe practic fiecare shot `fit`. `scripts/measure_fit_content_width.py`.

### Cele 116 tăieturi invizibile sunt încă acolo

Al patrulea clip vlog, pe care diagnosticul de joncțiune nu-l acoperea, are **17 tăieturi
invizibile** — 20 de shot-uri devin 3 prin `merge_equivalent_shots`. Pe tot corpusul: **exact 116, pe
23 din 58**, cifra de bază a lui R0 neschimbată. **R1 a reparat plannerul pe 29 august și reparația
n-a ajuns niciodată într-un export**, fiindcă re-randarea din 31 august a rejucat planurile stocate
în loc să re-planifice. Cel puțin 6 din cele 11 clipuri marcate `bad` poartă și tăieturi invizibile.

## Batch R8 — ÎNCHIS, 2 septembrie 2026

`dead_air.delivered_shots` reconstruiește secvența pe care a livrat-o FFmpeg prin intersecția
shot-urilor planificate cu intervalele păstrate. Un shot fără nicio durată rămasă dispare; unul
secționat produce două bucăți, iar joncțiunea care sare peste timp eliminat este declarată separat
ca `trim_jumps`, niciodată drept tăietură normală sau echivalentă. Agregarea păstrează aceeași regulă
ca echivalența: total complet sau `unavailable`, cu limita inferioară numită separat.

Span-urile declarate sunt acceptate numai dacă sunt pozitive, sortate, fără suprapunere și, când
fereastra este declarată, în interiorul ei. Auditul R0 trece neschimbat pe piloturi: **58 clipuri,
1.341 shot-uri și 116 tăieturi echivalente**. `trim_jumps` este `unavailable`, nu zero: cele 58 de
sidecar-uri precedă cheia `drop_spans`, deci nu demonstrează că n-a existat trim. Gate-ul R8 este
acoperit prin cazurile sintetice și prin intrarea reală a auditului.

## Batch S7 — resolverul și S7a–S7f livrate, 3 septembrie 2026

`quote_resolver.py` + `scripts/measure_quote_drift.py`. Vezi
[`docs/refs/grounding-2026-08-31.md`](../../../refs/grounding-2026-08-31.md).

**164 de afirmații pe 6 surse: bound 128 (78,0%), ambiguous 15 (9,1%), absent 21 (12,8%),
|drift| median 0,9s, p90 18,1s, max 78,1s.**

**Rezultatul contrazice așteptarea cu care a fost construit.** Driftul NU se grupează: 99 din 128 de
afirmații localizate sunt deja în raza de ~9,3s a regulii livrate, 29 nu sunt, iar prinderea lor cere
o rază de peste 20s care crește direct ambiguitatea de 9,1%. **Deci fereastra nu e răspunsul** — iar
mutarea evidentă, `_NEIGHBOURS` de la 1 la 3, ar fi urcat cifra ascunzând că un sfert dintre ratări
n-au legătură cu fereastra. De aia resolverul trebuia primul.

Cele 21 `absent` reconciliază exact măsurătoarea veche: 10 „fără potrivire" + 11 `subsequence`, o
subsecvență fiind o parafrază, nu o potrivire slabă.

**Pasul doi se schimbă pe baza datelor:** nu remăsurare după lărgire, ci **A/B de prompt cu
`atom_ids`** — `matched_by` e `timestamp` pe toate cele 164, deci modelul nu numește niciodată un
atom.

**S7a — envelope și invalidare țintită — este livrat.** `reasoning_cache.py` este contractul comun,
iar `clipper_cache.py` descrie intrările fiecărui artefact. Atoms, promises, threads, episodes,
anchors și `segment_types` poartă acum versiune de envelope, identitatea intrărilor și hash-ul
payloadului. `episodes.json` există pe disc și detectorul de ancore primește exact lista validată;
un rezultat gol nu declanșează o reconstrucție ascunsă. Fingerprintul include JSON strict și
canonic, modelele implicite rezolvate pe fiecare provider, temperatura, contextul, timeout-ul,
chunking-ul și upstream-urile relevante. Schimbarea unui semnal nu invalidează promises, iar
schimbarea transcriptului invalidează tot reasoning-ul dependent.

**Migrare intenționat incompatibilă:** fișierele reasoning bare și vechiul `{stamp, data}` sunt
refuzate o singură dată și se refac la primul score. `measure_snap_board_delta.py` refuză atoms vechi
în loc să le citească drept generația curentă. Suita completă: **1.654 passed, 2 failed**, exact cele
două 404 TikTok preexistente.

**S7b — recovery per chunk pentru promises și anchors — este livrat.** `reasoning_chunks.py`
păstrează în envelope câte un rând pentru fiecare interval și text exact, cu `pending`, `usable` și
`unusable` distincte. După fiecare cerere se scrie checkpointul; rerun-ul reutilizează numai
chunkurile `usable` și reapelează numai vecinii neutilizabili. O listă goală este un rezultat real,
dar un JSON non-gol din care normalizarea nu poate produce niciun promise/anchor rămâne retryable.
Proveniența numește providerul, modelul rezolvat, amprentele promptului/răspunsului, toate
încercările, numărul de obiecte normalizate și lipsa seedului (`nondeterministic: true`). Un state
nested malformat este refuzat integral, ca un rând corupt să nu se deplaseze peste chunkul următor.
Suita completă după S7b: **1.669 passed, 2 failed**, aceleași 404 TikTok preexistente.

**S7c — cache per rundă pentru judge — este livrat.** `judge_cache.py` păstrează răspunsul brut
pentru promptul exact și îl reaplică determinist. Rândurile neparsabile, goale sau fără verdict
aplicabil sunt `unusable` și se reapelează; starea malformată ori cu runde duplicate este refuzată
integral. Envelope-ul include identitatea sursei/transcriptului, versiunea promptului, ordinea
providerilor, modelele rezolvate și setările de inferență, iar rândul persistă providerul folosit,
amprentele promptului/răspunsului, toate încercările, `seed: null` și `nondeterministic: true`.
Testul prin worker dovedește că prima rulare scrie și a doua nu apelează providerul. Eșecul scrierii
checkpointului este vizibil în trace, dar nu anulează verdictul deja aplicat; fallback-ul după un
răspuns JSON inutilizabil este raportat ca fallback. Suita completă după S7c: **1.678 passed,
2 failed**, aceleași 404 TikTok preexistente.

**S7d — `anchor_id` canonic — este livrat.** Identitatea este atribuită după dedupe-ul de overlap,
înainte de variante, și este namespaced de dovezile exacte ale sursei. Confidence-ul și proveniența
nu o schimbă; un răspuns semantic diferit de la un apel nondeterminist primește deliberat alt id.
Variantele o păstrează atât top-level, cât și în `StoryEvidence`, iar `selection_trace` o arată
separat de `moment_id`. Censusul folosește ancora pe run-urile noi și payoff bucket numai pe
artefactele vechi. Un grup în care dedupe a unit mai multe ancore păstrează reuniunea tuturor
id-urilor și grounding-ul fiecăreia, deci liderul nu micșorează numitorul. Nu schimbă selecția.
Suita completă după S7d: **1.684 passed, 2 failed**, aceleași 404 TikTok preexistente.

**S7e — scara dedupe — este livrată.** Definiția grupului și alegerea tăieturii din grup sunt acum
două întrebări diferite. Topologia greedy se construiește numai din `heuristic_score`, înghețat
înainte de judge, fiindcă un verdict nu are voie să transforme A~B și B~C într-un singur moment când
A nu seamănă cu C. După ce grupul este stabil, liderul și diversity citesc `selection_score`, care
este propagat la toate variantele momentului; un moment nejudecat cade explicit pe euristică.
`overall` mai este citit numai pentru artefacte fără niciuna dintre scalele numite. O scară
necunoscută, un scor declarat dar invalid sau un candidat non-record nu sunt transformate în altă
măsurătoare.

Măsurat fără rescriere pe toate cele **13** artefacte `candidates.json`: cele opt proiecte fără
amestec de scară rămân identice la grupuri și top-8; numai cele cinci cu verdict judge stocat se
schimbă. Noile numere reproduc exact gruparea făcută înainte de verdict și înregistrată în trace:
`pilotf81b` 23, `pilotee0e` 49, `pilot6b38` 78, `pilot2c8a` 278 și `gateslice4h` 294. Regruparea veche
pe `overall` dădea 31/48/74/273/304. Perechea 23 contra 31 și, mai ales, 294 contra 304 este aceeași
discrepanță care fusese reconstruită manual restaurând `heuristic_score`; acum codul o împiedică.
Artefactele stocate nu au fost re-score-uite, deci `selection_trace` vechi păstrează deliberat
grupurile vechi. Suita completă după S7e: **1.696 passed, 2 failed**, aceleași 404 TikTok.

**S7f — identitatea comună selection/render — este livrată.** `RunTrace.run_id` era deja comun
celor două artefacte ale score-ului; acum devine `selection_run_id` pe fiecare clip nou și în
sidecar-ul randării lui. Orice `shadow_run_id` trebuie să coincidă înainte ca DB-ul să înlocuiască
board-ul. Exporturile păstrate peste re-score rămân legate de rularea care le-a creat, iar cele
istorice rămân `null`, nu sunt atribuite trace-ului curent. ID-ul nu intră în fingerprint-ul imaginii.
Legătura face o nepotrivire observabilă, dar nu arhivează automat un trace vechi suprascris. Suita
după S7f: **1.712 passed, 2 failed**, aceleași 404 TikTok.

**S7 este închis.**

## Batch S8a — fișierul review-ului este legat de sesiune, 3 septembrie 2026

`review_media.py` și ruta de review captează SHA-256 pe TOT exportul și pe sidecar, nu doar pe
primii 8 MB. Versiunea vine din sidecarul observat, nu din rendererul instalat; `selection_run_id`,
fereastra și transcriptul sunt păstrate cu sesiunea. Lipsa istorică rămâne lipsă, iar o sesiune cu
versiuni mixte le enumeră fără să pretindă una comună. Pozițiile/rank-urile sunt fixate la creare.

Înainte de prezentare, video și răspuns, ambele amprente se verifică. Fișierul schimbat dă 409,
nu primește verdict pentru versiunea nouă. Un proiect cerut fără board, un membru fără export sau
un amestec de selection runs în același proiect refuză sesiunea întreagă. Modificările ulterioare
în DB nu schimbă timpii/textul arătat. Răspunsul și evenimentul `reviewed` păstrează amprentele
media, separat de aprobările de produs. Rezultatele vechi se pot citi; sesiunile vechi fără
snapshot nu mai pot primi răspunsuri noi. Nu s-au modificat fișierele media sau creat review-uri reale.

**Limită explicită:** hash-ul leagă bytes, nu certifică imaginea, durata, vizionarea de către om
sau asocierea istorică sidecar/encode. S8a nu închide S8. Urmează
randarea neutră, payoff/diversitate în rubrică, review tehnic separat, minimum 10 SURSE distincte
(nu 10 proiecte/clonări) și 150 de momente, macro-agregare și intervale de încredere. Teste:
**1.739 passed, 2 failed**, aceleași 404 TikTok; typecheck curat.

## Batch S8b — dezvăluire numai după completare, 3 septembrie 2026

Ruta `/result` și butonul nu mai arată nici board-urile, nici tally-ul parțial înaintea ultimului
răspuns valid. Completitudinea compară id-urile planificate cu cele judecate, nu doar numărul.
Răspunsurile salvate nu mai pot fi rescrise; o retrimitere identică este acceptată fără feedback
dublat. Lock-ul OS din `review_lock.py` acoperă citirea/scrierea și copia feedback în ambele procese
de backend, nu numai într-un event loop. Concurența primește 409 retryabil; lock-ul se eliberează
la ieșirea procesului. Fișierul mic `.lock` rămâne intenționat pe disc pentru a nu crea doi inodes
independenți sub același nume. Nu este un job persistent care trebuie deblocat manual.

Sesiunea JSON rămâne sursa de adevăr. Dacă salvarea răspunsului reușește și DB-ul pică, retrimiterea
aceluiași răspuns completează feedback-ul lipsă; nu rescrie judecata. Sesiunile vechi (schema 1/2)
pot fi consultate ca istorice, dar nu pot continua: rezultatul lor putea fi dezvăluit pe parcurs.
Schema nouă este 3, politica `sealed_until_complete_v1`; rubrica rămâne `blind_eval_v1`.
UI-ul permite consultarea istoricului sau pornirea unei alte sesiuni când reluarea este refuzată.

**Următorul pas implementabil:** randare neutră separată pentru evaluare și rubrica S8 completă,
nu activarea motorului. Trebuie păstrate cohorta de surse distincte, review-ul tehnic separat și
gate-urile umane; protecția API-ului nu demonstrează superioritate semantică. Nu s-a rulat vreun
model și nu s-au creat sesiuni reale în S8a/S8b.

Verificare după S8b: **1.752 passed, 2 failed**, numai cele două 404 TikTok preexistente;
66 teste de review trec, inclusiv API și două procese; typecheck curat.

## RE-PLANIFICARE ȘI RE-RANDARE — 5 septembrie 2026

Cele 58 de exporturi ale piloturilor au fost **re-planificate**, nu rejucate. `rerender_pilots.py`
re-encoda din sidecarul de pe disc, deci planul rămânea cel din 22 august — motivul pentru care R1 a
aterizat pe 29 august, a scos 116 tăieturi invizibile din planner și **n-a schimbat niciun export**.
`scripts/replan_and_rerender.py` rulează `_decide_render` din nou.

Originalele sunt în `<proiect>/exports_pre_replan/`, iar `exports_pre_caption_fix/` de dinainte
rămâne neatins. O a doua rulare nu le poate suprascrie.

**Nu s-a pornit fără dovadă.** `scripts/verify_replan.py` re-planifică fără să encodeze și cere patru
probe; a rulat până a trecut pe toate. Apoi un micro-gate pe patru clipuri, câte unul pentru fiecare
defect confirmat, înainte de corpus.

### Poarta R0, pe corpusul nou

```
clipuri                    58        exit 0, integrity_ok: True
shot-uri                 1231        (erau 1.341)
tăieturi echivalente        0        ← erau 116, cifra de bază a batch-ului
salturi induse de trim      0
granițe necontigue          0
start pe primul cuvânt  58/58
coadă <= 50ms           22/58        ← neschimbat, e defectul de boundary
compoziție          crop=1198, fit=33
fingerprints          valid=58        ← erau `unavailable`
```

**Cele 116 sunt zero.** Prima oară când reparația R1 ajunge într-un fișier, la șapte zile după ce a
fost scrisă.

### Preflight-ul R7, pe corpusul nou

```
APPROVE 0    REVISE 67    REJECT 22    UNDECIDED 12

geometry_and_duration                  pass 58   fail  0   unavailable  43
cut_equivalence_and_profile_rhythm     pass  0   fail  0   unavailable 101
subject_present_when_required          pass  2   fail 12   unavailable  87
usable_frame_in_fit_and_no_chrome      pass  0   fail  0   unavailable 101
captions_not_duplicated_or_unreadable  pass  0   fail 55   unavailable  46
boundary_complete                      pass 44   fail 56   unavailable   1
provenance_complete                    pass 58   fail  0   unavailable  43
```

Trei mișcări reale, iar cele 43 de `unavailable` de peste tot sunt proiectele nere-randate:

- **`provenance_complete`: 0 → 58 treceri.** Verificarea a fost `unavailable` pe 101 din 101 tot
  batch-ul — întâi fiindcă nimic nu purta amprentă, apoi fiindcă un digest de rețetă nu atinge
  fișierul. Acum sidecarul poartă și `output_identity`, iar cele două jumătăți se verifică amândouă.
- **`cut_equivalence`: 23 eșecuri → 0.** Nu mai există niciun clip cu tăieturi pe care privitorul nu
  le poate vedea. Rămâne `unavailable` peste tot, fiindcă jumătatea de ritm nu se evaluează —
  starea onestă, nu o regresie.
- **`subject`: 0/0/101 → 2 treceri, 12 EȘECURI, 87 unavailable.** Prima oară când verificarea are
  intrare, fiindcă `regime_view` ajunge acum pe sidecar.

### Cele 12 eșecuri de subiect sunt toate `pilot2c8a`, și confirmă automat gate-ul uman

| clip | shot-uri `crop` | fără țintă | bază |
|---|---:|---:|---|
| `003a5c53c51d` | 46 | **37** | `stable_anchor` |
| `ec47597c60f2` | 17 | **17** | `stable_anchor` |
| `2a59b1e41880` | 21 | 17 | `stable_anchor` |
| `38aa005c7c1f` | 20 | 17 | `stable_anchor` |
| `ad1b8004ece9` | 10 | **10** | `stable_anchor` |
| `d646da7201ad` | 9 | 8 | `stable_anchor` |

Trei clipuri au **fiecare** shot `crop` ținut peste un interval unde ținta e măsurat absentă. Toate
poartă `target_basis: stable_anchor` — adică o ancoră a fost găsită, iar întrebarea „era ținta acolo"
e alta, exact distincția pe care Codex a numit-o: *o bază e o metodă, nu o măsurătoare.*

**Niciun alt pilot nu are vreun eșec.** Și pilotul care le are pe toate 12 e chiar cel pe care omul l-a
marcat pe 31 august: „personajul din browser e pus bine când vorbește". Verificarea construită după
review-ul lui Codex găsește acum defectul R3a singură, pe exact sursa unde un om îl văzuse.

### O scăpare a mea, și reparația

Prima re-randare a folosit `burn/default` pe toate cele 15 clipuri `pilotf81b` — deci **am recreat
cele 37 de duplicate**, exact ce avertizase Codex. Construisem mecanismul de politică de captions și
nu setasem setarea pentru proiectul pe care omul îl confirmase.

Reparat: `source_has_burned_captions: true` pe `pilotf81b`, care înregistrează verdictul lui din 31
august („se mai vede subtitrarea" = a SURSEI, 4 din 4), apoi cele 15 clipuri re-randate.
**REJECT 37 → 22.** Cele 22 rămase sunt `39c89ae2e16e` și `43a509687a33` — surse pe care detectorul
le dă `present` și pe care **nimeni nu le-a confirmat**, deci rămân respinse. Corect: detectorul e
`calibrated: false` și n-are voie să decidă singur.

### Ce NU s-a mișcat, și de ce

- **`boundary_complete` 44/56/1, neschimbat.** Re-planificarea nu re-scorează, deci ferestrele sunt
  aceleași. R5a mută finalurile la următoarea RULARE DE SCORING, nu la o re-randare.
- **`usable_frame` 101 `unavailable`.** Cache-ul de chrome e cheiat pe identitatea mp4-ului, iar 58 de
  fișiere tocmai s-au schimbat — deci verdictele lor sunt corect invalidate și cer ~66 de minute de
  OCR. Cache-ul a făcut exact ce trebuia.
- **Cele 57 de joncțiuni lungi** rămân. Verdictul uman din 4 septembrie a ales hard cut dintre trei
  prezentări; niciuna nu era una pe care el s-o numească bună.

## ÎNCADRAREA PE FAZE — stare la 9 septembrie 2026

Istoricul complet al arcului 5–9 septembrie este în
[`handoff-clipper-session-5.md`](../../archive/clipper/handoff-clipper-session-5.md).
Aici doar starea.

### Ce este livrat și măsurat

**Stratul suprimat, reparat.** `services/clipper/layout_policy.py` decide
`SECOND_CAMERA` / `NO_SECOND_CAMERA` (`SETTING = "source_has_a_second_camera"`);
`plan_dynamic_edit(..., no_second_camera=True)` scoate ramura `alive`. Verificat
**pe cadre randate**, nu pe plan: pe `pilotf81b` 48,1 s → **0,0 s** în care
încadrarea nu conține subiectul, fețe găsite în export 60/71 → **71/71**, clip de
control identic pe octeți (`23d9ec2a87842a67`).

**Generațiile de exporturi.** `services/clipper/export_generations.py` înlocuiește
cele două `_preserve` care aveau bug-uri opuse — unul continua când backup-ul
exista și pierdea generația pe care o înlocuia, celălalt refuza și nu putea
păstra niciodată a doua. Serial (`exports_pre_replan_02`), inventar cu sha256
scris **în interiorul** copiei. 19 inventare există deja pe disc.

**Adresarea temporală.** Indexul se citește ÎNAINTE de `read()`, timpul DUPĂ —
cele două proprietăți ale decodorului sunt în dezacord cu un cadru în același
moment. Reparat în `source_caption_observation.observe` și în cele trei scripturi
care îl oglindesc, cu test care pică pe ordinea veche. `build_phase_regions._in_clip`
refuză orice cadru din afara clipului: **f2425 era cadru de construcție al fazei
`screen` și e la 242,5 s față de un final la 242,42.**

### Ce NU este verificat, și de ce

**Cele patru regiuni ale lui `b23c14c41495` sunt înghețate dar NU sunt
verificate.** Candidatul curent e construit din adnotări citite pe proxy la
`--scale 3.0`, despre care lotul de calibrare a arătat că sunt deplasate cu 10–154
px sursă față de o toleranță declarată de ±25,6 px orizontal / ±14,4 vertical —
**de șase ori toleranța, în ambele direcții**. Toleranța nu era o măsurătoare a
instrumentului, era o presupunere despre el.

**Constatarea deschisă:** pe primele 7 din 21 de cadre ale fazei `screen`, citite
întregi la rezoluția sursei, capul vorbitorului ajunge la x 0,785–0,810 față de o
muchie a regiunii la 0,755 — **tăiat cu până la 141 px sursă**. Aceeași margine a
trecut prin patru verdicte (tăiat → ținut → nemăsurat → tăiat) și abia ultimul s-a
uitat la obiectul întreg.

Rămân în plus neverificate: subiectul fazei `speech` (44 + 55 cadre de hold-out
fără adnotare de față), jumătatea de LINII pentru regiunile care s-au mutat între
timp, tranzițiile (rezultatul lor stă pe regiuni care s-au mutat de atunci) și
deficitul din `screen` q0, unde a existat un singur cadru eligibil.

**Nimic din toate astea nu e cablat la randare.**

### Instrumentele, și de ce fiecare există

| Fișier | Ce face, și defectul care l-a cerut |
|---|---|
| `scripts/watch_annotations.py` + `_fresh.py` | geometria confirmată vizual, trei loturi (construcție / hold-out / fresh). Poartă valorile ÎNLOCUITE și un motiv pe cutie, fiindcă întrebarea următorului cititor e „a fost mereu așa, sau a fost mutată după un verdict" |
| `scripts/build_phase_regions.py` | o regiune constantă pe fază, din observații confirmate; refuză o fază fără adnotare de subiect, și orice cadru din afara clipului |
| `scripts/verify_phase_regions.py` | singurul test necircular; verifică LINIILE și SUBIECTUL separat, plus tranzițiile; `--set holdout\|fresh` |
| `scripts/select_fresh_lot.py` | alege lotul ÎNAINTE să-l vadă cineva, după o regulă convenită în avans; raportează deficitul, nu-l completează |
| `scripts/replay_annotations.py` | desenează coordonatele STOCATE peste cadrul pe care îl numesc — verifică transcrierea și adresarea, nu geometria |
| `scripts/source_crop.py` | **sursa 2560×1440, fără interpolare.** `--full --bare` e vederea pentru pasul 1; `--edge` pentru o singură margine. Grila e etichetată în fracțiuni din CADRUL ÎNTREG |
| `scripts/edge_calibration.py` | pasul 2: intervale precise, doar pentru marginile care stabilesc extremele |
| `scripts/coverage_bounds.py` | pasul 1: o limită conservatoare pentru fiecare parte pe fiecare cadru relevant |

### Cinci lucruri de nu repetat

1. **Un cadru pe planșă.** Planșele cu două cadre alăturate au propria riglă per
   tile; fiecare cadru din dreapta a ieșit deplasat cu 0,26–0,30 din cadru și a
   distrus prima adnotare întreagă.
2. **Mărirea proxy-ului nu adaugă detaliu.** 480×270 mărit la 1440 interpolează.
   Sursa e 2560×1440 și e pe disc.
3. **Un decupaj strâns pe o muchie ascunde conturul care decide extrema.**
   `f2400` a fost citit la înălțimea 0,20–0,35, unde silueta se vede pe cer, iar
   capul e cel mai lat mai jos, în bokeh.
4. **Nu compara o măsurătoare cu una luată cu alt instrument.** Prima rulare de
   hold-out a raportat un fapt despre două treceri de citire ca fapt despre
   regiune.
5. **Nu înlocui ±0,01 cu ±0,05 fiindcă acoperă discrepanțele.** Incertitudinea e
   intervalul măsurat pe cadrul căruia îi aparține; verdictul e dacă muchia
   regiunii cade înăuntrul lui.

### Punctul exact de reluare pentru încadrare

Planul e al lui Codex, în două treceri, și **nu cere încă un lot nou de 36**:

1. **PASUL 1, în curs: `coverage_bounds`.** 27 din 258 de perechi cadru/parte au
   limită; 1 confirmată absentă; **230 necitite**. Rulează cu exit 2 până se
   închide. Vederea e `source_crop --full --bare` — obiectele întregi, **fără
   nicio cutie desenată**, fiindcă limita trebuie confirmată pe sursă, nu
   moștenită din cutia greșită. Se înregistrează o limită sigură („marginea
   dreaptă e înainte de 0,79"), nu o poziție inventată.
2. **PASUL 2: `edge_calibration`,** intervale precise doar pentru marginile care
   ies extreme ale reuniunii (`coverage_bounds --extremes` le numește) și pentru
   cazurile neclare. Ce nu poate influența reuniunea nu merită precizie.
3. **Construcția** ia **capetele exterioare** ale intervalelor (stânga/sus =
   capătul inferior, dreapta/jos = capătul superior), reuniune peste cadrele
   fazei, intervalele păstrate separat de cutia derivată, rotunjirea în pixeli
   păstrează conținerea. Promisiunea rezultată e „conține toate pozițiile permise
   de observațiile înregistrate" — **nu** „păstrează subiectul pe toată durata".
4. **Reconstruiește o singură dată**, apoi randează proba video: conținerea,
   mărimea textului și a subiectului, tranzițiile și retragerea ceasului, pe toată
   secvența. Cele trei tabele existente rămân material de dezvoltare și regresie,
   cu istoricul păstrat; **nu mai pot fi prezentate ca verificare independentă** a
   candidatului nou.
5. Abia după ce instrumentul e stabil: cele 99 de cadre de față pentru `speech`.

## Punctul exact de reluare

**Nimic din motor nu e activ.** R2, R3a, R3b, R4, R5 și R6 sunt instrumentare în umbră: calculează,
scriu în sidecar sau în `candidates.json`, și nu mișcă niciun cadru. Singurele două care ating
comportamentul sunt R1 (scoate tăieturile invizibile din planurile viitoare, prin construcție fără
schimbare de imagine) și R5a (mută finalul a 261 de ferestre, la următoarea rulare).

**Ce nu pot închide agenții, în ordinea în care blochează:**

1. ~~**Gate-ul vizual R3a / R3b / R4**~~ — **FĂCUT 31 august, PICAT.** Vezi secțiunea de mai sus.
   Ce a mai rămas de la un om: `pilotee0e/a9f653576eb7` a ieșit `bad` și are o singură schimbare de
   compoziție — explicat ulterior prin cele 17 tăieturi invizibile, dar nimeni n-a confirmat că
   ALEA erau ce a deranjat.
2. **Gate-ul R5** — „≥95% începuturi și finaluri acceptate la review uman". Încă nefăcut.
3. ~~**Gate-ul R6, captions duble**~~ — **FĂCUT 31 august, PICAT 4/4**, cu sursa confirmată de om.
   Rămâne calibrarea detectorului: `calibrated: false` pe patru surse, și handover-ul cere explicit
   să nu se materializeze dezactivarea captions-urilor cât timp e așa.
4. ~~Culorile de highlight ale lui `Neon Pop` și `Viral Gradient`~~ — **DECIS 31 august 2026: rămân
   cum sunt, documentate.** Podea de 2,33 și 2,72, sub pragul de 3,0:1, adică paleta nu poate
   GARANTA bara pe orice fundal. Motivul acceptării e în aritmetică: podeaua e
   `sqrt(contrast(fill, contur))`, deci 3,0 cere 9:1 între cele două culori ale glifului. `Viral
   Gradient` e la 7,41 și s-ar repara ușor cu `#FF9364`, dar `Neon Pop` e la 5,42 și nici cu contur
   negru pur nu trece de 2,43 — roz-ul lui ar trebui deschis până pe la `#FF9DBB`, ceea ce e alt
   preset. Niciunul nu a fost folosit vreodată: toate cele 99 de exporturi cu stil sunt
   `Bold Impact`. Excepția e în `caption_contrast.KNOWN_SHORTFALLS`, **cheiată pe PALETĂ nu pe
   nume**, deci o revopsire care le înrăutățește repornește poarta.

**Ce poate face un agent, dar durează și atinge date:**

5. **Re-score pe o CLONĂ** — măsoară delta lui R5a pe scoruri, dedupe, shortlist, judge și board.
   Trebuie să includă `39c89ae2e16e` și `43a509687a33`, nu doar cele patru piloturi: acolo sunt cele
   trei mutări extreme. Nu pe proiectele-baseline.
6. **Re-randarea piloturilor** — abia atunci cele 116 tăieturi invizibile ale lui R1 devin 0 în audit,
   și abia atunci gate-ul §6 al lui R4 poate fi măcar încercat.

**Cod, în ordinea planului:**

7. **R6, restul**, strict în shadow. Poziționarea, motivul, contrastul, maparea dovezilor și
   warning-ul de browser chrome sunt livrate (`caption_placement`, `caption_choice`,
   `caption_contrast`, `evidence_map`, `source_chrome`). A rămas detecția per-shot pentru UI și
   textul sursei — lipsește un DETECTOR, nu un mapper — și cablarea în sidecar.
7a. **R7 e LIVRAT și CORECTAT** (`publish_preflight`, `publish_checks`, `publish_captions`,
   `publish_corpus`, `bounded_correction`, `scripts/audit_publish_preflight.py`). Cele opt defecte
   ale review-ului Codex sunt reparate; **nu i-am trimis reparațiile la re-review**, deci verdictul
   lui de acum e cel pe versiunea `d2b2079`. Nimic nu e cablat la randare: `applied` e fals peste
   tot. Ce se poate face fără decizii noi, în ordinea valorii:
   **(a)** reluat `--with-chrome` peste corpus, ~3 ore de OCR cu cache reluabil pe disc, ceea ce
   închide verificarea de cadru; **(b)** o randare prin calea de job ca sidecarele să capete
   `input_fingerprint` și verificarea de provenance să înceteze a mai fi `unavailable`;
   **(c)** cablat preflight-ul în `clipper_render_jobs` ca verdictul să ajungă pe sidecar lângă cel
   vechi — dar NU ca poartă, fiindcă asta e o decizie de produs pe care n-a luat-o nimeni. Pentru cablare, ține minte că `panels_to_keep_out` NU e un mapper generic:
   sare deliberat peste shot-urile de față, deci fețele și textul sursei au nevoie de mapper propriu.
   **Nu materializa dezactivarea captions cât timp detectorul rămâne `calibrated: false`.**
8. **R7** — preflight de publicare și corecția bounded (maximum una).
9. ~~**R8** — reconstrucția secvenței livrate după trim.~~ **ÎNCHIS 2 septembrie 2026.**
10. ~~**S7** — resolverul determinist, envelope/fingerprint, recovery per chunk și per rundă,
    `anchor_id`, scara dedupe și identitatea comună selection/render.~~ **ÎNCHIS 3 septembrie 2026.**
11. **S8** — S8a leagă sesiunea de media exactă, S8b protejează dezvăluirea și răspunsurile;
    continuă randarea neutră și evaluarea selecției, minimum 10 surse distincte și 150 de momente.
    Gate-ul rămâne deschis.
12. **P** — activarea graduală.

**Încadrarea pe faze** are propriul punct de reluare, în secțiunea de mai sus.
Nu e în lista numerotată fiindcă nu e un batch din planul de producție: a pornit
dintr-un defect livrat și a devenit o revizuire a instrumentului de măsură.

**În afara planului:** pipeline-ul TikTok nu e construit — router-ul nu e montat, sidebar-ul duce la o
pagină inexistentă, iar cele două teste care pică în suită sunt ale lui, de dinaintea acestor sesiuni.

Nu activa `story_v2`. Planul separă gate-ul de selecție de gate-ul de randare tocmai fiindcă
review-ul existent le-a amestecat.

## Starea artefactului curent

Recalculat din `analysis/selection_trace.json`, run `99105dc0fd5f` — **acestea sunt cifrele de
comparat cu o rulare nouă**, nu cele din tabelul de mai sus:

**Atenție: sunt două grupări diferite și nu trebuie amestecate.** `dedupe_group` din
`selection_trace.json` grupează **tot câmpul**; `judge_pool_moments` din `reasoning_run.json` este
shortlist-ul **plafonat** trimis la judge. De aceea 304 > 80 — plafon, nu propagare. Propagarea
explică altceva: de ce 109 grupuri conțin un verdict deși numai 80 au mers la judge.

Acesta este artefactul pre-S7e: finalul s-a regrupat pe `overall` și a produs 304, în timp ce
gruparea euristică înghețată din aceeași rulare produsese 294. S7e face noile run-uri să păstreze
topologia de 294 și să folosească verdictul numai pentru liderul din fiecare grup; cifra istorică
de mai jos nu este rescrisă retroactiv.

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
- **Reasoning Batch 7 este închis prin S7a–S7f; 9 și 10 nu sunt închise.** S8a/S8b protejează
  review-ul, dar nu înlocuiesc gate-urile de randare și evaluarea umană din plan.
- **Rendererul v3 nu este aprobat pentru publicare automată.** Geometria trece, dar auditul a găsit
  116 tăieturi invizibile, ritm de 29,3/min, boundaries fără padding, captions duble și browser UI.
  Din lista aia, captions-urile duble sunt acum DETECTATE (R7 respinge 37 de clipuri pentru ele) și
  browser UI e detectat (`source_chrome`, 14/14 pe Moist); restul rămâne.
- **Sidecarul nu înregistrează setul de keep-out față de care s-a plasat caption-ul.**
  `panels_to_keep_out(panels, shots)` se adaugă la randare și `panels` nu se scrie nicăieri, deci
  decizia de plasare NU e reproductibilă din ce e stocat. Nu e o problemă a vreunui clip; e o gaură
  de provenance în format, și e motivul pentru care `audit_caption_placement` refuză întrebarea
  „ar produce regula de azi poziția asta".
- **`caption_plan.y_pct` nu e poziția livrată.** E preset-ul; `.ass` poartă poziția rezolvată, și
  diferă pe 46 din 99 de clipuri cu până la 933px. Orice cod nou care vrea să știe unde a aterizat
  caption-ul citește `.ass`.
- **R7 nu e cablat la randare.** Verdictul se calculează, nu se scrie pe sidecar și nu blochează
  nimic. Dacă e sau nu o poartă e o decizie de produs pe care n-a luat-o nimeni — la fel ca la
  `review.py`, unde comentariul spune exact asta. Codex a adăugat un motiv în plus pentru care
  cablarea nu e o formalitate: **căile `APPROVE`-la-eșec din `review.py` au rămas acolo**, deci un
  consumator care ar citi „verdictul" ar avea două verdicte cu același nume și semantici opuse.
- **`provenance_complete` NU ARE azi drum către un `pass`, și e o proprietate a formatului.**
  Amprenta se ia peste `FINGERPRINT_KEYS`, iar `render_input` completează cu `None` orice cheie
  absentă — deci un sidecar care nu poartă decât digestul unei rețete goale se validează perfect. Și
  chiar completă, rețeta nu atinge nicăieri fișierul livrat: nici dimensiune, nici hash, nici durata
  mp4-ului. Propriul comentariu al lui `FINGERPRINT_KEYS` spune că dimensiunea de ieșire n-are o
  autoritate comună de unde să fie citită. Un digest valid demonstrează că planul n-a fost editat
  după randare; atât.
- **`exports/*.json` e un namespace cu ȘASE cititori care îl interpretează ca sidecar de clip.**
  `audit_clipper_exports`, `audit_caption_placement`, `audit_caption_contrast`,
  `audit_publish_preflight`, `build_shot_merge_fixture` și `rerender_pilots`. Nimic altceva nu are
  voie să scrie acolo un `.json`. Un singur fișier de cache pus acolo a făcut poarta R0 să iasă cu 2
  — și a stat așa, nedescoperit, până a citit Codex codul. Artefactele auxiliare merg în directorul
  lor (`<proiect>/chrome_cache/` e primul).

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
- [`handoff-clipper-session-5.md`](../../archive/clipper/handoff-clipper-session-5.md) — încadrarea pe faze și cele cinci defecte ale instrumentului de măsură
- [`handoff-clipper-session-4.md`](../../archive/clipper/handoff-clipper-session-4.md) — istoric detaliat
