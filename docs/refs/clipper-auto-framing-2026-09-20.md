# Încadrarea automată — 20 septembrie 2026

Lot implementat cu Claude Code, verificat separat de Codex. Punctul de plecare
este `e3718b2`. Detectorul implicit rămâne Haar; YuNet este folosit explicit în
probe. Acest lot nu automatizează selecția regiunilor pentru montajul de reacție.

## Defectul și contractul

În proba Speed `d789060e273d`, Haar furniza cutii pătrate de circa 92 px sursă;
YuNet furniza lățime mediană 64 px și înălțime 84 px pentru zona creatorului.
Plannerul arunca înălțimea și folosea lățimea ca scară de zoom. Exportul YuNet
din lotul B2 taie părul la 8 s și arde textul peste față. Cadrul original citit
la index 23668 / 394.4667 s arată capul întreg în sursă.

`framing_span` separă scara editorială de lățimea observată: folosește latura
mai mare a cutiei, cu aceeași scară proxy→sursă pe fiecare axă, pe observațiile
deja alese de planner. Nu schimbă alegerea feței, geometria camerei de joc sau
constantele treptelor de zoom. Nu este o măsurătoare a întregului cap.

Protecția locală păstrează încadrarea existentă și propunerile eligibile din
interiorul shot-ului într-o singură fereastră. Push/snap/shake sunt oprite dacă
ar anula lărgirea. Ferestrele sunt comparate după geometria rendererului;
dreptunghiul din plan poate diferi de fereastra livrată. Timpii se compară pe
ceasul sursei, fără scăderea care transforma limita 2 s în 1.9999999999999998.

## De ce protecția este limitată

Prima variantă unea toate propunerile alese de vechiul filtru dominant. Pe
Minecraft `e8fa6b35ea66`, aceasta cerea fit pe intervalele 5.13..7.74 și
31.07..33.49. Aceasta a fost o constatare pe PLAN, nu pe un export al variantei
respinse. Sursa originală la 7 s confirmă falsul pozitiv: cutia proxy
`[299,41,80,80]` acoperă peretele jocului; fața reală este `[397,31,27,27]`.

Contractul a fost restrâns: o propunere poate lărgi fereastra numai dacă centrul
observației se află deja în fereastra livrată a încadrării alese. Nu se adaugă un
nou prag de distanță. Excluderea nu declară observația falsă; mecanismul nu știe
să urmărească identitatea unei persoane care părăsește acea fereastră. Garda
existentă pentru dezacordul pozițiilor rulează înaintea acestei protecții.

## Verificare independentă

Contraexemplele adăugate de Codex verifică separat camera de joc, asocierea
exactă a observației, timpi egali/apropiați, date lipsă/invalide și refuzul unei
geometrii necitibile. O recuperare după timp cu toleranță putea lua înălțimea
altei fețe; fallback-ul pătrat inventa o observație. Ambele au fost respinse.
Un caz concret de rotunjire avea marginea dreaptă 712 în plan și 713 în cropul
livrat. Comparația exclusiv între dreptunghiurile planului îl rata.

Testul video al țintei mobile encodează și decodează un MP4 și verifică întregul
pătrat colorat la 0.10 și 1.85 s, între observațiile plannerului. Acesta este
separat de testele geometrice, care nu reprezintă vizionarea unui export real.

Probele reale se află în `data/claude-auto-framing/paired/`, cu intrări locale
reutilizate identic între variante, baseline din codul commitului menționat și
sidecar-uri produse de `render_export`. Lotul cuprinde Speed `d789060e273d`
(17.9 s), alt fragment Speed `6914b77525e4` (15 s), Minecraft `e8fa6b35ea66`
(39.65 s) și dialogul `c04e7960179b` (18.52 s). Primele două au variante Haar
și YuNet, celelalte două sunt controale Haar. Variantele sunt probe separate,
nu înlocuiesc exporturile utilizatorului.

## Rezultatul final

**Acceptat ca protecție limitată de încadrare, nu ca finalizare a încadrării
automate.** Sunt 12 MP4-uri (6 perechi), toate decodate integral, 1080×1920,
cu audio, identitate de ieșire și fingerprint v2 verificate. Hash-urile celor
patru fișiere de producție din probe coincid cu codul acceptat. Cele 390 de
fișiere preexistente din `exports/` au aceleași hash-uri după randări.

Codex a inspectat 80 de cadre distincte din variantele pereche, la 26 de momente
alese înaintea randării: 8 pe primul Speed și câte 6 pe celelalte clipuri.
Încă 12 cadre verifică ambele părți ale celor trei granițe descrise mai jos.
Adresele sunt în `frames/addresses.json`, `frames/haar-addresses.json` și
`paired/boundary-addresses.json`. Acesta este un eșantion vizual; nu reprezintă
vizionarea continuă a tuturor cadrelor sau evaluarea sunetului.

### Ce arată efectiv cadrele

- Speed `d789060e273d`, YuNet: la 8 s noul export păstrează părul și mută textul
  sub față; la 16,6 s păstrează persoana care ieșea prin dreapta vechii încadrări.
  Costul este mai mult joc/chat/HUD în imagine și persoana mai mică. La 15 s
  textul nostru se suprapune cu contorul din sursă. Evitarea feței nu înseamnă
  evitarea tuturor textelor sursei; asta rămâne nerezolvat.
- Același Speed, Haar: lărgirea ajută la 5,8 și 8 s, dar la 16,6 s fața rămâne
  tăiată în dreapta. Observațiile locale lipsesc; această protecție nu inventează
  urmărirea persoanei și nu rezolvă limitarea detectorului.
- Speed `6914b77525e4`: YuNet renunță la o parte din zoomul excesiv, dar la 5,8 s
  părul rămâne lipit de marginea de sus. Haar se schimbă puțin. La 8,15 și 11,4 s
  imaginile jocului rămân identice vizual. Acest clip nu arde stratul nostru de
  captions, deci nu validează poziționarea subtitrării. Negrul vizibil în partea
  de jos aparține încadrării sursei; lărgirea îl poate face mai prezent.
- Minecraft `e8fa6b35ea66`: la 5,8 s capul tăiat anterior este păstrat, fără fit
  pe întregul ecran. Persoana este mai mică și intră mai mult joc. Raportul
  captions devine `no_clear_position_in_observations`, față de o poziție găsită
  anterior: cutiile sunt propuneri de detector, inclusiv false pozitive, iar
  această schimbare nu dovedește nici suprapunere vizibilă, nici absența ei.
- Dialog `c04e7960179b`: deschiderea păstrează mai mult din femeie; la 3,15 s
  bărbatul este încă tăiat în stânga în ambele variante. Noul cadru larg de la
  început produce un salt suplimentar de scară la 1,32 s, vizibil în cadrele
  decodate de o parte și de alta. Nu declarăm montajul uniform mai bun.

### Timpii și costul de scară

Toate cele **57 de granițe** coincid exact între perechi; camerele alese și
duratele shot-urilor coincid. Ferestrele camerelor de joc sunt neschimbate și
nu apare niciun shot fit nou. Acestea sunt comparații ale geometriei folosite
de renderer, coroborate cu MP4-urile; nu sunt măsurători ale dimensiunii reale
a feței. „Scară” este raportul lățimilor ferestrelor înainte/după, pe shot-urile
de față: 0,67 înseamnă o mărire liniară de circa două treimi din cea anterioară.
Mediana este pe shot-uri, nu ponderată cu durata.

| Clip / detector | Granițe egale | Scară min / mediană / max | Salt maxim înainte → după | Perechi ≥1,5× înainte → după |
|---|---:|---|---|---|
| d789 / Haar | 8/8 | 0,67 / 0,93 / 1,00 | 1,25 → 1,41 | 0 → 0 |
| d789 / YuNet | 8/8 | 0,46 / 0,67 / 0,72 | 1,24 → 1,26 | 0 → 0 |
| 6914 / Haar | 7/7 | 0,90 / 0,94 / 1,00 | 3,62 → 3,34 | 4 → 4 |
| 6914 / YuNet | 7/7 | 0,62 / 0,72 / 0,81 | 6,08 → 4,90 | 4 → 4 |
| e8fa / Haar | 18/18 | 0,73 / 0,94 / 1,00 | 4,00 → 3,75 | 9 → 7 |
| c04e / Haar | 9/9 | 0,47 / 0,84 / 1,00 | 3,11 → 2,63 | 3 → 4 |

Salturile includ perechile față/joc, nu doar aceeași țintă. Verificarea pe
cadre în jurul granițelor: d789/YuNet la 14,63 s (textul ajunge peste HUD),
e8fa/Haar la 5,13 s (capul păstrat cu persoana mai mică), c04e/Haar la 1,32 s
(saltul nou de cadru larg→aproape). Timpii egali nu înseamnă ritm identic.
Datele complete sunt în `paired/comparison.json`.

### Teste și limite rămase

Suita completă: **2.304 passed, 2 deselected**, în 126,28 s. Sunt excluse doar
cele două teste TikTok 404 preexistente. Prima rulare a avut un test încă
învechit al lui Claude și un eșec intermitent la reînnoirea lease-ului; acesta
a trecut separat și apoi în suita completă, fără modificări la job queue.
După împărțirea fișierului de teste la limita de 500 de linii, cele 111 teste
focalizate trec din nou (inclusiv encode/decode, fără skip).

Claude a corectat în raport două afirmații neverificate: testul MP4 nu fusese
sărit și observațiile Haar pătrate nu fac întregul plan neschimbat — doar
formula inițială de scară rămâne aceeași. Uniunea locală poate schimba Haar.
Raportul lui rămâne o sursă secundară; artefactele și testele de mai sus sunt
baza acceptării. Nu a fost raportată epuizarea cotei Claude.

Rămân: identitatea și continuitatea țintei, cadrele fără detecție, subiectul
care iese din camera inițială, costul variațiilor de scară, texte/HUD ale sursei
și alegerea automată a regiunilor pentru reacții. YuNet nu este activat global;
probele nu justifică încă o înlocuire generală a detectorului implicit.
