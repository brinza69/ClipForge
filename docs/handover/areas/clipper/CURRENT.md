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

**Browser chrome:** măsurat cu easyocr pe 20 de proxy-uri × 8 cadre — 0 potriviri, din 453 de tokeni
citiți. Corpusul nu conține niciun pozitiv, deci un detector nu poate fi calibrat aici.

**Ce a mai rămas din R6:** contrastul pe banda blurată de `fit`, warning-urile de browser chrome,
cablarea în sidecar. `panels_to_keep_out` nu e mapper generic — sare peste shot-urile de față.

## Punctul exact de reluare

**Nimic din motor nu e activ.** R2, R3a, R3b, R4, R5 și R6 sunt instrumentare în umbră: calculează,
scriu în sidecar sau în `candidates.json`, și nu mișcă niciun cadru. Singurele două care ating
comportamentul sunt R1 (scoate tăieturile invizibile din planurile viitoare, prin construcție fără
schimbare de imagine) și R5a (mută finalul a 261 de ferestre, la următoarea rulare).

**Ce nu pot închide agenții, în ordinea în care blochează:**

1. **Gate-ul vizual R3a / R3b / R4** — patru clipuri, un om: Moist (creatorul, nu fața din browser),
   Jensen (diagramă lizibilă), vlog (obiectele în `fit`), go ghost (fără comutări false).
2. **Gate-ul R5** — „≥95% începuturi și finaluri acceptate la review uman".
3. **Gate-ul R6** — „zero captions duble pe cele 15 go ghost", care cere și re-randare, și un om.

**Ce poate face un agent, dar durează și atinge date:**

4. **Re-score pe o CLONĂ** — măsoară delta lui R5a pe scoruri, dedupe, shortlist, judge și board.
   Trebuie să includă `39c89ae2e16e` și `43a509687a33`, nu doar cele patru piloturi: acolo sunt cele
   trei mutări extreme. Nu pe proiectele-baseline.
5. **Re-randarea piloturilor** — abia atunci cele 116 tăieturi invizibile ale lui R1 devin 0 în audit,
   și abia atunci gate-ul §6 al lui R4 poate fi măcar încercat.

**Cod, în ordinea planului:**

6. **R6, restul**, strict în shadow. Poziționarea și motivul sunt livrate (`caption_placement`,
   `caption_choice`); au rămas contrastul pe banda blurată de `fit`, warning-urile de browser chrome
   și cablarea în sidecar. Pentru cablare, ține minte că `panels_to_keep_out` NU e un mapper generic:
   sare deliberat peste shot-urile de față, deci fețele și textul sursei au nevoie de mapper propriu.
   **Nu materializa dezactivarea captions cât timp detectorul rămâne `calibrated: false`.**
7. **R7** — preflight de publicare și corecția bounded (maximum una).
8. **R8** — reconstrucția secvenței livrate după trim; până atunci `edit_quality` refuză jumătatea
   bazată pe shot-uri pe exporturile cu `drop_spans`.
9. **S7** — închiderea infrastructurii reasoning v2.
10. **S8** — evaluarea selecției, review orb, minimum 10 surse și 150 de momente.
11. **P** — activarea graduală.

**În afara planului:** pipeline-ul TikTok nu e construit — router-ul nu e montat, sidebar-ul duce la o
pagină inexistentă, iar cele două teste care pică în suită sunt ale lui, de dinaintea acestor sesiuni.

Nu porni Batch R7–R8 în paralel și nu activa `story_v2`. Planul separă gate-ul de selecție de gate-ul
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
