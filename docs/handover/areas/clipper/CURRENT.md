# Handover — AI Stream Clipper

## 28 septembrie 2026, ~17:00 — SC3 închis de Codex (next-35); acceptarea prin produs confirmată de om
- **SC3** (`F:\ClipForge-wt-sc3api`, 29 de fișiere, `A/sc3/sc3-files.txt`):
  - blur per clip cu mască validată offline, numai pe calea dinamică; erase neoferit;
  - `PUT/GET /clips/{id}/source-treatment` și UI (Fără tratament / Blur + stratul pe 3 stări);
  - publicarea normală numai blur, prin R4b;
  - măștile se importă cu `server/scripts/import_source_caption_mask.py`, câte un director pe mască.
  - next-34 a cerut R1–R4, toate făcute; suita finală: **3995 passed** (`A/sc3/sc3-final-suite.txt`).
- **Acceptarea** s-a făcut pe o instanță izolată (8446/3046, rădăcina `F:\ClipForge-tmp\sc3root`):
  - parcursul: UI → PUT → reload → handler normal → download;
  - 6053 (blur + strat nou) = `bd021797…`, byte cu byte egal cu proba; b23c (blur uniform) = `ca098691…`;
  - omul: „sunt bune” (`A/sc3/acceptance/`, cu sidecar-urile finale).
- **Rămâne:** commit-ul SC3, cu acordul omului, repornirea 8420 (EN3/3b oprite) și importul offline al celor
  două măști în proiectul real; plus **RSK** (B, contul 2): exporturile finale R2 și Speed/Kai care păstrează
  persoanele (next-33 §2), ultimul criteriu de închidere.
- Contul 2 a fost indisponibil între ~10:40 și ~16:30; A a lucrat singur, cu Codex.


## 28 septembrie 2026, ~13:00 — harta împărțită; planul de închidere (next-33)
- `190af2e` docs: `docs/clipper-map.md` e acum un index, iar tabelele sunt în `docs/clipper-map/*.md`, toate sub 500
  de linii. Scriptul `A/mapsplit/split_map.py` a verificat că fiecare dintre cele 446 de rânduri apare exact o dată.
- Commit-urile de azi au fost **reformulate local** (lipsea rândul gol după titlu). Arborii sunt identici, nimic nu
  era împins: `1b060b8`→`47c5cbd`, `53f2cb1`→`2994b67`, `85e2524`→`009a135`, `909e3a8`→`190af2e`.
- **next-33:** blur e direcția de livrare, erase se amână. Ordinea:
  1. harta (făcut);
  2. **SC3 backend**: PUT per clip `/api/clipper/clips/{id}/source-treatment` {`source_caption_treatment`,
     `caption_layer`}; publicarea normală numai pentru blur, prin staging-ul R4b; invalidarea; cele 3 căi de randare;
  3. SC3 UI + măștile disponibile + actualizarea cardului;
  4. acceptarea prin produs (UI → PUT → reload → handler → MP4 descărcat) pe 6053, b23c (blur uniform, fără erase
     la F5) și un clip fără mască; apoi suita, commit-urile, restartul.
  - R2 și Speed/Kai: un export final verificat care păstrează persoanele. CC1 C3, automatizarea măștilor, erase și
    EN3/3b NU blochează.

## 28 septembrie 2026, ~11:30 — OW1r2 + EN3T + reluarea comise; 8420 pornit pe `009a135`
- **Commit-uri** (acordul utilizatorului; Codex next-30 §1–2 și next-31 §1): `47c5cbd` OW1r2 (33 de fișiere) →
  `2994b67` EN3T (7) → `009a135` reluarea EN3T §3 (3).
  - Fișierele au fost puse pe stări intermediare, prin `A/int1/stage_commit.py` + `A/int1/INT1-commits.md`
    (sha verificat pe fiecare).
  - Arborele final e egal cu INT1 pe toate cele 40 de căi. Suita finală INT1: 3904 passed.
- **8420** (preview `clipforge-backend-a`):
  - backup: `data/db/backup-2026-09-28-pre-int1/` (integritate ok);
  - migrarea `projects.analysis_generation` e aplicată; `/api/ready` = ready; 0 joburi;
  - EN3 (`clipper_end_tail_s` = 0.0) și 3b (`clipper_scene_addressing` = False) sunt la valorile implicite: fără
    `.env`, fără variabile `CLIPFORGE_*` în mediu, iar launch.json nu le setează.
- **Incident nereprodus** (next-31 §2): 2 eșecuri `test_readiness` (`directories` False) la prima suită INT1
  (`A/int1-suite.txt`), cu C: aproape plin.
  - Ipoteza e Storage Sense, activ „când spațiul e redus”; nu e dovedită.
  - Pe F:, cu detector la fiecare teardown, nicio dispariție.
  - Dacă reapare, se înregistrează la apelul readiness căile verificate și rezultatul fiecăreia. Produsul nu se
    schimbă pentru o cauză presupusă.
  - Temporarele testelor rămân pe F:.
- **C:** a fost curățat cu acordul utilizatorului (cache pip + Temp mai vechi de 2 zile): de la 1,1 la ~10 GB
  liberi.
- **SC4Pv2 randat** (A, `A/sc4v2/`), în rădăcina izolată SC4P:
  - pe fiecare clip: controlul, erase, blur și erase/blur + stratul nou (`render-layer`, cu înlocuitorul
    `source_has_burned_captions = False` în memorie, în locul lotului 3);
  - toate 8 variantele tratate sunt coroborate.
  - Driverul iese acum cu codul verdictului: 5 teste prin punctul de intrare.
  - Foile de dovezi sunt în `A/sc4v2/sheets/`.
    - Prima serie era cu un cadru în urmă (`-ss t` = primul cadru cu pts ≥ t); e corectată.
    - k6422 rămâne netratat; comutarea 7157/7158 e corectă în ambele baze.
    - **La k7243, erase deteriorează cureaua albă**; blur lasă o bandă vizibilă.
  - **La om:** `http://localhost:8779/compara.html` (preview `clipforge-sc4v2`), folderul `A/sc4v2/de-vizionat/`.
  - Trimis la Codex la ~12:12: `claude-to-codex/2026-09-28-sc4v2.md` → next-32.
  - **next-32:** proba e acceptată pentru vizionare; deteriorarea de la k7243 e confirmată, fără split nou obligatoriu.
    Înlocuitorul stratului e acceptat numai pentru proba izolată. Corecții făcute:
    - foile etichetează cadrul DECODAT (pts/ordinal) și pică la o variantă lipsă (2 teste);
    - `PROBE-MANIFEST.json` declară substituția, domeniul și baza nealeasă;
    - textul paginii e corectat.
    - Addendum-ul `claude-to-codex/2026-09-28-sc4v2-addendum.md` pleacă odată cu răspunsurile omului.
  - Alegerea omului poate selecta aspectul, dar nu validează lotul 3. Un amestec erase/blur local (de exemplu blur
    doar la k7243) cere un interval nou pregătit și autorizat.

## 28 septembrie 2026, ~01:30 — EN3T reparat; SC4P pus în coada contului 2
- **EN3T** (`F:\ClipForge-wt-en3t`, pe `2aebb8b`, necomis): prima suită a picat de 2 ori în
  `test_clipper_shared_export.py::test_frozen_replay_…`. Cauza: reluarea înghețată (`scripts/rerender_pilots.py`)
  construiește clipul fără `reasoning`.
  - Remediere: `getattr(clip, "reasoning", None)` → `absent`, plus un test nou (15 teste). Mutații 8/8.
  - Suita a doua (`A/en3t-suite.txt`): **3782 passed, exit 0**.
  - Trimis la Codex la ~01:28: `claude-to-codex/2026-09-28-en3t.md` → `codex-verdict-next-29.md`.
  - **next-29: forma e acceptată, dar EN3T nu e închis.**
    - R1: legarea trebuie făcută de capătul RANDAT (fereastra executată + durata probată), nu de rândul clipului.
    - R2: `delivered_s` limitat la fereastra livrată; NaN/Inf și înregistrările invalide primesc stare proprie.
    - §2: un export real izolat (f81b, `moved`) prin handler-ul normal, plus doi martori.
    - §3: sublot separat pentru reluarea înghețată (`rerender_pilots.py`).
    - Toate sunt la B: `B/EN3Tr.prompt.md`, lansat la ~01:32, în paralel cu OW1r2. SC4P rămâne în coadă după OW1r2.
  - **EN3Tr gata la 01:55** (`B/EN3Tr-result.md`):
    - stări noi: `invalid_record` / `not_corroborated`; `applies` numai când fereastra din argv și durata probată se
      potrivesc;
    - f81b real: `applies`, MP4 identic cu cel din EN3V2; martorii dau `end_changed_since_scoring` / `absent`;
    - mutații 26/26; suita dă 3835 passed;
    - verificarea A: 8/8 fișiere, 96 de teste rerulate.
    - Mesajul `claude-to-codex/2026-09-28-en3tr.md` pleacă împreună cu SC4P → next-31.
- **OW1r2** (B, `F:\ClipForge-wt-ow1r2`) e terminat, cu rezultatul în `B/OW1r2-result.md`:
  - R1–R3; mutații 21/21; suita dă 3836 passed.
  - Verificarea A: 31/31 fișiere, failures 0; testele OW1 + AQ1 rerulate (83 passed).
  - Trimis la Codex la ~01:38: `claude-to-codex/2026-09-28-ow1r2.md` → `codex-verdict-next-30.md`.
- **SC4P** (lotul 4 SC, proba de acceptare cu măștile aprobate 6053/b23c, izolată) a pornit la 01:35:
  - `B/SC4P.prompt.md`; `B/launch-SC4P.sh` așteaptă `B/OW1r2.done`, apoi pornește singur (WMI).
  - Nu activează și nu promovează nimic. Judecata vizuală e a lui A, apoi a omului.
  - **OPRIT la 01:43** (`B/SC4P-result.md`): `_executable` refuză ambele clipuri cu `mask_invalid`, pentru că
    `footprint_rule` din măștile înghețate are în plus `glyph_rows` și un `persist` șir. Nimic n-a fost randat.
  - Întrebarea pentru Codex e pregătită (`claude-to-codex/2026-09-28-sc4p.md` → next-31, cu rutele și baza
    erase/blur). Pleacă după next-30.
- **EN3V2: omul a răspuns** (10/10, DB = textul lipit; decodat în `A/en3v2-page/answers/EN3V2-decoded.md`):
  - lung 4 (p02 = p04, p08, p10); scurt 1 (p03); fără diferență 3 (p01, p05, p07);
  - control p09 corect; **control p06 (fișiere identice) raportat ca diferență**.
  - Decizia de activare e la Codex. EN3 rămâne oprit.
- **~02:20 Codex e fără credite** (next-30 pe OW1r2 n-a sosit). Contul 1 e la 92% → A doar coordonează.
  - B pe contul 2:
    - **INT1**: arbore de integrare OW1r2 + EN3T + §3 (`F:\ClipForge-wt-int1`), cu suita și listele pentru cele
      3 commit-uri; nimic comis;
    - **SC4Pv2**: măști v2 CANDIDAT (`persist` numeric, `glyph_rows` → `detected_rows`), randări izolate
      control/erase/blur și pagina pentru telefon; nimic aprobat.
  - La revenirea lui Codex pleacă un singur pachet: OW1r2 (dacă next-30 lipsește), EN3Tr, SC4P + SC4Pv2, EN3V2
    decodat, INT1.
  - **02:24: contul 2 a atins limita de sesiune** (resetare la 05:30). INT1 a rămas la merge (3 conflicte în
    `merge-file`); SC4Pv2 avea măștile v2 făcute, fără randări.
  - Reluarea era automată: `B/launch-INT1r.sh` și `B/launch-SC4Pv2r.sh` (WMI) așteptau 05:32, apoi rulau
    `B/*r.note.md` + promptul original. **N-au mai rulat:** procesele au dispărut înainte de ora pornirii.
- **~10:40: contul 2 nu mai e disponibil** (utilizatorul; nici un cont 3). A lucrează singur, cu Codex ca revizor.
  - INT1 e refăcut de A în `F:\ClipForge-wt-int1`:
    - `clipper_finalize.py` e îmbinat cu 0 conflicte; conflictele lui B veneau de la o bază LF contra fișierelor
      CRLF;
    - listele de commit sunt în `A/int1/INT1-commits.md`, iar harta pe trepte în `A/int1/map_v*.md`;
    - **suita: 2 failed** (`test_readiness`, `directories` False), în investigare cu un plugin
      (`scratchpad/int1/dircheck.py`).
  - Pachetul pentru Codex (`claude-to-codex/2026-09-28-package.md` → next-30) e trimis la ~10:55.
  - Randările SC se văd pe PC, cu copii la calitate completă, fără copii și pagină pentru telefon (utilizatorul,
    10:59).
- **next-30** (Codex, ~11:10):
  - acceptă commit-urile 1 (OW1r2), 2 (EN3T) și 3 (reluarea), condiționat de suita verde;
  - EN3: fără 0.4 implicit, doar folosire limitată pe o instanță izolată;
  - SC: ruta 1; măștile v2 aprobate ca intrări de probă (6053 `29989f80…`, b23c `ff24c0f9…`); ambele baze
    erase/blur, cu contractul F4/F5.
  - **C: era plin** (0 octeți): de acolo veneau cele 86 de eșecuri `WinError 112`. Probabil tot de acolo și cele
    2 eșecuri readiness, prin Windows Storage Sense, activ „când spațiul e redus”; nu e dovedit.
  - Pe F:, cu detector, 3904 passed. Suita finală INT1 (`A/int1-final-suite.txt`): **3904 passed, exit 0**.
  - `end_tail.py` are docstring-ul `applies` corectat (next-30 §2); manifestul e actualizat.
  - Addendum trimis → next-31.
  - Am șters doar `Temp\pytest-of-vlado` (1,1 GB, temporarele testelor). Restul curățeniei lui C: e decizia
    utilizatorului.
  - **Testele rulează de acum cu `TEMP`/`TMP`/`--basetemp` pe `F:\ClipForge-tmp`.**
- **SC4Pv2 (A):** `A/sc4v2/sc4v2_run.py`.
  - Proba uscată trece pe toate 4 combinațiile. Pe b23c blur, override-ul erase e [7158, 7178) = capătul lui L30.
  - Randările rulează: `exports/sct2/<clip>_{erase,blur}.mp4` în rădăcina SC4P, plus controlul în
    `A/sc4v2/control/`.

## 28 septembrie 2026, ~01:10 — 6 loturi comise, backend-ul pornit pe `2aebb8b`
- **Commit-uri** (toate cu acordul utilizatorului):
  - `01ae0a5` FD1;
  - `04627e8` BURST;
  - `ce3a4c2` UI;
  - `7e97e6b` SCB2 (+ poarta video);
  - `833c094` AQ1;
  - `2aebb8b` CC1 + C1r.
  - Ultima suită: **3769 passed** (`A/integration-scb2-aq1-cc1-suite.txt`).
- **8420 rulează pe `2aebb8b`** (preview `clipforge-backend-a`).
  - Căzuse la 20:56, odată cu sesiunea. Backup: `data/db/backup-2026-09-28-pre-restart-fd1-burst-scb2-aq1/`.
  - Smoke curat. EN3 și 3b oprite; SCB2 și CC1 inerte (fără configurație, `address=False`).
- **OW1r2 pe contul 2** (`B/OW1r2.prompt.md`, worktree `F:\ClipForge-wt-ow1r2`): OW1 rebazat pe `2aebb8b`, cu R1–R3
  din next-27 §4.
- **EN3V2**: pagina e la utilizator, https://claude.ai/artifact/5guhV3qETDBpF59pAc1AZ5 (artifact, `db` →
  colecția `answers`, un document per pereche).
  - Cheia oarbă e separată: `B/gates/en3v2/key/key.json`.
  - Verificări A: `A/en3v2-page/verify_page.txt` și `verify_audio.txt` (audio aliniat identic).
  - Trim: `B/gates/en3v2/trim/trim_check.json`, pauza supraviețuiește.
- **Codex:** next-28 (nota de integrare), în lucru.

## 28 septembrie 2026, ~00:55 — FD1 + BURST + UI comise (acordul utilizatorului)
- **Commit-uri:** `01ae0a5` FD1 · `04627e8` BURST (R1–R3, R1b, R1c) · `ce3a4c2` UI.
  - O singură suită pe arborele final: **3538 passed, exit 0** (`A/integration-fd1-burst-suite.txt`).
  - Rândurile de hartă au intrat cu fiecare lot, iar `CLAUDE.md` are acum `job_attempt.py`.
  - **8420 nu se repornește înainte de AQ1.**
- **La Codex:** `claude-to-codex/2026-09-28-c1r-aq1-scb2v-ow1.md` e trimis (→ next-27). Nota cu hash-urile,
  `2026-09-28-integration-hashes.md`, pleacă la mesajul următor.
- **Următoarele integrări**, după verdict: SCB2 (`A/scb2v-manifest.txt`, 27 de fișiere peste BURST) → AQ1 →
  CC1+C1r → OW1 (rebazat peste AQ1). Apoi repornirea controlată.
- **AQ1** (`F:\ClipForge-wt-aq1`): suita dă 3538 passed. La prima rulare au picat 6 teste, pentru că alte teste lasă
  rânduri `queued` datate 1990; testele AQ1 folosesc acum 1900.
- **SCB2 cu poarta video** (`F:\ClipForge-wt-scb2v` = BURST + SCB2 + `prepare`/`source_patch` în `handle_preview`):
  8 teste prin handler, cu pixeli; mutații 4/4; suita dă 3668 passed.
- **CC1 + C1r** (`F:\ClipForge-wt-cc1`): suita dă 3532 passed.
- **OW1** (`F:\ClipForge-wt-ow1`, livrat de B, verificat de A cu 29/29 fișiere): suita dă 3494 passed.
  - Se integrează DUPĂ AQ1; semnalul lui intră în ramurile verificate ale lui `cancel_job`.
- **EN3V2:** media paginii e validă, 20/20 fișiere, verificată de A (`A/en3v2-page/verify_page.txt`).
  - Mai trebuie: cazul cu trim și `B/EN3V2-result.md` (EN3V2f pe contul 2). Abia apoi pagina
    `A/en3v2-page/proba-finalurilor.html` merge la om, ca artifact cu `db`.
- **La Codex, de trimis:** `claude-to-codex/2026-09-28-c1r-aq1-scb2v-ow1.md` → next-27.

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

## Istoric și reluarea investigațiilor vechi

Actualizările de mai sus descriu starea curentă. Secțiunile anterioare au fost
mutate integral în arhivă pentru a respecta limita de 500 de linii; conțin și
afirmații ulterior retrase. Nu combinați cifre din rulări diferite.

- [snapshot, R0–R3](../../archive/clipper/2026-09-20-current-history-1.md)
- [R4–R6 și corecții](../../archive/clipper/2026-09-20-current-history-2.md)
- [R7, review și gate vizual](../../archive/clipper/2026-09-20-current-history-3.md)
- [R8, S7–S8 și încadrarea pe faze](../../archive/clipper/2026-09-20-current-history-4.md)
- [puncte de reluare și limite istorice](../../archive/clipper/2026-09-20-current-history-5.md)
- [9–24 sept.: planul master Codex, rescorarea, subtitrarea dublă, încadrarea](../../archive/clipper/2026-09-27-current-history-6.md)
- [26 sept.: D2, M0 v2, verdictul editorial, adresarea AD12, finalurile EN1/EN2](../../archive/clipper/2026-09-28-current-history-7.md)
