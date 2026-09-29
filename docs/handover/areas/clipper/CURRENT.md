# Handover — AI Stream Clipper

## 29 septembrie 2026, ~16:20 — cercetare (PR #32) și un test în cloud; de reluat pe PC
- **Cercetarea** e doar documentație, fără cod de produs, pe `claude/fireclaw-clipping-apps-fnmlou` (PR #32):
  - `docs/research/clipping-apps-survey-2026-09-29.md` (planul CA1–CA16);
  - `docs/research/clipper-scoring-selection-2026-09-29.md` (planul SL1–SL12);
  - `docs/research/clipper-reaction-signals-2026-09-29.md` (planul RS1–RS8): vocea streamerului față de
    conținut, decalajul și emote-urile din chat, logistica.
  - `docs/research/opusclip-reverse-engineering-2026-09-29.md`: OpusClip din surse publice (API, centrul de ajutor),
    plus `opus_blackbox.py`, care descrie clipurile lui pe VOD-ul de test când primește lista.
  - `docs/research/eklipse-reverse-engineering-2026-09-29.md`: Eklipse, concurentul cel mai apropiat (VOD-uri de
    stream). Motorul lor rutează pe categoria platformei; capitolele Twitch din yt-dlp ar fi la noi o a doua sursă
    pentru tipul segmentului.
- **Testul în cloud** e în `docs/research/clipper-cloud-test-2026-09-29.md`. VOD Twitch de 64 min, mod euristic
  (fără cheie LLM), fără GPU. Rezultate:
  - niciun clip după 44:20, deși acolo sunt cele mai puternice momente din chat;
  - board-ul nu prinde momentele din chat: 0/12 la decalajul măsurat din sunet (~10 s), față de 12% aleator;
  - clipul #1 e naratorul unui trailer.
- **Exportul dinamic se blochează pe ffmpeg < 8.** Schimbarea mărimii `crop` prin `sendcmd` oprește ffmpeg 6.1 și
  7.0 fără mesaj, până la `RENDER_TIMEOUT`. PC-ul are 8.1, deci nu e afectat. Guard-ul de versiune e propus ca task
  separat, necomis.
- **Retry pe o sursă URL descarcă din nou VOD-ul.** Propus ca task separat, necomis.
- **De făcut pe PC:** același VOD, cu judge-ul LLM pornit; apoi comparația board PC / board cloud / chat / OpusClip
  (§9 din documentul testului). Lista OpusClip e la utilizator.

## 28 septembrie 2026, ~18:35 — Clipper închis (next-38); RX1 comis și live; omul a acceptat variantele B
- **next-38 (Codex):** versiunea e închisă ca **utilizabilă cu verificare umană înainte de postare**. RSK e închis
  editorial pe cele trei fișiere B identificate prin hash; nu mai rămâne niciun blocaj de release. Acceptarea lor nu
  certifică alte exporturi și nici atribuirea vorbitorului.
- **Omul a acceptat cele trei variante B** (~18:10, formular cu două întrebări, după vizionarea paginii de comparație):
  - R2: „Da, jos e bine” (camera cu amândoi în banda de jos);
  - Speed/Kai: „Da, e acceptabil” (Kai vizibil tot clipul).
  - Răspunsul, cu cele trei hash-uri și abaterile, e în `A/rsk/human-answer-2026-09-28.txt`.
  - Egalitatea de octeți a controalelor RX1 arată doar că RX1 nu a schimbat fișierele; acceptarea e răspunsul omului.
- **next-37:** RX1 e închis tehnic; commitul și restartul au fost aprobate de om.
- **Commit `74f3686`** (fix RX1, 14 fișiere): blob-urile sunt 14/14 identice cu worktree-ul verificat, iar suita
  completă pe acel arbore a dat 4023 passed.
- **Backup** în `data/db/backup-2026-09-28-pre-rx1/` (integritate ok).
- **8420 repornit la 18:21:47 pe `74f3686`, ready.** Smoke (`A/rx1/smoke_live.json`): martorul bb9b cu conținut de
  880 px → 422 cu 802 px, zero scrieri (rândul, evenimentele, joburile și hash-urile fișierelor DB).
- **Pe date de probă** (8449, codul integrat din main, rădăcina izolată; `A/rx1/smoke_probe.json`):
  - controlul valid s-a salvat „verified”, iar exportul normal a ars planul construit, lăsându-l nescris în DB;
  - avertismentul pentru timpi lipsă: 200 + `no_timed_words`.
- **Corecturi cerute de next-37 §2:**
  - 38/78/68 s sunt timpii de procesare ai exporturilor de control; duratele fișierelor sunt 35,533 / 87,567 /
    75,640 s (Speed/Kai / bb9b / 5d89);
  - cele 23 de teste roșii dinainte de reparație nu sunt 23 de defecte independente: 3 pică numai pe câmpul nou din
    răspuns;
  - `verified: true` certifică verificarea cu intrările de la salvare, nu orice export viitor; randarea rămâne
    autoritatea, inclusiv pentru trim.
- **Starea Clipper-ului:** versiune utilizabilă **cu verificare umană înainte de postare**.
  - Blur numai cu mască validată offline, pe calea dinamică; erase neoferit.
  - Încadrarea automată încă taie oameni pe reacții și pe clipurile cu două camere (RSK: 3/3). Soluția livrată e
    încadrarea manuală din editorul de reacție, verificată de om.
  - Atribuirea vorbitorului nu există. EN3 și 3b sunt oprite.
- **Update-uri viitoare:** lista din secțiunea ~17:00, plus:
  - camera sus în editorul de reacție (varianta (a) din next-36 §2);
  - încadrarea pe intervale („Kai doar când vorbește”);
  - repararea încadrării automate pe reacții.
- **Curățarea** nu blochează închiderea; omul a ales să o facem mai târziu. Include rădăcina RSK (≈17 GB),
  `F:\ClipForge-tmp\rx1root` și worktree-urile. Înainte de ea (next-38 §3):
  - păstrați exporturile acceptate, sidecar-urile, măștile și dependențele lor, comenzile și configurațiile, logurile,
    manifestele și răspunsurile omului;
  - documentați noile căi și verificați că scripturile și paginile de probă găsesc aceleași hash-uri după remapare;
  - opriți instanțele de probă și confirmați că nimic activ nu depinde de ele;
  - salvați separat artefactele ignorate de Git, pentru că arhivarea unui worktree nu le păstrează.

## 28 septembrie 2026, ~18:05 — next-36 (RSK condiționat de om, RX1 aprobat); RX1 gata în worktree; next-37 la Codex
- **next-36:**
  - RSK închide criteriul din next-33 §2 numai dacă omul acceptă cele trei variante B, inclusiv camera jos la R2 și
    Kai vizibil tot clipul; răspunsul lui se consemnează cu cele trei hash-uri B.
  - Camera sus la R2 se face numai la cerere, ca variantă a editorului de reacție.
  - RX1 e aprobat înainte de închidere, cu un contract lărgit (§3).
- **RX1** (`F:\ClipForge-wt-rx1` pe `c155dbc`, necomis; 14 fișiere în `A/rx1/rx1-files.txt`):
  - `plan_to_place` (în `routers/clipper_caption_source.py`) verifică planul pe care îl construiește randarea
    (`_plan_for_render`), fără să-l scrie. Îl folosesc reaction PUT și `_place_or_refuse` (acum async), deci și
    cele trei rute care pornesc stratul: caption-source clip, stratul SC3, răspunsul proiectului.
  - Citire/construire eșuată → 422 `caption_check_failed`. Timpi lipsă → se salvează cu
    `caption_placement.verified: false`, iar UI-ul arată un rând chihlimbariu (`caption-placement.ts`).
  - 28 de teste noi, din care 23 pică fără reparație. Suita completă: **4023 passed**. tsc și eslint curate.
  - Pe clipurile RSK reale (instanța izolată 8448, `F:\ClipForge-tmp\rx1root`): încadrările failed1 ale lui B dau acum
    422 la PUT (802/802/884 px); cele livrate trec.
  - Cele 3 exporturi de control sunt **byte cu byte identice** cu variantele B acceptate.
  - Proba din browser e în `A/rx1/ui-check.json`.
- **Rămâne:**
  - verdictul lui Codex next-37;
  - răspunsul omului pe variantele B;
  - cu acordul omului: commit RX1, restart 8420 + smoke, apoi handover-ul final și commitul de docs.
  - Curățarea worktree-urilor nu blochează închiderea (next-36 §4). Rădăcina RSK de 17 GB se păstrează până se
    documentează remaparea scripturilor lui B la originalele verificate.

## 28 septembrie 2026, ~17:40 — RSK livrat de B; pagina de comparație la om; next-36 la Codex
- **RSK** (B, contul 2): raportul e în `B/RSK-result.md`, probele în `B/gates/rsk/`. Nu s-a atins cod de producție.
  - Worktree `F:\ClipForge-wt-rsk` la `190af2e`, curat la final.
  - Rădăcina izolată e `B/gates/rsk/root/` (≈17 GB de copii). Instanța 8447 e oprită.
  - DB-ul live nu a fost modificat: hash identic de la 16:42:01 până la final. Mtime-ul WAL-ului, 16:41:38, vine de
    la restartul 8420 făcut de A.
- **Măsurarea** s-a făcut pe fișierul randat: fereastra livrată e înregistrată din pixeli, 800/800 de eșantioane.
  - `bb9b21879ad0` (R2, pleacă unul):
    - automat: femeia e tăiată la 0–12,5 s și la ≈33 s;
    - manual (editorul de reacție): camera e întreagă 176/176.
  - `5d89f6a32c99` (R2, se apleacă):
    - automat: femeia e tăiată ≈60 s din 75,6;
    - manual: camera e întreagă 152/152.
  - `8c89091073ff` (Speed/Kai):
    - automat: Kai e tăiat 54/72, inclusiv la 25,5–33 s, când vorbește el;
    - manual: Speed 72/72 și Kai 72/72.
- **Abateri de la next-22:**
  - la R2, camera e în banda de jos, pentru că editorul face numai `game_top_face_bottom`;
  - Kai se vede tot clipul, pentru că nu există încadrare pe intervale.
- **Capcana găsită:**
  - pe un clip fără `caption_plan` stocat, `PUT /reaction-layout` răspunde 200;
  - exportul e apoi refuzat cu „reaction caption placement failed”;
  - propunerea e lotul RX1 (verificarea din PUT pe planul pe care l-ar construi randarea).
- **Pagina** `B/gates/rsk/page/compara.html` e servită pe 8780 (config `clipforge-rsk`). A a verificat că cele 6 mp4
  sunt byte cu byte exporturile lui B și că fiecare sidecar e legat de fișierul lui.
- **În așteptare:**
  - verdictul omului pe variantele manuale;
  - next-36 la Codex (`data/claude-to-codex/2026-09-28-rsk.md`), care decide închiderea, RX1 și banda de sus la R2.

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
- **Comis și pornit** (acordul omului): `c155dbc` SC3 (29 de fișiere + handover).
  - Backup: `data/db/backup-2026-09-28-pre-sc3/`. **8420 rulează pe `c155dbc`**: coloanele noi sunt migrate,
    `/api/ready` = ready, download-ul merge, EN3/3b sunt oprite.
  - Măștile v2 (6053 `29989f80…`, b23c `ff24c0f9…`) sunt importate cu unealta în `data/clipper/pilotf81b/
    source_caption_masks/` (`A/sc3/import-live.txt`). Blur apare disponibil pe ambele clipuri în instanța reală.
  - Instanța de acceptare (8446/3046) e oprită; rădăcina `F:\ClipForge-tmp\sc3root` e păstrată.
- **Rămâne RSK** (B, contul 2, `B/RSK.prompt.md`): exporturile finale R2 și Speed/Kai care păstrează persoanele
  (next-33 §2), ultimul criteriu de închidere.
- **Update-uri viitoare, după închidere** (cerute de om sau amânate de Codex; niciunul nu blochează release-ul):
  - **Analiza pe GPU** (cerută de om, 28 sept.):
    - torch cu CUDA, pentru easyocr (acum e wheel CPU-only);
    - `onnxruntime-gpu` pentru YuNet (modelul e ONNX; `cv2.dnn` din pip n-are CUDA);
    - Haar rămâne pe CPU; Whisper e deja pe GPU (RTX 3060; indexul ncnn e inversat, vezi CLAUDE.md);
    - lot separat, cu benchmark înainte/după și verificarea că fețele și textul detectat rămân aceleași.
  - SC: erase (amânat de om), producătorul automat de măști, randarea statică.
  - Detectarea automată a celui care vorbește/reacționează (Speed/Kai), declarată ca etapă separată.
  - Activarea generală EN3 (next-30 §3: un lot nou, fixat dinainte); 3b; CC1 C3.
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
- [27 sept.: FD1, R1c, C1r, AQ1, AD3/AD3C, EN3, BURST1, UI, SC lot 1 și loturile pe contul 2](../../archive/clipper/2026-09-28-current-history-8.md)
