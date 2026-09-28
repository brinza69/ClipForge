# Clipper — plan de execuție și acceptare, 24 septembrie 2026

Autor: Codex. Intrare: `data/claude-to-codex/2026-09-24-brief.md`.
Bază citită: HEAD `37537b4`, plus lotul necomis de păstrare a editărilor.
Acesta este planul curent; planurile anterioare rămân istoricul experimentelor.
Toate căile de mai jos sunt relative la `F:\ClipForge`.

## 1. Ce acceptăm și ce nu este încă demonstrat

- `43aaff0`: remedierea îngustă a dimensiunilor este acceptată. Proba STATICĂ
  înainte/după arată efectul; proba dinamică identică nu dovedește acel efect.
- `08c6781` și `37537b4` sunt prezente. Nu redeschidem implementarea lor integral;
  verificăm interacțiunile de mai jos. Cifrele 2446/2483/2502 sunt rezultatele
  raportate de Claude, nu o nouă rulare completă făcută de Codex.
- Codex a rulat acum cele două fișiere `test_clipper_rescore_keeps_edits.py` și
  `test_clipper_clip_caption_source.py`: **56 passed**. Păstrarea editărilor este
  justificată; nu este încă o garanție împotriva operațiilor concurente.
- **Gol confirmat în cod:** `put_caption_source` comite editarea, apoi
  `feedback.record` comite evenimentul. `_kept_clips` se bazează pe eveniment.
  Între cele două salvări, o rescorare poate vedea editarea fără protecția ei.
  Fereastra există; intercalarea exactă trebuie reprodusă în B1.
- `_kept_clips` nu păstrează azi un clip `exporting` fără eveniment uman.
  Verificăm în B1 rescorarea concurentă cu exportul, nu doar editarea concurentă.
- NULL la originea evenimentelor vechi rămâne protecție conservatoare împotriva
  pierderii. Nu îl redenumim dovadă că autorul a fost om. Rejected rămâne păstrat.
- Pornirea normală a aplicației cu migrarea declarată respectă CLAUDE.md.
  Interdicția anterioară privea probe private care apelau `init_db` pe DB reală.
  B1 testează migrarea idempotent pe o DB temporară; nu o repetă pe producție.
- Lotul geometric 1B este închis ca instrument: 11 teste, 8 hash-uri verificate.
  Nu certifică încadrarea automată. Spatial-only, detectorul local, separarea
  prin mișcare și corecția ceasului NU au candidat acceptat pentru producție.
- Exporturile 02dea/3e42 au fost înlocuite ulterior cu acord, conform brief-ului
  și raportului `data/claude-reaction-editor/slice4h-bad/REPLACE-RESULT.md`.
  Nu repetăm înlocuirea. A1 verifică fișierele și copiile, apoi corectează notele vechi.

## 2. Organizare: doi executanți, un validator

**A = Claude desktop, contul 1:** UI, browser, vizionare, integrare documentație.
**B = Claude CLI, contul 2:** backend, teste deterministe, măsurători offline.
**Codex:** stabilește contractele, verifică probele decisive și dă verdictul pe lot.

Împărțire estimativă egală: A1/A2/A3/A4 = 5/4/5/6 unități; B1/B2/B3/B4 =
8/5/3/4 unități, total **20 fiecare**. Unitățile sunt efort relativ, nu ore promise.
Dacă un lot depășește estimarea, se predă o sub-sarcină cu fișiere noi, nu se
deschid aceleași fișiere din ambele conturi.

| Val | A | B | Condiție de trecere |
|---|---|---|---|
| 1 | A1: inventar, probe UI și martori vizuali | B1: protecția editărilor/exporturilor | probe și teste negative acceptate |
| 2 | A2: politica proiectului în UI | B2: setare fără rescorare | contract comun de mai jos; integrare după B2 |
| 3 | A3: revizie pe rulări distincte | B3: cohortele de revizie | aceeași rulare și aceleași fișiere în fiecare cohortă |
| 4 | A4: verificare video completă + set editorial | B4: audit artefacte + diagnostic automat | raport comun; o singură suită completă |

A poate construi UI pe răspunsuri simulate în valurile 2/3, etichetate ca atare;
acceptarea în browser cere backend-ul real. A1 pregătește setul vizual și pentru B4.
Nu porni două backend-uri cu versiuni diferite împotriva aceleiași DB de probe.

**Proprietate exclusivă:** A modifică numai fișierele frontend enumerate și
`data/claude-master-20260924/A/`; B numai backend/testele enumerate și
`data/claude-master-20260924/B/`. A este singurul scriitor al documentației comune
după predarea fiecărui lot: acest PRP, `docs/clipper-map.md`, cele două CURRENT.md,
`PRPs/clipper-framing-next-steps-2026-09-24.md`. B livrează propuneri de text în raport.
Fișiere noi marcate „nou” sunt autorizate numai în lotul respectiv.
O extracție impusă de limita de 500 linii se notează în manifest înainte de editare;
nu se mută codul altui flux. Fără refactorizare generală.

Fișierele murdare fără autor verificat se evită. În special `dynamic_edit.py`,
`test_clipper_dynamic_export.py`, `scripts/render_dynamic_clip.py`, `src/lib/api-error.ts`
și celelalte pagini/documente ne-Clipper din brief. Nu reset, nu stage global.
Starea „modified” nu dovedește autorul; nici identitatea cu HEAD nu autorizează ștergeri.

## 3. A1 — inventar reproductibil și martor vizual (5 unități)

**Problemă:** rapoarte istorice contradictorii; sursa dea939 a fost confundată
cu primul fișier găsit: 854×480 versus fișierul randat 1920×1080.
**Fișiere scrise:** `data/claude-master-20260924/A/baseline.json`, `baseline.md`,
`visual-cases.json`, `browser-baseline.md`, `capture-baseline.py` (nou, privat).
Documentația comună se corectează doar după verificare.

Contract: inventar HEAD + diff/căi deja murdare; identificatori COMPLEȚI ai
clipurilor; sursa rezolvată prin calea rendererului, dimensiuni/PTS; export,
sidecar, hash și setări efective. Nu se alege sursa prin primul rezultat glob.
Set minim: 70ca (umăr/apel Discord), 9d2a (video urmărit/Moist), dea939, 02dea,
3e42, 65a9 (Kai), e8fa + 6914 (co-stream), c04e la 3,133 s (mai multe persoane),
d789 (gol de detecție), 6053a598cf06 și b23c14c41495 (subtitrare/ceas).
Prefixele se rezolvă din rapoartele existente; ambiguitatea se refuză, nu se ghicește.

| Probă / fișier raport | Caz | Așteptat |
|---|---|---|
| baseline.md | 02dea/3e42 + copii anterioare | export nou verificabil, backup identificabil, fără rescriere |
| baseline.json | sursă dea939 | calea efectiv randată, nu source.mp4 ales după nume |
| browser-baseline.md | deschide, editează, preview, reload într-un proiect de probe | stare persistentă și erori vizibile, fără modificări în proiectul original |
| visual-cases.json | defecte și controale | timp + ce trebuie păstrat + ce NU este demonstrat |

Acceptare: capturi din browser și cadre din MP4, cu timpi, nu doar coordonate.
Defectul de contor tăiat și lipsurile de încadrare rămân separate de fixul dimensiunilor.
Nu reeticheta materialele vechi drept probe pentru un encode nou.

## 4. B1 — editare, eveniment, rescorare și export fără pierdere (8 unități)

**Fișiere:** `server/workers/clipper_finalize.py`,
`server/routers/clipper_clips.py`, `server/routers/clipper_caption_source.py`,
`server/routers/clipper_reaction.py`, `server/services/clipper/feedback.py`,
`server/services/clipper/clip_mutations.py` (nou, numai dacă necesar pentru apelanții multipli),
`server/tests/test_clipper_rescore_keeps_edits.py`,
`server/tests/test_clipper_clip_caption_source.py`,
`server/tests/test_clipper_mutation_atomicity.py` (nou),
`server/tests/test_clipper_caption_source_migration.py` (nou).

Contract:
1. Reproduce înainte de fix edit→eveniment și edit→claim prin bariere explicite
   între două sesiuni SQLite pe fișier temporar. Nu teste bazate pe sleep sau noroc.
2. Editarea acceptată și evenimentul care o protejează sunt aceeași tranzacție.
   Eșecul evenimentului anulează editarea; no-op nu produce eveniment nou.
   Păstrează API-ul celorlalți apelanți ai feedback.record; fără commit ascuns
   care rupe tranzacția nouă. Verifică PATCH, caption-source, reacție și regenerate.
3. Claim versus edit/rescore are o ordine serială: exportul vede integral
   editarea acceptată, sau editarea pierde și primește 409. Nicio scriere veche
   nu scoate clipul din `exporting`; nicio rescorare nu șterge un export activ.
4. Predicatul comun rămâne comun. Protecția trebuie valabilă la ștergere,
   nu doar la SELECT-ul anterior. Un eveniment acceptat concurent nu poate fi ignorat.
   Dacă rescorarea câștigă și șterge întâi candidatul, editarea refuză explicit.
5. Fără mutex doar în memorie: există două backend-uri pe aceeași DB.
   Folosește tranzacții SQLite/actualizări condiționate; dacă snapshot-ul învechit
   trebuie refuzat, întoarce conflict controlat, nu succes sau 500 brut.
6. Menține status/rank/run/id și toate editările rândurilor păstrate. Auto-exportul
   nu consumă sloturi cu scoruri umane vechi; verifică și originea auto/system.
   Un rollback nu lasă board pe jumătate înlocuit.

| Test | Caz | Așteptat |
|---|---|---|
| test_clipper_mutation_atomicity.py | excepție la feedback după pregătirea editării | nici editare, nici eveniment persistat |
| același | claim înainte / după editare, două conexiuni | 409 sau export cu noua stare; fără stare intermediară |
| același | rescore între citire și salvare; în ambele ordini | păstrare integrală ori refuz explicit al editării |
| același | clip exporting fără feedback, rescore simultan | clipul și exportul activ supraviețuiesc |
| același | reacție, regenerate, PATCH, caption-source; no-op | același contract, fără evenimente fabricate |
| test_clipper_rescore_keeps_edits.py | rejected, legacy NULL, auto, alt proiect, insert eșuat | contractul existent + rollback integral |
| test_clipper_caption_source_migration.py | DB veche, migrare de două ori | coloană NULL implicit, restul valorilor intacte |

Testează funcțiile reale și SQL real; dublează doar coada/encode-ul costisitor.
Un test de cursă trebuie să pice pe implementarea anterioară; salvează dovada.
Nu adăuga o nouă coloană de versiune fără demonstrarea necesității și addendum de schema.

## 5. B2 + A2 — politica proiectului, fără rescorare (B 5 / A 4)

**Problemă verificată:** PATCH settings rescorează orice modificare
`clipper_settings`; setarea de proiect lipsește din UI; moștenirea e opacă.

**B2 fișiere:** `server/routers/clipper.py`, `server/routers/clipper_settings.py`,
`server/routers/clipper_caption_source.py`, `server/services/clipper/serialize.py`,
`server/services/clipper/caption_policy.py`,
`server/tests/test_clipper_project_caption_source.py` (nou),
`server/tests/test_settings_parity.py`, `server/tests/test_clipper_caption_policy.py`.
Helperul tranzacțional B1 rămâne proprietatea lui B.

**A2 fișiere:** `src/app/ai-stream-clipper/[id]/page.tsx`,
`src/components/clipper/clip-editor.tsx`, `src/components/clipper/project-caption-source.tsx` (nou),
`src/types/clipper.ts`, `data/claude-master-20260924/A/caption-policy-browser.md`.

Contract API/UI comun:
- Păstrează PATCH `/projects/{id}/settings`; clasifică DELTA efectivă a cheilor,
  nu simpla prezență a obiectului. Doar `source_has_burned_captions` schimbat:
  zero scoring/analyze/download. Mix cu o setare de scoring: comportamentul
  de rescorare existent, o singură cerere, fără a pierde editările B1.
- Valori strict true/false/null și moștenire clip→proiect→default existent.
  Nu transformăm detectorul în decident și nu schimbăm implicitul de burn.
- Schimbarea proiectului validează TOATE clipurile moștenitoare afectate.
  Dacă pornește burn peste o reacție fără loc: 422 structurat cu clipurile blocante
  și sugestia existentă; tranzacția întreagă refuzată, fără modificări parțiale.
- Clipurile cu override explicit rămân identice. Pentru cele moștenitoare,
  invalidează numai când decizia efectivă de randare se schimbă. Refuză 409 dacă
  un export afectat este activ; păstrează aceeași protecție atomică cu claim.
  Modificarea de proiect nu este feedback uman inventat pe fiecare clip.
- Fișierele exportate rămân pe disc; nu li se actualizează sidecar-ul sau hash-ul.
  UI spune că necesită reexport; exportul următor scrie propria proveniență.
- Expune la citirea clipului/editorului `effective_caption_policy` calculată
  din aceeași funcție ca renderul, cu action și scope normalizat clip/project/default.
  Câmp de prezentare separat: nu schimba dict-ul legacy folosit în fingerprint.
- UI arată ce se va întâmpla efectiv și de unde vine decizia. După reload e identic;
  nu promite că MP4-ul vechi s-a modificat. Erorile păstrează selecția salvată anterior.

| Test / raport | Caz | Așteptat |
|---|---|---|
| test_clipper_project_caption_source.py | true/false/null, șir/1/refuz, no-op | strict; fără rescore; fără evenimente la no-op |
| același | 3 moștenitoare + 2 override-uri, schimbă proiectul | numai deciziile efectiv schimbate invalidate |
| același | reacție fără bandă sau export activ în grup | 422/409; zero scrieri parțiale |
| același | caption-only și caption+scoring | zero versus o rescorare permisă; B1 păstrează editările |
| test_clipper_caption_policy.py | toate combinațiile clip/proiect | prioritate corectă; detectorul nu decide |
| caption-policy-browser.md | moștenire, override, reload, 422, 409 | explicație efectivă corectă, fără UI fals de succes |
| același | preview și MP4 nou burn/suppress | strat prezent/absent în pixeli; fișierul vechi intact |

Acceptare A2 numai după B2; sursa cu text parțial tăiat rămâne problemă de încadrare,
nu se declară reparată prin suprimarea stratului nostru.

## 6. B3 + A3 — revizie oarbă pe o singură rulare (B 3 / A 5)

**Problemă:** `clipper_review.start_session` refuză board-ul mixt cu 409.
Este o protecție corectă, dar UI trebuie să permită alegerea unei cohorte valide.

**B3 fișiere:** `server/routers/clipper_review.py`,
`server/tests/test_clipper_review_cohorts.py` (nou),
`server/tests/test_clipper_run_identity.py`, `server/tests/test_clipper_review_media.py`.
**A3 fișiere:** `src/app/clipper-review/page.tsx`, `src/types/clipper-review.ts`,
`src/components/clipper/review-cohort-picker.tsx` (nou),
`data/claude-master-20260924/A/review-cohorts-browser.md`.

Contract:
- Adaugă GET `/api/clipper/review/cohorts?project_id=...`: rulări existente,
  număr membri și eligibilitate/lipsuri media. Ruta statică precedă `/{session_id}`.
- POST existent acceptă opțional `selection_runs: {project_id: run_id|null}`.
  Dacă este dat, trebuie să specifice exact proiectele cerute. Null alege numai
  cohorta legacy necunoscută; nu amestecă necunoscut cu rulări identificate.
- Fără noul câmp: păstrează comportamentul compatibil, inclusiv 409 pe amestec.
  Nu alege automat „ultima” după id sau scor; nu rescrie run_id al clipurilor păstrate.
- Cohorta trebuie să aibă board-ul cerut de regulile existente, shadow compatibil
  și media capturată validă. Un subset rămas după rescore nu se prezintă drept
  board complet pentru comparația selectoarelor; raportează lipsurile și refuză
  experimentul dacă membrii originali nu pot fi demonstrați din trace-ul rulării.
- UI arată rulările înaintea sesiunii. În timpul vizionării rămân ascunse
  membership-ul experimental, scorurile și clasamentul. Snapshot-ul media rămâne sigilat.

| Test / raport | Caz | Așteptat |
|---|---|---|
| test_clipper_review_cohorts.py | export vechi + editat vechi + board nou | cohorta explicită nouă poate fi evaluată; celelalte nu intră |
| același | run străin, lipsă proiect, shadow nepotrivit, mixt implicit | 4xx explicativ; nimic reetichetat |
| același | legacy numai / legacy mixt; cohortă veche incompletă | necunoscut explicit; fără comparație fals completă |
| test_clipper_review_media.py | MP4 schimbat după sigilare | refuz; fără fallback la alt export |
| review-cohorts-browser.md | alegere, reload, următor, răspuns, rezultate | sesiune utilizabilă; nicio dezvăluire înainte de final |

Nu construim în acest lot arhivarea completă a tuturor board-urilor istorice.
O cohortă incompletă rămâne indisponibilă, cu motiv vizibil.

## 7. B4 + A4 — fișier postabil și limitele automatizării (B 4 / A 6)

**B4 fișiere noi private:** `data/claude-master-20260924/B/audit_deliverables.py`,
`test_audit_deliverables.py`, `deliverables.json`, `framing-diagnosis.md`.
**A4 fișiere noi private:** `data/claude-master-20260924/A/release-cases.json`,
`release-browser.md`, `editorial-review.md`, `blind-frames/` și `videos/`.
Nu modifică rendererul/plannerul în această etapă. B folosește serviciile reale
de export existente; A pregătește exporturi NOI într-un proiect/director de probe.

Contract de audit:
- Identifică sursa efectivă, snapshot-ul intrărilor, candidatul și MP4-ul exact.
  Recalculează independent hash/probe/decode, nu doar citește output_identity.
- Verifică schema fingerprint declarată; v1 rămâne limitat, v2 mismatch nu cade
  înapoi la v1. Render_record descrie comanda efectivă; nu reconstruim din setări.
- ASS-ul și intervalele lui sunt relevante doar dacă filtrul chiar l-a folosit.
  Captions după trim/drop/speed se verifică pe ceasul exportului și în video.
- Păstrează separate: test de geometrie, măsurare pe artefact, verdict vizual.
  Cadru necitibil = unavailable; față nedetectată nu înseamnă subiect absent.
- Vizionare integrală cu audio: început inteligibil, payoff și final complete,
  subiect/material urmărit păstrat, text lizibil fără dublare/tăiere, fără salturi
  deranjante, audio sincron, cadru vertical și descărcare funcțională.
- Înregistrează timpul de intervenție manuală și durata de export, hardware,
  setări, costul LLM observat. Nu inventăm praguri de acceptare din aceste valori.

| Test / probă | Caz | Așteptat |
|---|---|---|
| test_audit_deliverables.py | MP4 alterat, lipsă, decode eșuat, v2 mismatch | fail/unavailable precis; niciun pass din sidecar singur |
| același | ASS orfan, suppress, timestamp după trim/speed | strat efectiv și ceas corect, fără fals captions fail |
| release-browser.md | sursă locală → scor → editare → preview → export → download | fișier complet redabil din UI; refresh păstrează starea |
| același | anulare/retry, 422, export activ, rescore după editare | recuperare fără pierdere/duplicare, folosind comportamentul B1 |
| editorial-review.md | toate contraexemplele A1 + controale | verdict pe clip integral, defecte și limite numite |
| framing-diagnosis.md | 70ca umăr+apel; 9d2a actor versus webcam; 65a9 Kai | separă detecția, conturul, identitatea și ținta editorială |
| același | c04e la 3,133 s; text karaoke; ceasul prezentat | fețele singure nu devin adevăr despre întregul subiect |

**Selecție și granițe:** A4 folosește rubrica existentă de blind review și notează
separat „merită clipul” versus „arată bine”. Un export impecabil al unui moment
fără context/payoff nu trece. Compară modurile doar în cohorte valide B3;
story_v2/content_aware rămân shadow până la evaluarea prevăzută, nu sunt deblocate aici.
B4 distinge replanificarea de rescorare: o problemă de început/final cere probe
despre selecția ferestrei, nu încă un encode al aceleiași ferestre.

**Încadrare automată — următorul gate, nu o soluție pretins cunoscută:**
1. B4 identifică prima cauză demonstrabilă per contraexemplu, cu source/PTS corecte;
   nu repetă căutarea acelorași praguri sau reuniuni respinse.
2. A4 adnotează ținta și conturul pe cadre sursă, fără crop/verdict în vedere;
   verifică adnotarea prin replay și intervale de incertitudine. Subtitrarea = rând
   întreg, inclusiv cuvintele estompate; ținta poate fi mână/obiect, nu doar față.
3. Numai dacă există o metodă nouă testabilă: Codex scrie addendum cu fișierele
   exacte de producție, înainte de implementare. Nu autorizăm acum atingerea
   fișierelor dirty sau activarea detectorilor prin simplul fapt că sunt disponibili.
4. Candidat înghețat, cadre nefolosite la construcție, plus surse/layout-uri noi
   pentru generalizare; scene și tranziții acoperite separat. Eșantionarea nu
   dovedește fiecare cadru. Orice reglaj după hold-out îl face set de dezvoltare.
5. Export pereche prin aceeași cale; rezolvă umărul ȘI apelul 70ca fără regresii
   pe controale. Fără candidat acceptat, editorul manual rămâne soluția utilizabilă.

Nu estimăm „100% automat” din trecerea acestui lot. Pentru acel obiectiv rămân
încadrarea generalizată, semantica momentului și lizibilitatea per interval;
addendum-urile vor trata defecte măsurate, nu o rescriere speculativă a motorului.

## 8. Comenzi, integrare și dovezi de predare

Fiecare executant rulează numai testele lotului său și dependențele afectate.
Din `server`, pentru fiecare fișier de test enumerat în tabel:

```powershell
Remove-Item Env:CLIPFORGE_DATA_DIR -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_clipper_mutation_atomicity.py tests/test_clipper_rescore_keeps_edits.py tests/test_clipper_clip_caption_source.py tests/test_clipper_caption_source_migration.py
```

B2 înlocuiește lista cu `test_clipper_project_caption_source.py`,
`test_clipper_caption_policy.py`, `test_settings_parity.py`.
B3: `test_clipper_review_cohorts.py`, `test_clipper_run_identity.py`,
`test_clipper_review_media.py`, `test_clipper_blind_review.py`.
B4, din rădăcină:
`server/.venv/Scripts/python.exe -m pytest -q data/claude-master-20260924/B/test_audit_deliverables.py`.
Auditul privat trebuie să accepte `--manifest` și `--output`; B salvează comanda
exactă, manifestul și codul de ieșire. Nu folosi o căutare implicită a corpusului.

A, din rădăcină: `npm run typecheck`; `npx eslint` urmat numai de fișierele TS/TSX
schimbate, fiecare cale cu paranteze drepte între ghilimele. Compară erorile cu
baseline; erorile preexistente din alte pagini se raportează, nu se „repară” aici.
Rulează testul existent dacă e afectată selecția reacției:
`node --test src/components/clipper/reaction-selection.test.mjs`.
Probe browser pe instanță cu DB de test și porturi distincte; nu scrie datele
probe în DB reală. Configurația explicită de izolare se înregistrează în raport.

**Un singur punct de integrare finală:** B predă, încetează editările; A verifică
contractele UI/API reale și îngheață arborele; apoi A rulează O SINGURĂ suită completă:

```powershell
Set-Location F:\ClipForge\server
Remove-Item Env:CLIPFORGE_DATA_DIR -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest -q --tb=short --deselect=tests/test_tiktok_transform.py::test_list_endpoint_ok --deselect=tests/test_tiktok_transform.py::test_create_rejects_invalid_url
```

Nu repetăm o suită deja verde fără schimbări sau problemă nouă. Dacă pică, reparație
țintită + teste relevante; orice rerulare completă ulterioară își notează motivul.
Nu folosi testele pe corpus viu ca presupunere că schema tuturor exporturilor e aceeași.

Predarea fiecărui lot în `data/claude-master-20260924/{A|B}/<lot>-result.md`:
fișiere și hash/diff, teste+exit, dovada negativă înainte de fix, artefacte cu timpi,
ce rămâne necunoscut, fără procese de verificare încă în fundal. Codex verifică
fragmentele decisive și poate respinge lotul chiar dacă toate testele trec.
A actualizează harta pentru fișiere noi și handover-ul la starea efectivă;
corectează mențiunile 1B „în validare” și sursa dea939 greșită din planul vechi.

## 9. Autorizări și definiția livrării

Planul autorizează lucrul reversibil în domeniul de mai sus și probele izolate.
Commit: numai după acordul utilizatorului, un lot pe commit, căi exacte, hooks active.
Suprascrierea MP4-urilor existente: acord separat pentru clipurile numite, backup
fișiere + rând DB; aprobările istorice nu autorizează o rerandare a întregului corpus.
Nu push/publicare și nu vizionare umană inventată în numele utilizatorului.

Produs utilizabil pentru postare = flux complet A4 + zero defecte tehnice blocante
pe lotul acceptat + verdict editorial uman pe fișierul final. Necesitatea unei
încadrări manuale se declară; nu se ascunde sub o rată de succes automată.
Automatizare generală = gate separat pe surse noi, cu rata de corecție manuală,
defecte și cost raportate. Nu există azi o măsurătoare care să susțină „100%”.

**Pornire imediată:** A1 și B1 în paralel. Nu aștepta noi aprobări pentru teste sau
probele private. Orice alegere care schimbă aceste contracte se aduce la Codex cu
contraexemplul și variantele; nu se rezolvă prin relaxarea testului.

PLAN GATA
