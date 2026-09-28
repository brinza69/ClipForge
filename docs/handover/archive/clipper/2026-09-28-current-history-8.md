# Arhivă Clipper — 27 sept.: FD1, R1c, C1r, AQ1, AD3/AD3C, EN3, BURST1, UI, SC lot 1 și loturile pe contul 2

Mutat din CURRENT la 28 septembrie 2026, ~18:30, pentru limita de 500 de linii. Istoric, inclusiv
afirmații ulterior retrase; nu este verdictul stării actuale. Conținutul secțiunilor este păstrat.

## 27 septembrie 2026, ~23:15 — FD1, R1c, C1r, AQ1 gata în worktree-uri; nimic comis; EN3V2 pe contul 2
- **Sesiunea s-a întrerupt între 20:56 și 22:20** și a oprit rulările contului 2 (`exit=127`). Ele au fost
  relansate la 22:22 prin WMI (`Win32_Process.Create`), în afara arborelui de procese al sesiunii: OW1r și EN3V2r
  (`B/*r.prompt.md`).
- **FD1** (`F:\ClipForge-wt-fd1`, `A/fd1/FD1-result.md`): clasificatoare Haar per thread.
  - Codex next-26 §1: **acceptat, commit separat**.
  - Suita: 3447 passed. ND0 reluat cu instrumentul corectat: 6/6 thread-uri identice.
- **BURST R1c** (`F:\ClipForge-wt-burst2`, manifest `A/burst-ui/burst-manifest-r1c.txt`, 27 de fișiere):
  - identitatea încercării e luată la claim (`UPDATE … RETURNING`), din contextul task-ului
    (`job_attempt.CLAIMED_ATTEMPT`); tranzițiile finale țin cont de încercare;
  - suita: 3532 passed; mutații 8/8;
  - Codex next-26 §2: acceptat ca lot distinct, ordinea BURST → UI → SCB2.
  - **Smoke real făcut** pe 8445/3045 (rădăcina EN3V on): două preview-uri reale, cu calea, record-ul și rândul
    concordante; `/preview-file` = fișierul selectat; redarea merge.
- **AQ1** (`F:\ClipForge-wt-aq1`, peste R1c, `A/aq1/AQ1-result.md`): coada ține cont de încercare peste tot
  (progres, heartbeat, requeue, evidența task-urilor, cancel).
  - Teste cu două încercări reale suprapuse; mutații 9/10, supraviețuitorul e descris.
  - Suita rulează. **Backend-ul nu se promovează înainte de AQ1** (next-26).
- **CC1C12 + C1r** (`F:\ClipForge-wt-cc1`): identitatea decodorului trebuie să fie completă; cititorul de panouri e
  validat separat.
  - Suita: 3532 passed; mutații C1r 4/4.
- **EN3V2** (contul 2): copie nouă de board, cu FD1 în cod, exporturi off/on, replay controlat și fragmente pentru
  telefon.
  - Pagina e pregătită (`A/en3v2-page/proba-finalurilor.html`, cu răspunsuri în `db`). Merge la om după verificarea
    lui A (next-26 §4).
- **Commit-urile așteaptă acordul utilizatorului:** FD1; BURST (+R1c); UI; apoi SCB2 rebazat; AQ1; CC1+C1r; OW1.

## 27 septembrie 2026, ~13:45 — AD3C și EN3 comise; LIM1 și CC1A la Codex; proba EN3 pe contul 2

- **Commit-uri noi** (cu acordul utilizatorului):
  - `a73e589` AD3C — adresarea 3b e opt-in (`clipper_scene_addressing`, implicit False). Codex next-17 §1.
  - `3229423` **EN3** — coada după ultimul cuvânt: `end_tail.extend_tail`, apelat în `refine_boundaries` imediat
    după `settle_end`.
    - `clipper_end_tail_s` = ținta după capătul vocal, nu un adaos peste EN1. **Implicit 0.0 = oprit.**
    - Se aplică sub gărzile EN1: vorbirea următoare − 0,05 s, veto-ul `onset_in`, limita și audio-ul.
    - Refuzul e înregistrat în `end_evidence.tail`.
    - Dovada: runda 3 oarbă (`A/editorial-review/tail-probe-3-answers-2026-09-27.txt`) → Codex next-18.
    - Suita pe main: **3445 passed**.
- **Backend-ul A (8420) rulează pe `3229423`**, cu ambele flag-uri oprite. Backup:
  `data/db/backup-2026-09-27-pre-en3-restart/` (API-ul sqlite backup, integrity ok). Smoke: health ok, 18 proiecte,
  0 joburi, fără erori în log.
  - UI-ul de pe 3000 nu rulează. Nu e efectul repornirii.
- **BURST1 NU e pe main.** L-am scos înainte de repornirea AD3C, cum a avertizat Codex. Codex next-17 §2 cere
  R1/R2 (pragul de 11 ms împrumută timp, iar raportul nu descrie ASS-ul scris); BURST1r le corectează pe contul 2.
  - Omul a preferat varianta nouă pe 1da8 și b365; controalele au ieșit „la fel”
    (`A/editorial-review/burst-answers-2026-09-27.txt`).
  - Ordinea din next-17 §3 rămâne: R1/R2 → limita în UI → pilotul de realiniere la audio.
- **Livrate de contul 2 și trimise la Codex** (`data/claude-to-codex/2026-09-27-lim1-cc1a.md` →
  `codex-verdict-next-19.md`):
  - **LIM1** (`B/LIM1-result.md`), poarta limitelor pentru M0 v2:
    - A a verificat: 16/16 ieșiri, specificația și adevărul R2 neschimbate.
    - Rezultate: 0 regiuni false. Pe R2 (Moist, două persoane), candidatul omite 34–38 % din cameră, față de 4 %
      la Haar. Corpul e tăiat în banda livrată. Costul e de ×12–23 față de Haar.
    - Concluzia lui B: cel mult seed limitat pentru layout-ul static. Adevărul R2 e o singură adnotare, fără QA.
  - **CC1A** (`B/clock-composition-addendum.md`):
    - legătura fereastră → proxy prin `-stats_enc_pre` pe același encode, încă nedovedită (proba P0);
    - puncte, span-uri și agregate, fără conversie între ele; audio-ul nu primește offset-ul video;
    - keyword-ul `address=False`, deci implicit costul e zero.
- **Rulează pe contul 2:**
  - SCB2c (SC lotul 2);
  - BURST1r;
  - **AD3H**, proba pe handler cu 3b pornit. **Constatare parțială:** un thread `build_signals` al unei încercări
    anulate a rescris `signals.json` și `faces.json` la 7,5 min după anulare. Verificarea „anulare fără publicare”
    pică pe instanța izolată. Aștept raportul;
  - **EN3V**, proba de activare la 0,4 (next-18 §2):
    - instanță izolată pe 8434, cu copia DB-ului prin backup API și copii ale proiectelor;
    - cazurile f81b, ee0e, 2c8a, slice4h și controlul refuzat 39c8 458,1;
    - scorare → export reală, perechi oprit/pornit, pagină oarbă pentru om.
- **Nimic nu e activat pe 8420.** Activarea EN3 la 0,4 se decide abia după vizionarea exporturilor.

### ~16:20 — contul 2 la limită (A a făcut munca lui B); cont 1 la 95% (5 h)
- **Verdicte:** next-21 (AD3H/OW1), next-22 (SCB2/C0/camera-review), next-23 (BURST R1–R3, EN3V), next-24
  (OW1 aprobat cu 5 amendamente; SCB2r: preview-ul VIDEO ocolește tratamentul; BURST R1: rename netranzacțional).
- **BURST** (`F:\ClipForge-wt-burst2`, pe `3229423`), făcut de A:
  - R1–R3 + **R1b**: preview publicat la cale imuabilă pe încercare; `clips.preview_record` (JSON:
    job/attempt/worker/raport) selectat în aceeași tranzacție cu `preview_path`; `capture_attempt` și verificarea
    proprietarului la publicare;
  - `workers/clipper_preview_publish.py` e nou; manifestul e `A/burst-ui/burst-manifest-r1b.txt`;
  - mutații 8/8 + 9/9;
  - suita: la prima rulare R1b, 3 teste existente fără job preluat au picat (reparate prin fixture, 41 passed); a
    doua suită (`A/burst-ui/r1b-suite2.txt`): **3524 passed, exit 0**;
  - contractul e în `A/burst-ui/CONTRACT.md`.
- **UI** (`F:\ClipForge-wt-ui2`): nota de subtitrare, acceptată de Codex la nivel de cod; browserul pe cazurile
  schimbate e făcut (`claude-to-codex/2026-09-27-burstr-browser.md`); manifestul e `A/burst-ui/ui-manifest-r.txt`.
- **SCB2r** (`F:\ClipForge-wt-sc3`): editorul coroborează, V3, `edit_quality` împărțit (suita 3571). Rămâne
  **poarta pe preview-ul VIDEO** (next-24 §2), de făcut DUPĂ commit-ul BURST, pe `handle_preview` nou.
- **OW1:** addendum aprobat (`A/ow1/OW1-addendum.md` + amendamentele din next-24 §1). **Neimplementat.**
- **ND0** (`A/nd0/ND0-result.md`): exporturile nu diferă în procese separate. Diferă când două analize rulează
  simultan în ACELAȘI proces: `face_cascades()` e globală, partajată, iar inițializarea ei publică lista goală.
  Fix propus FD1, neaplicat.
- **Mesaj pentru Codex, netrimis încă** (utilizatorul se juca): `claude-to-codex/2026-09-27-r1b-nd0-browser.md` →
  next-25.
- **Ordinea după next-25:** commit BURST (acordul utilizatorului) → UI → rebazare SCB2 + poarta video → FD1 → OW1
  (implementare) → C1/C2 → EN3V (copie nouă de board, exporturi, `tail` în sidecar).

### ~14:05 — verdictele next-19/20 și lucrul pornit după ele
- **M0 v2 NU se integrează ca seed** (next-19 §1). Rămâne candidat diagnostic înghețat.
  - **LIM1r:** adevărul are trei stări, iar lipsa adevărului = `unavailable`. „Zero false peste tot” e retras.
    Rezultatul, `B/LIM1r-result.md`, e verificat de A.
  - **Citirea independentă a lui A** (`A/r2-read/R2-READ.md`, acceptată în next-20 §3): marginile ±1 px,
    creștetele ≤ 5 px; incertitudinea declarată rămâne. Tăieturile de pe 172/173 și 176–178 se confirmă.
  - **Martorul Jynxzi 4800** (`A/r2-read/JYNX4800-WITNESS.md`): persoană cheiată, localizată pe 24–30/37 cadre, dar
    zero regiuni. Poarta care refuză nu e măsurată.
- **CC1:** C0 aprobat (next-19 §2), doar legătura prin stats; probele P0/P1/P2 rulează pe contul 2 (`CC1C0`).
- **BURST1r:**
  - R1 e reparat, iar verificarea independentă a lui A (`A/burst1r-check/check.py`, 400 de planuri) a ieșit 0 eșecuri;
  - next-20 §1 cere o ultimă corectură: toate cardurile `no_time` din builder ieșeau înaintea raportului;
  - **BURST1r2** o face în `F:\ClipForge-wt-burst2` (pe `3229423`, cu cele 7 fișiere BURST1r) și livrează contractul
    backend pentru avertismentul din UI (`B/BURST-UI-contract.md`);
  - **UI-ul îl face A**, după contract. B deține `server/`, A deține `src/`.
- **AD3H PICĂ „nicio publicare după anulare/preluare”.** `build_signals` scrie `signals.json`/`faces.json` din
  thread-ul executor, care nu poate fi anulat. La 7,5–8 min după anulare a suprascris chiar o analiză completă. Bug-ul
  e mai vechi decât AD3. Propunerea OW1 e la Codex (next-21): handler-ul publică după gardă, stop cooperativ, audit.
  **3b rămâne oprit; niciun consumator al lui `scenes_addressed`.**
- **La om:** `A/editorial-review/camera-review.html` (R2 în secvență, Jynxzi 100:00–100:05, Speed/Kai), cu 5
  întrebări neutre.

## 27 septembrie 2026, ~11:50 — UI comis; AD3C și BURST1 integrate (necomise); pauza pe conturi

- **Commit-uri azi** (toate cu acordul utilizatorului):
  - `be618b1` AD3 3a+3b;
  - `31972e2` nucleul SC (lotul 1);
  - `78b9fae` lotul UI O2–O4 + cardul „Ready”. Codex next-15 a aprobat integrarea după corecturile R1/R2. Suita:
    3406 passed.
- **Backend-ul A (8420) rulează pe `78b9fae`.** Backup DB: `data/db/backup-2026-09-27-pre-ui-restart/`. Smoke:
  908 carduri cu `from_current_run`/`last_export`, `max_clip_s_effective` = 90.
- **Pilotul AD3 (3c) e închis** de Codex next-16. Corecția e adăugată în `B/AD3P-result.md` (1810 intervale în
  afara setului curat). e846 are două schimbări reale, câte una pe interval.
- **Integrate pe main, NECOMISE, în așteptarea Codex next-17 și a acordului utilizatorului.** Suita comună: 3436
  passed.
  - **AD3C** (`A/ad3c-manifest.txt`): adresarea 3b e opt-in, prin `settings.clipper_scene_addressing` (implicit
    False) și starea `not_requested`. Cele două fișiere de teste AD3 existente primesc o fixture care pornește
    flag-ul.
  - **BURST1** (`B/BURST1-result.md` §10; worktree `F:\ClipForge-wt-burst`):
    - `caption_display.py` + builder, writer și raport;
    - suprapunerile scad de la 65 de fișiere la 0 pe cele 85 arse;
    - rândurile hărții BURST1 se inserează ABIA la commit-ul lui. Harta de pe main are acum doar schimbările AD3C.
  - Commit-uri separate: întâi AD3C (fișierele lui + harta), apoi BURST1 (fișierele lui + rândurile lui), cu o
    singură repornire la final.
- **Pauza:**
  - contul 2 a atins limita de sesiune la ~11:30. La 14:05, `B/relaunch-1405.sh` reia SCB2c și LIM1c de la starea
    de pe disc, iar la 14:10 `B/launch-CC1A.sh` pornește addendum-ul pentru compunerea ceasurilor;
  - Codex a atins limita (resetare la 14:10). Mesajul gata de trimis:
    `data/claude-to-codex/2026-09-27-ad3c-burst1.md` → `codex-verdict-next-17.md`.
- **La om:**
  - `A/editorial-review/tail-probe-3.html` (runda 3 a cozii, 8 perechi);
  - `A/editorial-review/burst-compare.html` (vechi/nou pentru rafale).
  Răspunsurile se salvează în `A/editorial-review/answers/`.
- **Următorii pași, în ordinea next-16:**
  - proba pe handler-ul real pentru 3b (HTTP și heartbeat DB în timpul adresării, anulare, pierderea proprietății),
    pe o instanță izolată cu flag-ul pornit;
  - compunerea ceasurilor (CC1A → Codex → cod);
  - M0 diagnostic + LIM1 + exporturi pereche;
  - consumatorii, câte unul;
  - SC: după SCB2, lotul 4 de acceptare cu măștile aprobate (6053 `6677ec49…`, b23c `bf74839b…`) și verificările din
    next-15 §2;
  - worktree-urile comise se pot șterge, cu acord: `wt-addressing`, `wt-endings`, `wt-ad3`, `wt-sc` și `wt-ui`
    (ultimele două după commit-urile lor).

## 27 septembrie 2026, dimineața — AD3 și SC lot 1 comise; probele cozii; lotul UI; patru loturi pe contul 2

- **Commit-uri, cu acordul utilizatorului:**
  - `be618b1` AD3 3a+3b (`scenes_addressed`), cu backend-ul repornit;
  - `31972e2` SC lot 1, nucleul `source_treatment*`. E neconectat, deci nu cere repornire. Codex a acceptat
    R1/R2/R3 (next-13/14). Suita integrată: 3396 passed.
- **dea939** a fost re-exportat pe fereastra aleasă de om (`A/dea939-window/NOTE.md`). Primul export a fost
  refuzat corect de legarea reacției la fereastra veche.
- **Coada după ultimul cuvânt** (EN1 rămâne neschimbat):
  - runda 1 (`A/editorial-review/tail-probe-answers-2026-09-27.txt`) are controlul reetichetat: era capătul
    ferestrei stocate, nu EN1;
  - runda 2 (`tail-probe-2-answers-…`): candidatul +0,4 s ales în 4 din 5 perechi reale, dar controlul nul a
    eșuat. Butonul „Final” pornea variantele din puncte diferite ale sursei, lucru găsit de Codex;
  - runda 3 (`A/editorial-review/tail-probe-3.html`, 8 perechi: 4 noi, 2 repetări inversate, 2 nule) e la om.
    Butonul e reparat și verificat pe toate cele 16 fișiere.
  - Codex next-14: dacă preferința rezistă, urmează un EN3 limitat și reversibil; dacă nu, EN1 rămâne implicit.
- **SC lot 0:**
  - citirea lui A e în `A/source-captions/whole-reading-v2.json`;
  - A a văzut toate cele 505 cadre semnalate de SC0 și toate arată starea citită (`sc0-resolution.json`);
  - SC0r: măștile sunt canonice (un LF), cu rezolvarea legată. Validatorul trece, dar doar ca validator, și
    loader-ul le acceptă. Acceptarea e la Codex (next-15).
- **Lotul UI O2–O4 + cardul „Ready”** (A, `F:\ClipForge-wt-ui`, `A/ui-o2o4/manifest.txt`):
  - „kept” pentru clipurile din rulări vechi;
  - „export cancelled”;
  - refuzul intervalului peste maxim, cu scurtarea permisă;
  - „default” în loc de „undefined”.
  E verificat în browser pe sonda 8431 și UI-ul 3001. Așteaptă Codex (next-15).
- **Pe contul 2, în paralel:**
  - AD3P, pilotul 3c (`B/AD3P.prompt.md`);
  - SCB2, lotul 2 SC (executor + poartă pe toate căile, `F:\ClipForge-wt-sc2`);
  - BURST1, rafalele de subtitrare (`F:\ClipForge-wt-burst`);
  - LIM1, gate-ul limitelor M0 (`B/gates/lim/`).

## 27 septembrie 2026, ~00:20 — starea la oprirea PC-ului

- **AD3 comis `be618b1`** (6 fișiere, suita 3139 passed). Backend-ul A a fost repornit pe el, cu backup DB în
  `data/db/backup-2026-09-26-pre-ad3-restart/`. Pilotul 3c NU a rulat; cerințele sunt în `codex-verdict-next-12.md` §3.
- **dea939 re-exportat pe fereastra aleasă de om** [13044.80, 13083.604], cu acordul lui explicit. Pașii, backup-ul
  exportului vechi și hash-urile sunt în `A/dea939-window/NOTE.md`. Omul trebuie să vadă exportul vertical.
- **Proba cozii**: `A/editorial-review/tail-probe-answers-2026-09-27.txt`. Finalul actual n-a fost ales niciodată;
  4 din 5 alegeri decise au fost cozi mai lungi (de obicei +0,4 s). Rezultatul încă nu e trimis la Codex. Nu se
  aplică padding universal fără verdict (next-11 §1).
- **Contul 2:**
  - SCB1 (nucleul SC, în `F:\ClipForge-wt-sc`) e terminat: `B/SCB1-result.md`. Urmează verificarea cu
    `A/verify_b_delivery.py`, apoi Codex;
  - SC0 a fost tăiat de oprire. Se relansează cu `B/relaunch-sc0.sh` (detached, prin Start-Process bash).
- Worktree-uri: `wt-ad3` (comis, se poate șterge), `wt-sc` (SCB1, necomis), `wt-addressing` și `wt-endings`
  (comise, se pot șterge).

