# Continuitatea feței în goluri scurte — 21 septembrie 2026

Punct de plecare: `351fabe`. Implementare cu Claude Code, verificare independentă
de Codex. Detectorul implicit rămâne Haar. O poziție urmărită prin mișcare nu
devine detecție de față, dovadă de identitate sau acoperire temporală completă.

## Defectul de la care pornim

Speed `d789060e273d`, sursa `386.47..404.37`, export de 17,9 s: cadrul la
16,6 s taie persoana în dreapta și după protecția de încadrare din lotul anterior.
Ultima detecție Haar este la timpul cerut 400,97 / 14,50 relativ, cadrul 145
al ferestrei recodate: o cutie `[44,17,25,25]` în proxy-ul 480×270. Cele 13
probe de la 401,22 la 404,22 au `state: empty`. Nu sunt cadre necitibile și
nici nu demonstrează că persoana a dispărut; aceasta este vizibilă în sursă.

La alte momente există fețe în materialul urmărit, uneori mai mari decât fața
creatorului. O propunere nouă nu are voie să schimbe alegerea țintei pe ascuns.
Datele brute sunt cele din `data/claude-auto-framing/paired/`, nu citirea unui
raport de provenance drept măsurătoare de calitate.

## Prototip respins și proba independentă

Primul prototip al lui Claude a raportat 20 de poziții și acoperire până la
16,6 s. Raportul a fost respins: ultima poziție era la 16,5 s, punctele erau o
grilă recreată după fiecare pas, deplasarea era rotunjită la fiecare cadru,
iar timpii decodați ai ferestrei erau promovați în timpi ai sursei. În plus,
desenarea putea lua propunerea cadrului vecin dacă cea exactă lipsea.
Acestea puteau ascunde deriva și făceau etichetele mai puternice decât proba.

`data/claude-face-continuity/independent_lk.py` este o verificare separată:
30 de colțuri măsurate în cutia inițială, punctele originale păstrate, deplasare
în virgulă mobilă și filtrare individuală înainte/înapoi. La cadrul 165 rămân
27 de puncte, cutia aproximativ `[72.36,12.98,25,25]`. Cadrele 157 și 165
inspectate arată puncte pe față, nu o continuare în decor. Aceasta stabilește
fezabilitatea unei continuări scurte pe acest exemplu, nu calibrarea trackerului.

## Contractul

Doar un gol de detecții curate poate primi propuneri de mișcare, pornind de
la o detecție unică, cu adresă verificată. Datele brute rămân neschimbate.
Punctele nu sunt înlocuite cu puncte noi; erorile de citire, pierderea suportului,
schimbarea locală incompatibilă, ieșirea din cadru și limita de două secunde
opresc continuarea. Limitele sunt alegeri inginerești, nu praguri calibrate.

Plannerul trebuie să recunoască exact sămânța în observațiile reale pe care
le-a ales deja. Centrul ei trebuie să fie în camera existentă; mișcarea ulterioară
poate lărgi acea cameră. Nu modifică alegerea feței dominante, prezența brută,
numărul de fețe folosit de captions sau scara globală. Ancora fixă și camera
de joc păstrează protecțiile existente. Refuzurile și proveniența ajung în plan.

## Rezultatul verificării

Verificare finală: 23 septembrie 2026. Lotul a început pe 21 septembrie (acest
fișier). Codex a **acceptat lotul limitat** de continuitate a golurilor. Acceptarea
nu privește calitatea generală a clipurilor și nici publicarea corpusului.
Raportul complet este local:
[`data/claude-face-continuity/VERIFICATION_RESULT.md`](../../data/claude-face-continuity/VERIFICATION_RESULT.md).
Exportul candidat Speed:
[`paired/d789060e273d/haar/candidate/d789060e273d.mp4`](../../data/claude-face-continuity/paired/d789060e273d/haar/candidate/d789060e273d.mp4).

**Teste.** Suita completă a serverului: 2.402 teste trecute, cu cele 2 teste TikTok
cunoscute deselectate și `CLIPFORGE_DATA_DIR` nesetat. Înainte de rularea completă
trecuseră 217 teste focalizate.

**Exporturi.** 4 perechi, adică 8 MP4-uri, randate prin `render_export`. Toate au
decodare completă, identitate și amprentă valide. Cele 390 de fișiere de export
existente (MP4, ASS, sidecar-uri din directoarele de export) sunt neschimbate. Granițele de tăietură sunt identice (42/42) și nu apare niciun fit nou.
MP4-urile baseline sunt identice byte cu byte cu exporturile `351fabe` acceptate anterior
(`data/claude-auto-framing/paired/<clip>/haar/candidate`). Hash-urile de cod
din rapoartele baseline descriu arborele de lucru, nu dovedesc plannerul înghețat.

| clip | folosite / refuzate total | geometrie schimbată | scară minimă |
|---|---|---|---|
| Speed `d789060e273d` | 16 / 14 | 8,255 s | 0,594 (cu 40,6% mai mic) |
| `6914b77525e4` (control) | 3 / 5 | 0 (identic byte cu byte) | 1,0 |
| Minecraft `e8fa6b35ea66` | 11 / 40 | 6,625 s | 0,950 |
| dialog `c04e7960179b` (control) | 5 / 2 | 0 (identic byte cu byte) | 1,0 |

Pentru fiecare fereastră s-a verificat că suma categoriilor de refuz este egală cu
lungimea listei de refuzuri. Un refuz nu înseamnă o observație lipsă: sămânța
nepotrivită își păstrează motivul. Pe Speed, cel mai mare salt de scară între shot-uri
vecine scade de la 1,413 la 1,299.

**Subtitrare.** Hash-ul real al fișierului ASS și filtrul de subtitrare sunt identice
în fiecare pereche. Perechea fără ASS are filtrul oprit în ambele moduri.
Hash-urile argv diferă, deci nu sunt declarate identice. Geometria subtitrării
rămâne aceeași, dar crop-ul mai larg arată mai mult UI din sursă.

**Inspecție Codex.** Codex a verificat 8 planșe (52 de cadre) și 8 cadre fără etichete.
Pe Speed, fața și capul sunt complet în cadru la 15,7, 16,1 și 16,6 s, față de
tăierea la marginea din dreapta în baseline. 16,1 și 16,6 sunt aceleași momente,
în detaliu mai bun, nu un set de verificare nou. La 9,3 s pe Speed și 32,0 s pe
Minecraft se vede o lărgire mică, fără o pierdere nouă evidentă a capului.
Rămân tăierea unui participant la 3,133 s în dialog, aglomerarea joc/chat și
costul de scară. Nu există aprobare editorială video/audio: au fost inspectate
doar cadre statice și dovezile de granițe, scară și identitate.

**Limite.** Propunerile de mișcare nu sunt detecții reale, nu dovedesc identitatea
și nu dau extinderea reală a capului. Bugetul de 2 s nu este calibrat. Prezența
brută, detecțiile pentru subtitrare și detectorul global rămân neschimbate
(Haar implicit, YuNet opțional). Sidecar-ul final păstrează adresele sămânței și
țintei folosite, calitatea, timpul cerut din sursă și PTS-ul ferestrei, separat.

**Rămân deschise:** identitatea când sunt mai multe persoane, golurile lungi,
salturile de scară existente, subtitrările și UI-ul din sursă. Nu se declară
pregătire 100%.

**Flux de lucru.** De la 23 septembrie 2026, Codex orchestrează și analizează, iar
Claude execută modificările și testele. Editările anterioare ale lui Codex nu
sunt atribuite retroactiv lui Claude. Rulările finale au folosit Opus 5.5, fără
epuizarea cotei. Nu se afirmă un avantaj de cost al vreunui model pe acest repo.
