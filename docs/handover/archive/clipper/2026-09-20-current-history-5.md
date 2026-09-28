# Arhivă Clipper — puncte de reluare și limite istorice

Mutat din CURRENT la 20 septembrie 2026. Istoric, inclusiv afirmații ulterior
retrase; nu este verdictul stării actuale. Conținutul secțiunilor este păstrat.

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
