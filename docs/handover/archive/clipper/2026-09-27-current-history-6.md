# Arhivă Clipper — 9–24 septembrie 2026: planul master Codex, rescorarea, subtitrarea dublă, încadrarea

Mutat din CURRENT la 27 septembrie 2026, pentru limita de 500 de linii. Istoric, inclusiv afirmații ulterior
retrase; nu este verdictul stării actuale. Conținutul secțiunilor este păstrat.

## Planul master Codex, 24 septembrie 2026 — cod comis (`cc9921b`, `f4c0e88`, `1267a13`, `172242c`)

Planul curent e **`PRPs/clipper-master-plan-2026-09-24.md`**, scris de Codex la cererea
utilizatorului. Are patru valuri, fiecare cu un lot A (Claude desktop: UI, browser, verificare
vizuală, documentație) și un lot B (Claude CLI pe contul 2: backend, teste, audit offline), pe
fișiere disjuncte. Codex validează. Dovezile sunt în `data/claude-master-20260924/{A,B}/`.
- **A1 acceptat** (inventar + martori vizuali; `codex-verdict-wave1.md`). Instrumentele au fost
  corectate după verdict și au 13 teste private. Constatări: pe 6053, crop-ul taie subtitrarea
  sursei și cadrul sare la 45 s; pe c04e un participant e tăiat la 3,133 s; pe co-stream, identitatea
  subiectului e deschisă (Kai în panoul de față).
- **B1 acceptat parțial:** o editare și evenimentul care o protejează sunt acum aceeași tranzacție,
  sub `BEGIN IMMEDIATE`, iar rescorarea șterge doar ce nu e protejat. Codex a găsit 6 contraexemple
  (regenerare lentă peste o editare mai nouă, headline peste export activ, titlu/transcriere
  pierdute, lock ocupat, claim fără job) → **lotul Bfix rulează**.
- **B2 (contractul de bază) terminat:** o schimbare doar de subtitrare la proiect nu mai rescorează;
  câmp nou `effective_caption_policy`. Amendamentele C1–C4 (eveniment de sistem
  `export_invalidated`, politica efectivă în toate răspunsurile, curse cu claim-ul) sunt în Bfix.
- **A2:** UI-ul e scris (control de proiect `project-caption-source.tsx`, decizia efectivă în
  editor). Acceptarea în browser vine după Bfix.
- **Badge-ul de joburi:** alias fără slash în `server/routers/jobs.py`, cu test și dovadă negativă.
  Slash-ul în frontend nu merge, fiindcă Next.js îl scoate cu un 308 (`A/badge-finding.md`).
- **A4 pregătit:** `A/release-cases.json` (5 extrase de 10 min din sursele martorilor, rubrica
  „merită clipul” separată de „arată bine”). Nu există încă surse hold-out.
- **25 sept. — verdictul Codex pe valul 2** (`codex-verdict-wave2.md`):
  - **A2** e acceptat în browser pe codul nou (`A/caption-policy-browser.md`).
  - **Bfix2** e conform (Q1 test despărțit, Q3 motiv de respingere). Reclaim-ul de 120 s e retras;
    interimar, un enqueue eșuat lasă clipul `exporting`.
  - **R4b e aprobat cu amendamente** (coloana nullable `clips.export_job_id`, claim + job în aceeași
    tranzacție, `fail_job`/`cancel_job` fără efect pe clip pentru preview). **Implementarea rulează pe
    contul 2.**
  - **B3:** infrastructura e acceptată, dar dovada componenței shadow e insuficientă. Trebuie selecția
    structurală în trace pentru rulările noi, înainte ca vreo cohortă să fie eligibilă. A3 are UI-ul
    scris și verificat preliminar; nu e acceptat.
  - **B4** (audit: 9/9 unavailable, corect fără vizionare umană) și tablele A4 (`A/release-boards/`)
    sunt în revizie independentă Opus.
  - Suita completă se rulează O SINGURĂ dată, după R4b + B3 corectate și acceptările A2/A3. Exporturile
    noi A4 așteaptă R4b. Foaia de verdict editorial UMAN e `A/editorial-review.md`.
  - **Codex mai avea 6% din limita săptămânală**; de pe 25 sept. e **fără limită până pe 30 sept., 12:41**.
    Revizii Opus independente pe contul 2 țin locul validării lui până atunci.
- **25 sept. — R4b, B3r, A3 și A4 (comise în `f4c0e88`, `1267a13`, `172242c`):**
  - **R4b** e implementat (`B/R4b-result.md`). Revizia independentă (`review-R4b-B3r/REVIEW.md`) a decis
    „close with conditions” și a găsit:
    - **F1:** un preview eșuat sau anulat marca proiectul `failed`/`cancelled`, iar recuperarea eșua
      apoi exportul viu al altui clip;
    - **F2:** `.ass`-ul era comun încercărilor;
    - **F3:** rămâneau `.cmd.txt` ascunse;
    - **F4:** nu exista un test pentru `origin` pe jobul de export.
  - **R4b-r2** (`B/R4b-r2-result.md`) le repară pe toate, plus F5 (B3r) și F6. Fiecare are un test care
    pica înainte: 12 picau, acum trec 21; setul obligatoriu dă 720 passed.
    - Pe codul nou, probele originale ale reviewer-ului dau 16/17. P4 pică doar din stub-ul lui:
      scrie într-un director pe care `_write_ass` real îl creează. Adaptarea lui B adaugă numai acel
      `mkdir`.
    - Verificat și în browser: anularea unui export lasă proiectul `ready`; retry-ul publică `.ass`-ul
      cu sha-ul din sidecar și nu lasă niciun fișier ascuns.
    - **De confirmat de Codex:** Q1–Q4 din `R4b-r2-result.md`. Cea mai importantă e Q1: F1 schimbă
      coada pentru `clipper_preview`/`clipper_export`, care nu mai scriu `project.status`.
  - **B3r** e închis de revizie. Blocul `shadow_selection_v1` e în trace, cu comparație exactă și
    multiplicitate.
  - **A3** e acceptat în browser pe calea pozitivă (`A/A3-result.md`). Pe acel extras, v2 a ieșit
    identic cu legacy, fiindcă nu exista LLM.
  - **R5:** `job_queue.py` e spart în `job_rows.py` + `job_recovery.py`, numai prin mutări (793 → 494 de
    linii; 14/14 comparații AST identice; `B/R5-split-result.md`).
  - **Suita completă, rulată O dată, cu arborele înghețat:** 2792 passed, 2 deselected, exit 0; typecheck 0;
    eslint doar cu cele 2 erori preexistente (`A/full-suite/RESULT.md`).
  - **A4:**
    - `A/release-browser.md`: fluxul complet în UI (sursă locală → scor → editare → preview → export →
      rescore → anulare/retry) merge fără pierdere sau duplicare. Are două constatări: **D1**, UI-ul
      nu oferă descărcarea fișierului exportat (butonul cu iconița de download pornește randarea);
      **D2**, o alternativă exportată iese fără subtitrare și fără avertizare (nu are `caption_plan`).
      Ambele așteaptă decizia utilizatorului.
    - `A/b23c-watch/EVALUATION.md`: momentul cu ceasul, evaluat fără promovare. A pierdut doar la
      scorul euristic (57,6 față de 64,2), cu 0 judecați. Exportul lui așteaptă verdictul uman.
- **25 sept., noaptea — Codex pe contul nou; lucrul pe verdictele lui. Nimic comis după `2ed0e4b`:**
  - Istoricul Codex e exportat local, doar text, în `data/codex-export/`, cu brief-ul de pornire
    `CODEX-BRIEF.md`.
  - Verdictele noi: `codex-verdict-closure.md`, `-framing.md`, `-framing-2.md` și `-framing-3.md`.
    R5 și B3/B3r sunt închise.
  - **R4c** (`B/R4c-result.md`):
    - `render_record.ass_path` rămâne calea executată; `ass_published_path` e separat, legat prin hash;
    - `caption_corpus` ignoră un `.ass` vechi când nu a existat filtru;
    - `/export-file` dă 409 `export_not_current` dacă clipul nu e `exported`.
  - **D1 + O1** (`A/D1-result.md`): butoanele Render și Download sunt separate, iar panoul de progres al
    unui clip nu mai arată etapele analizei.
  - **D2** (`B/D2-result.md`): alternativele primesc planul de subtitrare construit la fiecare randare,
    fără să fie salvat; `caption_plan_state` apare pe sidecar, iar `unavailable` ajunge pe card.
  - Toate sunt verificate în browser și pe exporturi reale. **Integrare cu arborele înghețat: 2813
    passed** (`A/full-suite-2/`).
  - **Încadrare** (hardest-first), totul privat, fără cod de producție:
    - adevărul A pentru 70ca (v2, cu evenimente), c04e și 9d2a, plus un eșantion 6053, cu cadre de
      verificare rezervate și nevăzute (`A/framing-truth/RESULT.md`);
    - diagnozele de poartă B (`B/F-gates-result.md`);
    - **M0** (`B/F-M0-result.md`): YuNet pe sursă, ca seed pentru `_find_webcams`, îi recuperează pe Kai
      și pe Moist fără regiuni false. Codex l-a acceptat ca candidat de dezvoltare înghețat, nu ca
      integrare;
    - **decalaj proxy/sursă**: proxy-ul arată sursa cu −7 cadre (0,117 s) în urmă, din `-r 10` în
      `ingest.build_proxy`, reprodus sintetic (`A/proxy-offset/RESULT.md`). Lotul de validare a
      ceasurilor rulează la B (`F-offset`);
    - verificarea independentă M0 e fixată înaintea rezultatelor (`A/verification-lot/SPEC.md`, cu
      hash).
- **Rămâne (25 sept., noaptea) — înlocuit de secțiunea din 26 sept., de sus:**
  - Utilizatorul: verdictul editorial în `A/editorial-review.md`, inclusiv b23c. D1 și D2 sunt
    făcute, dar necomise.
  - Codex: închiderea R4b integrală, acceptarea D1/D2 și U1–U6 din `B/D2-result.md`
    (`codex-verdict-closure-2.md`).
  - Operațional:
    - repornirea AMBELOR backend-uri pe codul nou; migrarea adaugă `clips.export_job_id`;
    - apoi `server/scripts/release_stuck_exports.py`, întâi doar listare.
  - Încadrarea automată, cu pașii următori ai lui Codex: validarea ceasurilor (`F-offset`), apoi
    verificarea independentă M0 pe lotul fixat, apoi gate-ul pentru limitele regiunilor (`_snap_edge`,
    Kai îngust), apoi M3/M4/M1+M2. Umărul și apelul Discord de la 70ca se rezolvă împreună. Până
    atunci, editorul manual de reacție e calea utilizabilă.
  - Comparația v2 față de legacy cere un LLM (Ollama) și răspunsuri umane.
  - Mărunțișuri UI: O2–O4 din `A/release-browser.md` (O1 e rezolvat).
  - Fișiere la limita de 500 de linii: `clipper_render_plan.py`, `reasoning_trace.py`,
    `clipper_build.py`.

## 24 septembrie 2026, noaptea — rescorarea păstrează munca oamenilor (COMIS `f4c0e88`)

- **Plan:** `PRPs/clipper-rescore-keeps-human-edits-2026-09-24.md` (cu Closure). `_write_clips` ștergea
  orice clip neexportat la fiecare rescorare, iar orice editare a unui clip exportat îl coboară în
  `approved`. Se pierdeau astfel tăieturile, titlurile, subtitrarea, încadrarea de reacție desenată de
  mână, răspunsul „sursa are deja subtitrare” și aprobările/respingerile. Acum rămâne și orice clip
  cu un eveniment de muncă umană (`HUMAN_WORK_EVENTS`, origin `manual` sau NULL — rândurile vechi
  dinaintea coloanei), cu același id și toate câmpurile neatinse; momentul lui nu mai e repropus.
  Un singur predicat (`_kept_clips`) servește atât filtrul dinainte de dedupe, cât și `_write_clips`.
  Clipurile respinse rămân intenționat.
- **Auto-exportul** sare peste clipurile lucrate de oameni: au scorul rulării vechi și altfel ar
  lua locul alegerilor noi. E exact comportamentul de dinainte, testat și verificat prin mutație.
- **Cine:** Claude Code pe contul 2 (Opus 5.5), 32 de ture; auto-exportul — coordonatorul.
  Raport: `data/claude-rescore-edits/worker.md`. **Suita: 2502 passed, 2 deselected, exit 0**;
  fostul xfail al răspunsului per clip trece acum ca test normal.
- **Pe DB-ul real** schimbă azi un singur clip: `1a8a31dc9215` (verificarea UI de mai jos).
- Rămâne la fel ca la exporturi: revizia oarbă refuză cu 409 o tablă care amestecă rulări.

## 24 septembrie 2026, noaptea — subtitrarea dublă: răspunsul per clip (COMIS `37537b4`)

- **Plan:** `PRPs/clipper-clip-caption-source-2026-09-24.md` (cu secțiunea Closure). Pe materialul de
  reacție doar UNELE clipuri arată un video cu subtitrare arsă, deci setarea de proiect
  `source_has_burned_captions` (care nici nu avea UI) e greșită oricum ar fi pusă. Acum există o
  coloană nouă pe clip, cu același nume și trei valori; răspunsul clipului câștigă în fața proiectului
  (`caption_policy.decide(..., clip_setting=)`, cu `scope: "clip"`). Fără răspuns, decizia e identică
  byte cu byte cu cea de dinainte. Detectorul tot nu decide.
- **Endpoint:** `PUT /api/clipper/clips/{id}/caption-source` (`routers/clipper_caption_source.py`):
  strict (doar true/false/null), 409 în timpul exportului; invalidează randarea și scrie
  `caption_changed` doar la o schimbare reală. Un răspuns care PORNEȘTE stratul peste o încadrare de
  reacție fără bandă primește 422 cu `max_content_height` (folosește `caption_ready_height` din lotul
  caption-hint, deci acela se comite primul); unul acceptat salvează poziția rezolvată.
- **UI:** în editorul clipului, „Subtitrarea din sursă”: „Urmează proiectul” / „Arde subtitrarea
  ClipForge” / „Nu arde — sursa are deja subtitrare”, salvat imediat, blocat cât există modificări
  nesalvate. Nota de sub cadru spune acum „Added captions are off for this clip.”
- **Cine a lucrat:** backend + teste — Claude Code pe contul 2 (Sonnet 5); revizie independentă —
  Claude Code pe contul 2 (Opus 5.5). Revizia a găsit 9 teste stricate de o ajustare a coordonatorului
  (revenit la `getattr`), repornirea stratului peste o încadrare fără bandă (reparat) și pierderea
  răspunsului la rescorare (neremediat, vezi mai jos). Rapoarte: `data/claude-caption-source/`.
- **Verificat în aplicație** pe `1a8a31dc9215` (slice4h00test): cadrul fără / cu „CONTROL IT.”, diferența
  doar în dreptunghiul subtitrării; readus la NULL. Urme: 3 evenimente `caption_changed` (nu sunt
  etichete). DB-ul real are acum coloana, adăugată de `init_db` la pornire (NULL peste tot).
- **Suita: 2483 passed, 2 deselected, 1 xfailed, exit 0** (`data/claude-caption-source/full-suite.txt`).
- **Limită cunoscută:** o rescorare (`_write_clips`) șterge clipurile neexportate și le reinserează ca
  rânduri noi, deci răspunsul se pierde, la fel ca tăieturile, subtitrările și încadrările de reacție.
  E fixată ca `xfail(strict=True)`. **Rezolvată în lotul de mai sus** (rescorarea păstrează munca oamenilor).
- **Încă fără UI:** setarea la nivel de proiect (PATCH settings pornește o rescorare).

## 24 septembrie 2026, seara — mesajul de refuz al editorului de reacție (COMIS `08c6781`)

- **Plan:** `PRPs/clipper-reaction-caption-hint-2026-09-24.md`. Când salvarea unei încadrări e
  refuzată pentru că nu rămâne bandă pentru subtitrare, răspunsul 422 spune acum ce înălțime a
  materialului ar trece la lățimea aleasă (`max_content_height`, plus o propoziție în mesaj).
  Înălțimea e găsită prin căutare binară cu același constructor și același resolver ca salvarea,
  nu dintr-o formulă. `NoCaptionGap` e subclasă de `ValueError`, deci apelanții existenți nu se schimbă.
- Pe planul real al lui 70ca răspunsul e **802 px** (804 era refuzat), în ~0,36 s. 5 teste noi
  (limita exactă h / h+2, stilul în afara anvelopei nu e raportat ca lipsă de bandă, HTTP 422 → 200
  cu înălțimea sugerată, subtitrarea manuală neatinsă). **Suita: 2446 passed, 2 deselected, exit 0**
  (`data/claude-reaction-caption-hint/full-suite.txt`). Interfața nu s-a schimbat: afișează mesajul.

## 24 septembrie 2026 — planuri statice fără dimensiuni (COMIS `43aaff0`)

- **Plan:** `PRPs/clipper-legacy-layout-dimensions-2026-09-24.md`. `_plan_fits` refolosește un plan
  static doar cu `src_w`/`src_h` explicite, întregi valide (≥2, fără bool) și egale cu sursa;
  fallback-ul pe limite a fost eliminat, iar planurile vechi trec pe replanificarea existentă din
  `_layout_plan` (fără înmulțire cu 2,25, fără reștampilare). Reacția manuală rămâne pe validarea ei.
- **Dovezi:** 38 de teste independente (B) cu funcțiile reale; replanificare reală a 02dea/3e42 la
  1920×1080; proba statică arată remedierea în pixeli, cea dinamică e identică byte cu byte
  (`data/claude-legacy-layout/review/RESULT.md`). Testul de corpus v1/v2 validează fiecare export
  după schema declarată. `clipper_render_plan.py`: 499 linii. **Suita: 2441 passed, 2 deselected,
  exit 0.** Verdict: `data/claude-legacy-layout/VERDICT.md`.
- **Comis** cu acordul utilizatorului: `43aaff0`, exact cele 5 fișiere ale lotului.
- **02dea6f0a9e9 și 3e42c5a399c2 înlocuite** (acordul utilizatorului) de o instanță Claude Code pe al
  doilea cont (`CLAUDE_CONFIG_DIR=C:\Users\vlado\.claude-cont2`), prin API-ul editorului (PUT
  reaction-layout + export), cu încadrarea dea939. Ambele: 1080×1920, decodare curată; exact 6 fișiere
  schimbate din 395; exporturile vechi și rândurile din DB salvate în
  `data/claude-reaction-editor/<clip>/`. Raport: `data/claude-reaction-editor/slice4h-bad/REPLACE-RESULT.md`.
  Cele 3 exporturi afectate din `slice4h00test` sunt acum toate reparate.

## Stare NECOMISĂ, 23–24 septembrie 2026 — ceasul mișcării și geometria jocului

- **Corecția ceasului mișcării**: verificată matematic (teste time-local + mutații), dar
  respinsă editorial — schimbă ținta camerei de joc spre chat/ecrane (`data/claude-motion-clock/RESULT.md`).
  **Plan Codex 24 sept.** (`PRPs/clipper-framing-next-steps-2026-09-24.md`, Lot 1A): cele 4
  fișiere de cod restaurate exact la `29a4441`; `dynamic_timing.py` și `test_clipper_motion_clock.py`
  scoase din calea activă. Nu mai e activ în working tree. Arhivă completă (copii, patch,
  SHA256, HEAD): `data/claude-motion-clock/quarantine-20260924/`. Status și dosar în
  `PRPs/clipper-motion-clock.md`. Reintroducere = schimbare separată, cu aceleași probe (Lot 3).
- **Filtrul raport vârf/medie**: eșuat, în carantină, NU e activ (`data/claude-motion-filter/`).
- **Webcam-urile măsurate (`regions.webcams`) nu ajung în editorul dinamic**: banda vine din
  clusterele de fețe, dreptunghiul static de joc din „dreapta feței” și poate conține un webcam.
  `data/claude-game-region/DIAGNOSIS.md`.
- **Probă spatial_only** (privată): fereastră 9:16 fixă disjunctă de
  webcam-urile măsurate, fără tracking; alegerea camerelor, fețele și subtitrarea neschimbate.
  Un singur layout (3 clipuri, 2 proiecte), c04e `unavailable`.
  `data/claude-game-region/prototype/RESULT.md`.
- **Inspecție + alte 4 clipuri, 24 septembrie** (`data/claude-game-region/layouts/RESULT.md`):
  pe co-stream, Moist și Minecraft solo e mai bună sau neutră; **pe Speed/Discord (70ca) nu trece**:
  webcamul decupat depășește dreptunghiul măsurat (~735 vs 648 px), deci o fâșie intră în toate
  cele 7 shot-uri de joc, iar verificarea raportează „clear”. Webcam-urile măsurate nu sunt
  geometrie de pixeli (lui Kai lipsește pe 65a9). Integrarea în producție cere întâi o verificare
  pe pixeli, per clip, a extinderii webcam-ului.
- **Observație locală, Lot 2** (`data/claude-game-region/local-observation/RESULT.md`), privată:
  detectorul existent pe 40 de cadre din clip. Recuperează camera lui Kai pe 65a9, dar pierde un
  webcam real pe 9d2a și pe dea9; cauza nu e măsurată. Pe 70ca rămâne sub-delimitat: umărul lui
  Speed trece de marginea dreaptă (~648 px în sursă), la fel ca dreptunghiul larg, deci nici
  reuniunea nu rezolvă. **Niciun candidat pentru integrare.**
- **Lot 1B, validare Claude** (`data/claude-game-region/verification/CLAUDE-REVIEW.md`): auditul
  găsit avea 2/8 teste picate (fixture fără dimensiuni; necitibil raportat „changed”). Corectat
  numai în folderul privat: 11/11 teste; pe date reale exit 0, strict geometric, 8/8 SHA256.
- **Separare cameră / contur / conținut** (`data/claude-game-region/separation/RESULT.md`),
  privat: schimbare peste zgomotul propriu al intervalului (test binomial), legată de o propunere.
  Rezolvă umărul 70ca (739–740 px, stabil pe α); apelul e doar fezabil. Peste gameplay nu
  corectează nimic; pe 9d2a camera se lipește de video-ul urmărit. **Nu e candidat.** Notă: sursa
  randată a dea939 este 1920×1080 (`slice4h_hd.mp4`), nu 854×480 (`source.mp4` din Lot 2).
- **Editorul manual de reacție, folosit real din browser** (`data/claude-reaction-editor/RESULT.md`),
  la cererea utilizatorului, pe DB-ul real: 70ca (apelul Discord păstrat, fără fâșie din Speed) și
  9d2a (YouTube sus, Moist jos, fără subtitrare dublă) exportate ca fișiere NOI; dea939 ÎNLOCUIT cu
  acordul utilizatorului (vechiul păstrat în `dea939f254a1/previous-export/`). Dintre cele 390
  exporturi originale s-au schimbat doar cele 3 fișiere ale dea939. **Defect găsit în exporturi
  existente**: `layout_plan` static în coordonatele 854×480 ale `source.mp4`, aplicat pe sursa randată
  1920×1080 (`slice4h_hd.mp4`); planul nu-și notează dimensiunile. Audit pe cele 8 exporturi din
  `slice4h00test`: 3 afectate (dea939 reparat; **02dea6f0a9e9 și 3e42c5a399c2 rămân defecte**).
  Revenire exactă: `backup-before-edit.json`. Închide limita „UI neexersat în browser”.
- Teste pe arborele restaurat: **2.402 trecute, 2 TikTok deselectate**, exit 0
  (`data/claude-motion-clock/quarantine-20260924/RESULT.md`); 390/390 exporturi neschimbate.
  „2.424 trecute” este cifra istorică a lotului de ceas arhivat
  (`data/claude-game-region/prototype/full-suite.log`). Nimic comis.

## Actualizare 23 septembrie 2026 — continuitatea feței în goluri scurte

Codex a acceptat lotul limitat. Golurile curate după o detecție unică primesc
propuneri de mișcare separate (optical flow, buget de 2 s, necalibrat). Plannerul
le folosește doar când sămânța este exact o observație aleasă, aflată în camera
existentă. Datele brute, detecțiile pentru subtitrare și detectorul (Haar implicit)
rămân neschimbate. În cadrele verificate la 15,7, 16,1 și 16,6 s ale probei Speed,
fața rămâne în cadru. Costul: scară cu până la 40,6% mai mică și mai mult
joc/chat/UI. Al doilea Speed (`6914b77525e4`) și dialogul (`c04e7960179b`) sunt
identice byte cu byte. Rezultate: 2.402 teste trecute (2 TikTok excluse),
390 de fișiere de export existente neschimbate,
42/42 granițe identice, fără fit nou. Rămân deschise identitatea cu mai multe
persoane, golurile lungi, salturile de scară și tăierea din dialog la 3,133 s.
[Contract, probe și limite](../../../refs/clipper-face-continuity-2026-09-21.md).

## Actualizare 20 septembrie 2026 — protecția încadrării automate

Lot implementat împreună cu Claude Code, verificat separat în MP4-uri. Scara
încadrării folosește și înălțimea cutiei feței, fără să schimbe sensul observației
sau alegerea țintei. Pe shot-uri fără ancoră fixă, fereastra se poate lărgi pentru
observațiile locale al căror centru se află deja în camera aleasă; necunoașterea
geometriei rămâne refuz. Nu urmărește o identitate care a părăsit acea cameră.
[Contract, rezultate și limite](../../../refs/clipper-auto-framing-2026-09-20.md).

Șase perechi de exporturi pe patru clipuri: toate se decodează, 57/57 granițe
identice, fără fit nou, 390 de fișiere existente intacte. În Speed/YuNet sunt
păstrate capul la 8 s și persoana la 16,6 s; în Minecraft se repară capul tăiat.
Costul este mai mult fundal/HUD și persoana mai mică. Haar încă pierde fața
la finalul Speed; dialogul încă taie un participant și câștigă un salt de scară.
Evitarea fețelor nu evită automat scrisul din sursă. **2.304 teste trecute,
2 TikTok excluse**, apoi 111 focalizate după separarea fișierului de teste.

Detectorul implicit rămâne Haar; YuNet rămâne opțional. Pasul următor este
continuitatea țintei și tratarea golurilor de detecție, cu aceleași probe/control,
nu activarea generală a YuNet pe baza numărului de detecții. Selecția regiunilor
de reacție rămâne manuală. Nu este un verdict de publicare pentru întregul lot.

## Actualizare 20 septembrie 2026 — subtitrarea reacției folosește banda liberă

Încadrarea explicită de reacție rezervă temporar și imaginea materialului urmărit
pentru poziționarea automată a subtitrării. Folosește geometria rendererului și
refuză poziționarea automată dacă anvelopa textului nu încape; poziția manuală
rămâne a utilizatorului. Selecțiile salvate și geometria lor nu sunt rescrise.
Politica de suprimare se aplică înainte de poziționare, iar textele eliminate
sau din afara ferestrei nu cer spațiu. Claude Code a implementat lotul; verificarea
independentă a corectat inclusiv amestecarea ceasului original cu cel scurtat.
[Contract, probe și limite](../../../refs/clipper-reaction-caption-gap-2026-09-19.md).
Aceasta nu este detecție OCR și nu rezolvă alegerea automată a celor două regiuni.
Anvelopa rămâne aproximativă; stilurile mari/animate cer poziționare manuală.
Proba finală: `data/claude-xqc-reference/caption_gap_20260920_005030_790630/`.
Text pe unul/două rânduri în banda 904..1152px; imaginile rezervate rămân identice
cu controlul fără text în cele două cadre măsurate. MP4 decodat integral,
390 de fișiere existente neschimbate. **2.244 teste trecute, 2 TikTok excluse**;
Claude a verificat separat cele 43 de teste noi și nu a găsit alte probleme.
Nu a fost raportată epuizarea cotei Claude; verificarea finală s-a încheiat normal.

## Actualizare 19 septembrie 2026 — încadrarea de reacție ajunge în editor/export

Cu Claude Code la backend și Codex la interfață/verificare, editorul permite
alegerea manuală a materialului urmărit și a reacției pe cadrul original.
Planul este legat de sursă și interval; prevalează asupra montajului dinamic,
iar o sursă schimbată sau un interval extins îl refuză. Revenirea la automat
rămâne disponibilă. Editorul preia după salvare obiectul confirmat de server.

Proba prin API și `handle_export`, într-o bază separată, produce MP4 identic
la nivel de octeți cu D; cele 390 de fișiere existente sunt intacte. Captionul
de diagnostic este desenat deasupra reacției, dar poate acoperi scrisul din
materialul urmărit: aceasta rămâne o limită, nu un verdict de publicare.
[Contract, artefacte și limite](../../../refs/clipper-reaction-editor-2026-09-19.md).
Interacțiunea browserului nu a fost verificată vizual; build-ul și geometria
frontendului sunt verificate separat de probele backendului. Alegerea regiunilor
este manuală, nu un detector automat pentru tot live-ul. Claude nu a raportat
epuizarea cotei; opririle intermediare au fost limitele de ture ale sarcinii.
Verificare finală: **2.201 teste Python trecute, 2 TikTok excluse**, plus 3 teste
Node, TypeScript, lint pe fișierele noi și build de producție. Nicio activare
globală a detectorului sau schimbare de politică de captions pe proiect.

## Actualizare 13 septembrie 2026 — sursa locală și trei probe reale

Sursa indicată de utilizator în `2c8af11153a3/source/source.mp4` a fost citită.
Claude Code a pregătit probele, iar Codex a verificat trei exporturi reale de
12 secunde prin calea comună. Cropul îngust taie „Boogah” și numeralul;
varianta largă le păstrează la 8,5 s, dar aduce browserul în imagine. Toate se
decodează, au sidecar v2 verificat și lasă 390/390 fișiere de export intacte.
[Probe, corecții și limite](../../../refs/clipper-reaction-local-2026-09-13.md).
Fit-ul explicit din `PRPs/clipper-reaction-panel-fit.md` este acum implementat
și randat: `data/claude-xqc-reference/panel_fit_20260913_214419_372541/reaction_content_fit.mp4`.
24 cadre noi inspectate: fără barele browserului, „Boogah”/numeral păstrate;
rămân pierderea fâșiei stângi și costul blurului. Audio codat identic cu C.
2.138 teste trecute, 2 TikTok excluse; 210 filtre legacy identice cu `bb99f81`.
Versiune statică `render_static_split_v3_reaction_fit`; modurile existente
păstrează geometria. Nu este încă o opțiune automată activată în aplicație.
Urmează legarea observațiilor locale și a intervalelor potrivite la exportul
obișnuit, nu aplicarea acelorași dreptunghiuri întregului live.
Nu s-a identificat episodul din Short și nu s-a ascultat audio-ul referinței.

## Actualizare 13 septembrie 2026 — acces la referința de montaj xQc

La cererea utilizatorului, Claude Code a încercat accesul public la referința
`T1jsllUvrHA`. A obținut metadate și miniaturi, dar nu a vizionat video-ul și nu
a ascultat sunetul. Codex a inspectat aceeași miniatură: material sus, reacție
jos, text alb în imaginea de sus. Acestea sunt observații despre miniatură;
nu stabilesc tăieturile, proporțiile exportului sau un tratament de randare.
Codex a respins specificația dedusă de Claude din ea, inclusiv presupusa cerință
de a păstra barele browserului. Claude a corectat raportul, verificat de Codex:
`data/claude-xqc-reference/RESULT.md`; originalul respins este păstrat separat.
Nu există un proiect local pentru URL-ul exact
(căutare în DB doar în citire). Analiza montajului așteaptă acces la video-ul
efectiv. Controlul Chrome a fost oprit de instrument din cauza imposibilității
de a verifica URL-ul; nu se ocolește această restricție.

## Actualizare 12 septembrie 2026 — B1 verificat, detector nou încă neactivat

Codex a verificat lotul B1 implementat cu Claude Code (adaptor YuNet și comparație
pe cadre), inclusiv corecțiile raportării și probele independente prin main().
Suita completă: 2.053 teste trecute, aceleași două eșecuri TikTok 404 cunoscute
(excluderea a folosit prefixul `server/`, dar nodurile pornesc din `tests/`,
conform colectării pytest; deci cele două teste au rulat).
Predicțiile pe 40 de cadre coincid cu apelurile OpenCV salvate independent;
raportul păstrează un cadru incert, exit 2. Cele 390 de fișiere din exports sunt
neschimbate față de inventarul de la începutul acestui lot.
Detectorul din aplicație rămâne cel din lotul A.
Probe vizuale înghețate: 40 de cadre inițiale și 20 de confirmare la alte momente.
[Rezultatele și limitele lotului B](../../../refs/clipper-face-detector-b-2026-09-12.md).
B1 este comis la `30e4247`. B2 este verificat: profil explicit 2x/.75 și aceeași
cale de eșantionare; 2.096 teste trecute, cele două TikTok excluse corect.
Proba Speed a respins ACTIVAREA DIRECTĂ: 72/72 observații cu fețe, dar zoom
mai mare și text peste față. La 8s este tăiat și capul. Ambele MP4 se decodează
integral; 390/390 fișiere existente sunt intacte. Probele sunt în
`data/claude-face-detection/batch-b/speed-probe-20260912-195851-910623/`.
Următoarea corecție de produs separă geometria încadrării de cutia detectorului;
nu se activează YuNet implicit pe baza numărului de detecții. Claude B2 a încheiat
fără epuizarea cotei (sesiunea `40f2f95e-4773-434d-bc5b-7abb4dce09c9`).

Ordinea cerută de utilizator: se termină întâi partea de detecție începută,
folosind Claude Code; apoi se analizează modelul de montaj pentru live-ul xQc:
https://youtube.com/shorts/T1jsllUvrHA?is=Xfai9xR99IRBuIN-
Starea accesului la referință este în actualizarea din 13 septembrie de mai sus.
La epuizarea cotei Claude se salvează
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
