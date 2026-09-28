# Arhivă Clipper — 26 septembrie 2026: D2, M0 v2, verdictul editorial, adresarea AD12, finalurile EN1/EN2

Mutat din CURRENT la 28 septembrie 2026, pentru limita de 500 de linii. Istoric, inclusiv afirmații ulterior
retrase; nu este verdictul stării actuale. Conținutul secțiunilor este păstrat.

## 26 septembrie 2026, noaptea — finalurile (EN1 + EN2) comise și active, cu utilizare limitată

- **EN1 + EN2 sunt comise în `4a2c7ea`**, cu acordul utilizatorului, după ce Codex a închis tehnic R1, R2 și
  R3 (`codex-verdict-next-7.md`). Regula este `end_acoustics_v2`:
  - un final așezat pe capătul unui cuvânt Whisper se prelungește până la capătul acustic + 100 ms;
  - altfel, clipul se oprește la următorul final complet urmat de pauză, dacă e în 3,5 s și e **mai
    târziu** decât finalul curent (R1);
  - un `speech.wav` trunchiat dă `unavailable` (R2);
  - rafinarea rulează într-un worker thread, în afara event loop-ului, și se poate anula între pași (R3).
- **Verificări:**
  - suita pe arborele principal: **3069 passed** (`A/en2-integration/pytest.txt`);
  - smoke pe instanța izolată (`A/en2-integration/smoke_en2.txt`): rescorare prin handlerul real, anulare
    în timpul rafinării fără nimic publicat, retry, 40/40 candidați cu regula v2, proiectul martor neatins.
- **Backend-ul A a fost repornit la 22:47** pe `4a2c7ea`, fără joburi active. Copia de siguranță a DB e în
  `data/db/backup-2026-09-26-pre-en2-restart/`.
- **Utilizare limitată** (`codex-verdict-next-8.md`):
  - regula se aplică doar la scorările noi; corpusul vechi nu se rescorează, iar cele 19 ferestre manuale
    nu se rescriu;
  - omul verifică clipurile înainte de postare;
  - veto-ul, adică debutul energetic al cuvântului următor, rămâne neschimbat;
  - **negativele: 0 din 3 confirmate de ureche** (`A/editorial-review/negative-check-2026-09-26.txt`).
    Afirmațiile contrare din `B/EN2-result.md` §4 sunt retrase printr-o notă de corecție. Pragul nu se
    recalibrează din trei răspunsuri.
- **Proba primului lot de utilizare**, încă neînceput (nu există clipuri noi):
  - 6 extensii aplicate: 3 `tail_extended` + 3 `next_complete_ending`, din cel puțin 2 surse, alese înainte
    de verdict;
  - ascultare oarbă vechi/nou pe exporturile efective; „niciunul” înseamnă respingere;
  - o extensie care taie un cuvânt se păstrează ca atare, iar extinderile automate se opresc pentru
    utilizarea respectivă până la diagnostic.
- **Răspunsurile omului**, date pe pagina neutralizată după next-9 §3 și copiate exact în
  `A/editorial-review/de1-sc-answers-2026-09-26.txt`:
  - **dea939: varianta nouă**, adică startul la 13044.80, care include acuzația. Finalul e același, iar
    motivul nu e dat. Preferința e compatibilă cu ipoteza setup-ului, dar n-o demonstrează. Codex a aprobat
    corecția locală (next-11 §2): backup, cale normală, exportul vertical prezentat. Ipoteza că scurtarea
    story_v1 taie premisele ține de un lot separat (`A/editorial-review/story-start-census-2026-09-26.txt`:
    18/31 story_short încep după propriul hook_t);
  - **3e42: „pauza puțin prea scurtă” la final.** E al doilea semnal după 1204 (E0) că liniștea de după
    ultimul cuvânt (+0,1 s) e prea scurtă. EN1 rămâne neschimbat, iar proba cu cozi mai lungi e propusă
    lui Codex (next-11);
  - **linia F4/F5: (c)**, comutare la k 7158. Autorizarea e legată de fișierul
    `answers/de1-sc-2026-09-26T23-18-49.txt` prin sha256.
- **Codex:**
  - next-9: ambele addendum-uri sunt acceptate cu amendamente;
  - next-10: SC v2 e acceptat, deci lotul 1 poate începe. Fingerprint-ul folosește o proiecție semantică,
    separată de hash-ul manifestului. Poarta refuză orice tratament neexecutabil, inclusiv cu `suppress`. Cele
    patru aserțiuni v2 din testele existente trec la V3 în lotul 2.
- **Lotul 0 SC**, citirea lui A, e gata: `A/source-captions/whole-reading-v2.json`.
  - 55 de linii cu intervale exacte `[k_on, k_off)`;
  - fiecare cadru al celor 76 de tranziții a fost văzut. Există un gol ascuns de un cadru la b23c k 6422;
  - scanarea pe fiecare cadru (control prealabil, nu detector) susține doar că niciun cadru atribuit unei linii
    nu a declanșat controlul. Nu exclude un cuvânt adăugat temporar sau o linie necunoscută (next-11 §3). Cele
    55 de semnalări (curea, sfoară) au fost verificate vizual. b23c k 6422 rămâne regresie explicită;
  - acoperire: A a văzut 465/1711 cadre la 6053 și 525/1770 la b23c.
  Urmează partea lui B (SC0): detectorul, măștile de glifă ≥ 50 %, validatorul.
- **Contul 2:** AD3 (adresarea, pașii 3a + 3b, în `F:\ClipForge-wt-ad3`) rulează. După el pornesc automat
  SCB1 (nucleul SC, în `F:\ClipForge-wt-sc`) și SC0 (`B/chain-after-ad3.sh`).
- **Truth v3 (TV3) e gata**, cu proveniența fiecărei cutii de cap (`A/verification-lot/score_truth_v3.txt`,
  post-hoc, etichetat).
  - YuNet pe sursă: 65/98 persoane-cadru (Moist 8/36);
  - Haar pe sursă: 83/98;
  - verdictul pe camere (4/4, 0 regiuni false) rămâne cel al lui M0 v2.
- **Worktree-urile** `F:\ClipForge-wt-addressing` și `F:\ClipForge-wt-endings` au conținutul comis și se
  pot șterge (`git worktree remove`) cu acordul utilizatorului.

## 26 septembrie 2026, seara — adresarea (AD12) integrată și comisă; backend-ul repornit

- **AD12, pașii 1–2, cu AD12-r**, e închis de Codex (`codex-verdict-next-4.md`) și comis cu acordul
  utilizatorului:
  - `0f50dc7`: `proxy_clock.py`, `proxy_provenance.py`, ingest/pipeline, 4 fișiere de teste,
    `tests/data/proxy_clock/` și rândurile din hartă. Pașii 1 și 2 sunt împreună, pentru că dovezile și
    scriptul de reproducere importă `proxy_provenance`;
  - `073ef97`: testul heartbeat pe ceas controlat.
- **Testul de fum de dinaintea repornirii**, pe instanța izolată, cu codul din worktree
  (`A/ad12-smoke/smoke_*.txt`):
  - proiectele vechi: fără fișiere de proveniență, fără joburi noi, cu același status și același board;
  - un ingest nou de 75 s: proveniență cu hash complet, ceas `validated`, domeniu `ok`, iar
    `frames_pts.json` are 8/8 cadre decodate;
  - anularea în ingest nu lasă nicio proveniență, iar retry-ul o înregistrează.
  Separat, pe sondă, transcrierea a fost lentă: 590 s pentru 75 s.
- **Integrarea:**
  - baza a fost reverificată (= fd76b8f), fără coliziuni;
  - cele 24 de fișiere au fost transferate din manifest și verificate prin hash;
  - suita completă pe arborele principal dă **3005 passed**, 2 deselected, exit 0
    (`A/ad12-integration/pytest.txt`);
  - cele 2 skip-uri din worktree depind de date (fără corpus și fără modelul YuNet acolo) și trec în
    arborele principal.
- **Backend-ul A (8420)** a fost repornit pe codul nou, fără joburi active. Copia de siguranță a DB e în
  `data/db/backup-2026-09-26-pre-ad12-restart/`.
  - Atenție: serverele pornite din sesiunea Claude Desktop se opresc când aplicația repornește sesiunea.
    Pentru rulare de durată, folosiți `scripts/start_all.ps1`.
- **QA1, verificarea încrucișată a truth v2** (`B/QA1-result.md`):
  - referințele `camera_picture` sunt corecte la ≤ 4 px, deci 4/4 camere rămâne valabil;
  - la cutiile de cap: 8 ok, 1 ok prin convenție, 3 greșite;
  - eroare sistematică pe clasele **Jynxzi** (38 de rânduri: marginea dreaptă și căștile, fața tăiată în
    trei sferturi) și **Speed** (12 rânduri: marginea de jos sub bărbie);
  - readnotarea v3 e doar pe aceste clase, cu convenția „silueta exterioară a capului, cu căștile” (B,
    TV3). Scorurile pe persoane nu se acceptă înainte de ea.
- **Worktree-ul `F:\ClipForge-wt-addressing`** și-a încheiat rolul: se poate șterge
  (`git worktree remove`). `F:\ClipForge-wt-endings` e încă folosit de EN1.

## 26 septembrie 2026, după-amiaza — verdictul editorial, cauza finalurilor, verificarea M0 v2

Nimic comis după `fd76b8f`. Dovezile sunt în `data/claude-master-20260924/{A,B}/`.

- **Verdictul editorial al utilizatorului** pe 9 exporturi, pe clipul întreg, cu sunet
  (`A/editorial-review/verdict-2026-09-26.txt`, copiat exact și în `A/editorial-review.md`):
  - merită: da 6; nesigur 1 (02dea: „nu e neapărat un moment chiar bun”); nu 1 (e8fa); nemarcat 1 (b23c);
  - probleme: finalul (dea939, 02dea: „se taie cuvânt la final”), subiectul în cadru (c04e la 3,133 s;
    e8fa), salturi de cadru (e8fa);
  - observații:
    - 9d2a: lipsește subtitrarea a ce spune el (minoră);
    - 6053 și b23c: subtitrarea sursei e tăiată de crop, deci ștergere/blur și strat nou. Codex a
      autorizat doar o probă pe aceste două clipuri (`codex-verdict-next-1.md` §5).
- **Corectură la linia de bază a rafalelor.** „71/100” număra și `.ass`-uri nearse (`suppress`).
  - Pe fișierele arse sau probabil arse: **64/85, dintre care 38 cu ≥ 50 ms**.
  - Doar 4 au `render_record`; restul sunt deduse (`A/d2r-check/ass_census_burned.txt`).
  - Codex: se raportează separat și nu sunt o măsurătoare certă pe MP4.
- **Finalurile tăiate.** Ordinea cerută de Codex: cauză → intervenție minimă → probă audio → regulă.
  - **E0, probă audio** (`B/E0-result.md`): ceasurile sunt corecte (±5 ms). Defectul e **unde** e pus
    `source_end`. Finalurile cad pe sfârșitul cuvântului din Whisper, deci se pierd 180–390 ms de sunet.
  - **E1, trasare pe codul fiecărei epoci** (`B/E1-result.md`), trei cauze:
    - 3e42 și dea939 sunt ferestre de dinainte de `_keep_release` (4a136de, 15 aug.), doar rerandate. Un
      clip exportat nu e rafinat din nou. În DB mai sunt **19 rânduri exportate** de dinainte de regulă.
    - La 1204, garda lui `_keep_release` e blocată de cuvântul („so”) pe care `_drop_dangling_tail`
      tocmai l-a eliminat.
    - La 02dea, `_nearest_sentence_end` alege o propoziție fără pauză după ea.
    - Controlul acceptat 70ca: codul de azi l-ar muta cu +1,67 s.
  - **Ascultarea utilizatorului** (`A/editorial-review/listening-2026-09-26.txt`):
    - coada e preferată la 3e42, dea939 și 1204. La 1204 se aude coada lui „happening”, nu „so”, deci
      garda greșește;
    - 70ca: finalul codului de azi e „mai ok”;
    - 02dea: finalul complet de mai târziu (C).
  - Lotul de cod pentru finaluri așteaptă `codex-verdict-next-3.md`.
- **Verificarea independentă M0 v2** (`B/M0v2-verify-result.md`): ieșirile au fost înghețate înaintea
  deschiderii adevărului.
  - 178/178 de cadre adresate.
  - Candidatul YuNet/sursă: **4/4 camere, 0 regiuni false, 0 pe cazurile fără cameră**. Haar/proxy (baza
    de producție) 3/4, cu Kai pierdut.
  - 3 repetiții identice.
  - Cutiile de cap din adevărul v1 al lui A erau deplasate (Kai pe poster 12/12; Speed oprit la ochi).
    **Truth v2** a fost readnotat după rulare, verificat prin suprapunere și etichetat post-hoc
    (`A/verification-lot/TRUTH-v2.sha256`, `score_truth_v2.txt`): candidatul are 65/98 persoane-cadru,
    iar Moist e punctul slab (8/36).
  - Gate-ul limitelor și decizia de integrare sunt la Codex (next-3).
- **Worktree-uri separate** (codul de producție nu se scrie sub backend-ul pornit):
  `F:\ClipForge-wt-addressing` (AD12) și `F:\ClipForge-wt-endings` (EN1), ambele detached la `fd76b8f`.
- **Adresarea proxy→sursă, lotul 1:**
  - pașii 1–2 (`proxy_clock.py`, `proxy_provenance.py`, `ingest.py`) sunt scriși în **worktree-ul
    separat `F:\ClipForge-wt-addressing`** (detached la `fd76b8f`), nu în arborele principal;
  - Codex (next-2): pasul 1 e acceptat, pasul 2 are nevoie de corecturi. AD12-r s-a oprit la limita
    contului 2 și a fost relansat;
  - mutarea în arborele principal, repornirea backend-ului și commit-urile se fac doar cu acordul
    utilizatorului. După integrare, worktree-ul se șterge (`git worktree remove`).
- **Verdictul Codex next-3** (`codex-verdict-next-3.md`):
  - **M0 v2** trece gate-ul declarat (4/4 camere, 0 regiuni false). Poate intra ca **sursă de seed-uri
    pentru camere**, numai după AD12-r verificat, compunerea ceasurilor și gate-ul limitelor. Condiții:
    - fără fallback Haar/sursă, pentru că admite regiuni false;
    - eșantionare rară, pentru că YuNet costă 866 ms/cadru, față de 54 la Haar/proxy;
    - costul se măsoară pe proiect înainte de activarea implicită;
    - nu rezolvă c04e și nu înlocuiește detecția fețelor.
  - **Truth v2** e acceptat ca post-hoc etichetat, cu o verificare încrucișată pe 12 cadre fixate
    înainte (`A/verification-lot/QA-SAMPLE.md`, făcută de B în QA1, fără ieșirile brațelor).
  - **Gate-ul limitelor** are ambele referințe, cu roluri separate:
    - `camera_picture`: erori semnate pe 4 margini, cu imaginea omisă și zona străină raportate separat;
    - persoana sau obiectul: conținere în fereastra livrată, dimensiunea pe output și salturile.
    IoU-ul rămâne doar diagnostic.
  - **Lotul de finaluri** e aprobat pentru cod și probe, fără modificarea exporturilor existente:
    - la 1204, extinderea se face doar cu dovadă acustică locală, plus un test negativ obligatoriu;
    - la 02dea, se caută următorul final complet cu pauză acustică, iar cazul pozitiv ajunge la C;
    - cele 19 rânduri vechi primesc doar un audit fără rescriere;
    - 70ca rămâne control acceptat.
    Lotul rulează ca EN1 în worktree-ul `F:\ClipForge-wt-endings`.
- **Rămâne:**
  - EN1 (finaluri), apoi integrarea lui;
  - AD12-r terminat (relansat), apoi integrarea;
  - QA1 (truth v2);
  - gate-ul limitelor;
  - proba de ștergere a subtitrării sursei pe 6053 și b23c;
  - cercetarea rafalelor;
  - adresarea, pașii 3–6;
  - O2–O4.

## 26 septembrie 2026 — D2 închis și comis; backend-ul repornit; M0 v2 înghețat

- **Commit-uri, cu acordul utilizatorului:**
  - `d0f397a` R4c;
  - `d01dd6e` D1 + O1;
  - `c4e8db3` D2, cu corecturile D2r, D2r-2, D2r-3, UI-ul lor și rândurile din hartă.
- **Integrarea finală, pe arborele înghețat:** 2894 passed, 2 deselected (TikTok), exit 0; tsc 0; eslint
  cu 4 erori preexistente, în fișiere neatinse (`A/full-suite-final/RESULT.md`).
- **Verdictele Codex:** `codex-verdict-closure-2.md` … `-closure-4.md`.
  - Regula de temporizare a fost corectată pe date reale. `end > start` refuza 50% din alternative
    (3.417 din 6.813 ferestre), numai din cauza cuvintelor punctuale ale lui whisper. Acum e
    `end >= start` (`A/d2r-check/timing_census.txt`).
  - Un preview publică acum ca un export, iar eroarea proiectului e a ultimei încercări de ANALIZĂ.
    Înainte, un preview aruncat apărea ca eșec de analiză, cu un Retry care rescora proiectul.
- **Acceptarea în browser** pe sondă: traseele din closure-4 (`A/d2r-check/d2r3_live.txt`).
- **Operațional:**
  - backend-ul A (8420) e repornit pe codul nou, printr-o configurație `clipforge-backend-a` în
    `.claude/launch.json` (aceleași variabile ca `start_all.ps1`); există un singur GPU, deci nu există
    backend B;
  - copia de siguranță a DB e în `data/db/backup-2026-09-26-pre-restart/`;
  - migrarea a adăugat `clips.export_job_id`;
  - `release_stuck_exports.py` (listare) a găsit 0.
- **Lotul subtitrărilor suprapuse**, aprobat separat (closure-3 §1 B), are linia de bază:
  - 71 din 100 de exporturi livrate au evenimente ASS suprapuse pe aceeași ancoră;
  - 41 dintre ele au suprapuneri de 50–120 ms;
  - 299 de perechi au interval identic, adică rafale comprimate;
  - vezi `A/d2r-check/BUILDER-BASELINE.md`.
- **Încadrarea:**
  - `codex-verdict-framing-4.md` a aprobat M0 v2 și a ales un contract de adresare versionat.
  - SPEC v2 e fixat înaintea predicțiilor: 70ca keyed trece la diagnostic separat, iar ținta
    inset-ului e `camera_picture` (`A/verification-lot/SPEC-v2.md`, `insets-v2.json`).
  - M0 v2 e înghețat (`B/M0v2-result.md`): o replică exactă a vsync CFR din ffmpeg 8.1.1. Pe lotul de
    dezvoltare nu s-a mișcat nimic.
  - Tabelul de adrese al lotului de verificare are 178/178 cadre adresate. Jensen are 23,976 fps, iar
    −7 ar fi greșit toate cele 80 de cadre ale lui.
  - Constatare: ordinea căsuțelor la Haar depinde de fire și schimbă încrederea regiunii lui Kai.
  - Addendumul pentru lotul 1 de producție e în `B/addressing-lot1-addendum.md`, doar document.
- **Rămâne (dimineața) — înlocuit de secțiunea de după-amiază, de sus:**
  - Utilizatorul: verdictul editorial pe cele 9 exporturi, în pagina
    `A/editorial-review/review.html`, servită local de `clipforge-editorial` pe portul 8778.
  - Încadrarea:
    - A adnotează cadrele sursă adresate (`B/gates/m0v2/address_table.json`), fără predicții;
    - B rulează apoi M0 v2 înghețat pe lot;
    - urmează gate-ul limitelor, apoi loturile de producție pentru adresare.
  - Lotul subtitrărilor suprapuse (builder), apoi plasarea subtitrării peste overlay-urile stream-ului.
  - Mărunțișuri UI: O2–O4 și cardul „Ready to analyse”, care arată „undefined–undefineds” fără setări
    de lungime (preexistent).
