# Clipper — plan de continuare, 24 septembrie 2026

## Contract și punct de reluare

Cererea utilizatorului: Codex planifică și validează; Claude Code și un agent
economic implementează. Planul se salvează ÎNAINTE de implementare. Loturi mici,
probe refolosite, fără re-encode de corpus sau consum nelimitat de agenți.

HEAD la început: `29a4441` (continuitatea feței). Arborele are schimbări străine:
NU folosi reset/restore pe tot repo-ul, git add -A sau commit global.
Instrucțiuni: `CLAUDE.md`. Stare: `docs/handover/areas/clipper/CURRENT.md`.

## Validarea muncii Claude înainte de acest plan

- Lotul ceasului mișcării: corect matematic, dar NU acceptat editorial.
  Este încă activ în working tree. Pe c04e la 14,95 s trece de la persoane la TV;
  pe e8fa la 37,52 s de la persoană la chat/webcam. Testele nu infirmă regresia.
- Filtrul peak/mean este deja scos din producție: `data/claude-motion-filter/`.
- `spatial_only` este privat. Cele patru layout-uri suplimentare sunt în
  `data/claude-game-region/layouts/RESULT.md`; controlul lor folosește working
  tree CU ceasul modificat, nu HEAD. Nu echivala aceste controale cu produsul acceptat.
- Codex a deschis sursa cu riglă 70ca la 19,30 s și cele trei perechi MP4 din
  shot-ul 18,12–20,40 s: umărul intră în fereastra nouă; apelul vizibil în vechiul
  export dispare. Contraexemplu confirmat, fără a certifica fiecare cadru al clipului.
- Codex a deschis 65a9 la 3,60 s: există webcamul din dreapta nemăsurat și cel
  din stânga depășește cutia roșie. Poziția centrală favorabilă nu validează detectorul.
- 2.424 teste / 2 deselectate este rezultatul suitei lotului de ceas (log existent).
  Repetabilitatea celor 8 MP4 și inventarul 390 de fișiere urmează să fie reverificate.
- Verificarea compară geometria livrată cu propuneri de webcam incomplete: poate
  demonstra disjuncția de acele dreptunghiuri, nu absența webcamului din imagine.
  `prototype/spatial.py` elimină refuzurile din numitor; `layouts/probe.py` a
  adăugat numărătoarea refuzurilor și un rând fit separat. Acesta din urmă este
  un progres real, dar niciunul nu certifică imaginea. Cer cazuri negative prin CLI.

## Lot 1 — bază stabilă și verificare care nu ascunde lipsurile

### 1A. Claude Code: izolează ceasul neacceptat

1. Salvează patch binar + copii integrale + SHA256, HEAD și lista exactă în
   `data/claude-motion-clock/quarantine-20260924/` ÎNAINTE de restaurare.
2. Fișiere de cod deținute exclusiv de acest lot:
   `scripts/render_dynamic_clip.py`, `server/services/clipper/dynamic_edit.py`,
   `server/workers/clipper_render_plan.py`, `server/tests/test_clipper_dynamic_export.py`.
   Verifică diff-ul; dacă apar schimbări străine, oprește restaurarea acelui fișier.
3. Păstrează în arhivă și fișierele noi `dynamic_timing.py`,
   `test_clipper_motion_clock.py`, apoi scoate-le din calea activă după verificarea
   hash-urilor. Restabilește DOAR cele patru fișiere la `29a4441`.
4. Păstrează PRP-ul ceasului și istoricul probelor; actualizează harta/handovers
   cu starea reală: bug cunoscut, fix reproductibil izolat, nu remediat în produs.
   Nu pretinde că revenirea rezolvă selecția jocului sau dialogul.
5. Rulează testele focalizate și suita completă. Fără commit până la validarea Codex.

### 1B. Agent economic: audit privat al probelor deja randate

Scrie doar în `data/claude-game-region/verification/`. Nu modifica producția sau
rapoartele istorice și nu re-randa. Citește cele 4 perechi `layouts/` și raportul
`prototype/spatial_report.json` pentru context. Auditul nou citește shot-urile și
`evidence_map.crop_window`, nu boolean-ul `clear` deja raportat.

Inventariază separat: total game, crop citibil/disjunct, crop suprapus, fit,
refuz/necitibil. Numără înainte de filtrare. Comparația fit se face pe intervale
și geometrie/compoziție efectivă. `geometry_status` și `visual_status` sunt axe
separate; pixelii neinspectați rămân `unreviewed`, chiar la geometrie disjunctă.
Un raport de geometrie nu emite `accepted`. `main()` iese non-zero când auditul
e incomplet/refuzat, gol, are o suprapunere sau fit schimbat. Nu promova
`unavailable`/`not_exercised` la succes. Un succes pur geometric se numește explicit.

Teste necesare prin funcții și prin `main()`:

| Caz | Rezultat cerut |
|---|---|
| un crop disjunct + unul necitibil | total 2, refuz 1, non-zero |
| toate lipsă / listă goală | neexercitat/incomplet, non-zero |
| fit identic | rând separat, nu crop refuzat, nu webcam-free |
| fit schimbat / dispare | diferență detectată, non-zero |
| crop cu ancoră diferită de rect | folosește fereastra rendererului |
| timeline mobil fără footprint unic | refuz explicit, rămâne în numitor |
| 70ca disjunct de 648 px, umăr vizibil dincolo | geometrie disjunctă NU acceptare vizuală |
| 65a9 webcam Kai lipsă | absența observației NU dovadă că nu există webcam |

Reverifică SHA256 pentru 8 MP4 față de `layouts/first_run_sha.json`, identitatea
și fingerprint din sidecar; raportează exact lipsurile. Fără import de loader DB
cu efecte secundare; fără inițializare DB. Agentul nu consumă citirea a sute de cadre.

## Lot 2 — observație locală: experiment limitat, fără activare

Claude Code, după 1A: diagnostic de fezabilitate, maximum o metodă existentă,
fără model nou descărcat, API plătit, prag ajustat până iese verde sau 280 de cutii
adnotate manual. Citește `content_facecam.py` și `scene_independence` înainte să
propui ce pot măsura. Scrie în `data/claude-game-region/local-observation/`.

Prima întrebare: poate observația locală să RESPINGĂ contraexemplele 70ca/65a9,
fără să respingă drept webcam fețele actorilor din materialul urmărit pe 9d2a?
O față stabilă nu certifică un webcam, iar absența feței nu certifică spațiu liber.
Scene-independence între două regiuni nu dovedește singură conturul sau sensul lor.

Folosește cadre identificate prin index/PTS, legate de sursă și interval, pentru:
70ca webcam fără ramă; 65a9 camera lipsă; 9d2a fețe din conținut; e8fa co-stream;
6914 control; dea939 fit; c04e dialog fără webcam măsurat. Dacă metoda necesită
mai multe date decât există, rezultatul corect este limită documentată, nu prag nou.
Separă localizarea camerelor de ținta editorială: apelul Discord trebuie păstrat;
o fereastră goală poate fi liberă de webcam și totuși inutilă.

Livrabil: raport compact cu numitori, exemple de reușită/eșec/necunoscut, cost și
propunere de pas următor. Cel mult o probă video scurtă dacă există candidat
defensabil; altfel nu consuma encode pentru un candidat deja infirmat.

## Lot 3 — integrare numai după un candidat acceptabil

Nu este autorizată promovarea automată a `spatial_only` pe baza vechilor dreptunghiuri.
Codex decide din lotul 2 dacă există dovadă pentru un domeniu limitat sau dacă
editorul manual de reacție existent rămâne soluția practică pentru acel material.

La un candidat nou: modul mic fără DB; observație/propunere/decizie separate;
identitatea sursei, ceasul și acoperirea temporală explicite; lipsa și conflictul
refuză schimbarea, fără etichetă falsă „bun”. Două capete ale unui interval nu
dovedesc conținut constant între ele. Nu transforma numele `game` în adevăr semantic.

Teste de integrare: dimensiuni invalide, NaN/bool, coordonate în afara sursei,
segmente suprapuse/conflictuale, webcam apărut în mijloc, sursă schimbată, lipsă
acoperire, marginile rotunjite de renderer, reacție manuală neatinsă, fețe și
captions identice unde nu sunt ținta lotului. Păstrează raw observations.

Probe înainte/după față de HEAD acceptat, aceleași intrări, cale comună de encode.
Verifică MP4 decodat integral, text efectiv ars, scară, momentele tăieturilor,
obiectul relevant (inclusiv apelul), nu doar sidecar. Cazuri de control obligatorii:
Speed, Minecraft, Discord, dialog; păstrează toate exporturile existente.
Reintroducerea ceasului este o schimbare separată; cere aceleași probe editoriale.

## După încadrare: restul produsului

1. Continuitate/identitate la mai multe persoane și cazul dialogului c04e 3,133 s.
2. Captions: evită dublarea și textul sursei tăiat; verificare pe export, nu bandă
   cumulată; finalizarea probelor locale înainte de aplicare generală.
3. Selecție și început/final: propoziție/poantă completă, sens și audio, fără a
   confunda lipsa unui detector cu dovada absenței defectului.
4. Parcurs complet din UI pe surse distincte: alegere → editare → export → fișier
   vizionabil/postabil; eșecul clar și reluabil. Suita verde nu este verdict editorial.

## Comenzi și disciplină de reluare

Python: `F:\ClipForge\server\.venv\Scripts\python.exe`.
Teste din `server`, `CLIPFORGE_DATA_DIR` eliminat din mediu:

```powershell
Remove-Item Env:CLIPFORGE_DATA_DIR -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest -q --tb=short --deselect=tests/test_tiktok_transform.py::test_list_endpoint_ok --deselect=tests/test_tiktok_transform.py::test_create_rejects_invalid_url
```

Exact două teste TikTok cunoscute sunt excluse, nu întregul fișier. Nu apela
`init_db` pe DB de producție. Fiecare fișier modificat/nou maximum 500 linii.
Claude: executabil `C:\Users\vlado\.local\bin\claude.exe`; sesiunea cu context
`628536bd-be3e-4fe8-bf0c-d9fac4e8fa49`; MCP gol
`data/claude-xqc-reference/no-mcp.json`. Lot simplu: Sonnet, buget de ture explicit.
Agent economic: context scurt, numai acest plan și fișierele repartizate.
Codex verifică diff, testele negative și artefactele; apoi actualizează acest
plan cu starea exactă și ambele handovers. Commit doar fișierele lotului acceptat.

## Execuție și punct actual de reluare

Planul a fost salvat înainte de delegare. Tentativa anterioară de integrare,
oprită de limita verificării automate de aprobare, nu a executat modificări.
Acest plan o înlocuiește.

- **1A terminat și verificat de Codex.** Cele patru fișiere active sunt identice
  inclusiv la nivel de octet cu `29a4441`. Șase copii din arhivă au hash-urile
  înregistrate înainte de revenire. Suită rulată de Codex: **2.402 passed,
  2 deselected, exit 0**, 199,53 s. Scăderea de la 2.424 este scoaterea testelor
  experimentului odată cu codul lui, nu ignorarea unor teste care pică.
  `data/claude-motion-clock/quarantine-20260924/RESULT.md`.
  Codex a recalculat și verificat toate cele 390 de fișiere de export: neschimbate.
- **1B în validare.** Prima implementare Luna a citit greșit contractele; apoi a
  comparat coordonate de canvas cu coordonate de sursă. Codex a refuzat-o și a
  cerut corecții și teste negative. Sol finalizează auditul privat; rezultatul
  nu devine valid doar fiindcă există `verification/audit.py` pe disc.
  **Actualizare 24 sept., seara:** 1B e închis ca instrument (11 teste, 8 hash-uri
  verificate), conform `PRPs/clipper-master-plan-2026-09-24.md` §1. Nu certifică
  încadrarea automată.
- **2 terminat ca experiment, candidat respins.** Claude Code a rulat detectorul
  existent pe 40 de cadre din fiecare dintre 7 clipuri: 280 citiri, 0 refuzuri.
  Recuperarea camerei Kai pe 65a9 este reală în cadrele inspectate, dar detectorul
  pierde o cameră existentă pe 9d2a și dea939. Pe 70ca, conturul lui Speed rămâne
  în afara dreptunghiului. Nici reuniunea propunerilor locale cu cele vechi nu
  rezolvă asta. Cauza celor două omisiuni nu a fost măsurată.
  `data/claude-game-region/local-observation/RESULT.md`.
- Codex a verificat imaginile și a cerut corectarea afirmației inițiale false
  că dreptunghiul local ține tot umărul. Au fost corectate și dimensiunile:
  dea939 are sursă 854×480, c04e 3840×2160; raportul inițial spunea ×4 pentru toate.
  **Corecție (A1, 24 sept.):** 854×480 este fișierul de ANALIZĂ al dea939
  (`slice4h00test/source/source.mp4`). Sursa pe care o randează exportul
  (`project.video_path`) e `data/testclip/slice4h_hd.mp4`, 1920×1080
  (`data/claude-master-20260924/A/baseline.json`).
  Coordonatele logice folosite de renderer nu trebuie redenumite pixeli ai
  fișierului original. Transformările au domeniul precizat în raport.
- Samplerul a fost corectat la intervalul `[start,end)` și citește indexul real
  înainte de fiecare cadru. **6 cazuri de test trec**, rerulate de Codex; toate
  cele 280 de PTS din raport sunt sub capătul exclusiv al clipului. Acest control
  de adresare nu dovedește acoperire temporală sau adevărul contururilor.

**Următoarea decizie:** nu mai repeta aceeași căutare de praguri sau reuniuni de
dreptunghiuri. Nici `spatial_only`, nici detectorul local nu sunt promovate.
Pentru următorul lot de implementare, separă explicit observația unei camere de
alegerea conținutului care merită păstrat și de geometria conturului. Orice metodă
nouă trebuie să rezolve întâi cele două contraexemple salvate (umăr 70ca și apelul
Discord pierdut), apoi controalele. Cât timp acest lucru lipsește, editorul manual
de reacție existent este calea utilizabilă; nu eticheta ieșirea automată ca sigură.

Economie verificată în această rundă: sesiunile Claude noi primesc planul și
fișierele relevante, nu cele ~334k tokeni ai sesiunii istorice. Nu încheia o
sesiune CLI după lansarea testelor în fundal: primul Sonnet a ieșit înaintea
rezultatului. Așteaptă procesul și salvează codul de ieșire. Sarcinile de geometrie
și contracte cer cel puțin un agent capabil să urmărească transformările; un model
mai ieftin care cere trei rescrieri consumă și verificarea coordonatorului.
