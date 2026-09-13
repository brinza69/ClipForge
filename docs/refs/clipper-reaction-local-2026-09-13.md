# Proba locală de încadrare pentru reacții — 13 septembrie 2026

Utilizatorul a indicat `data/clipper/2c8af11153a3/source/source.mp4`.
Fișierul există: 1920×1080, 60 fps, AV1/AAC, 13.402,906122 s,
1.606.933.658 octeți. SHA256:
`598a9b9fb774049979a4c2de1d938b1a351f9172cc95a50b5ca072c5515c012e`.
Titlul proiectului din DB este „moistcr1tikal Just Chatting”; calea dată de
utilizator este autoritatea selecției, nu numele xQc folosit în conversație.

## Limitele referinței

Short-ul `T1jsllUvrHA` nu a fost vizionat și sunetul lui nu a fost ascultat.
Miniatura arată material sus și reacție jos. Nu stabilește ritmul, proporțiile,
vorbitorul subtitrării sau necesitatea de a păstra interfața browserului.
Sursa locală nu a fost identificată drept episodul din acel Short.

## Ce s-a observat în sursa locală

Claude Code a pregătit analiza; Codex a inspectat toate cele 20 de cadre rare
și cele 24 de cadre la 0,5 s din fereastra [2692,2704). Decodarea păstrează
PTS-ul efectiv, nu un index dedus din timpul cerut. Artefacte:

- `data/claude-xqc-reference/local-source-20260913/manifest.json` și imaginile;
- `data/claude-xqc-reference/probe-source-2692-2704/manifest.json` și imaginile.

Sursa este browser cu o cameră inserată la mijlocul marginii stângi. Chenarul
albastru la cinci momente verificate este aproximativ x0,y448,w346–348,h293–294;
imaginea interioară propusă pentru probă este x8,y476,w326,h232. Estimarea
anterioară y740 numea baza camerei ca și cum ar fi fost marginea de sus.
Codex a retras și propria afirmație că poziția se muta între aceste mostre.

Alte cadre arată o altă persoană în cameră și, separat, un scaun gol. Camera
fixă nu demonstrează prezența sau identitatea unui vorbitor. Unele pagini sunt
căutări; altele conțin video cu subtitrare deja arsă. Nu există o politică de
încadrare sau captions certificată pentru întregul VOD.

Propunerea inițială de player x90,y55,w1400,h835 și recomandarea 563–660 s
din `RESULT-LOCAL.md` sunt retrase. Cuvintele din transcriere despre Forged in
Fire nu demonstrează că acel video a fost deschis. Fereastra folosită efectiv
este 44:52–45:04, cu animația „The Dawn of Sketch Comedy”. Este o probă de
încadrare, fără verdict privind selecția editorială sau sunetul ascultat.

## Trei MP4-uri reale, înaintea noului tratament

Director: `data/claude-xqc-reference/probe_20260913_105204_483935/`.
Toate trec prin `workers.clipper_render_output.render_export`, cu aceleași
12 secunde, 1080×1920/60 fps, fără ASS, watermark sau intervale eliminate.
Obiecte în memorie, fără scriere în DB; politicile nemăsurate rămân `None`.

| Variantă | Regiuni sursă și tratament | Ce arată exportul |
|---|---|---|
| A SOURCE_FIT | sursa întreagă, fit static | mult negru, camera mică, browser prezent; nu este un export „ca livrat” |
| B NARROW_REJECTED | sus x578,y128,w750,h800; jos x8,y476,w326,h232; față 40% | textul/numeralul din stânga este tăiat la 8,5 s; respins |
| C WIDE_CONTEXT | sus x348,y14,w1314,h1052; jos x48,y476,w238,h232; față 55% | „Boogah” și numeralul încap la 8,5 s; browserul ocupă imaginea; cameră mărită și tăiată lateral |

Fișierele sunt `probe_{A,B,C}_local-reaction-probe-xqc-sample3-{a,b,c}.mp4`.
SHA256 în aceeași ordine:

- `987c445a526954a3b54b585e0eff7299be2976f9bc2daae3133140ba911ce918`
- `50de436ba98bcd02ec63cb9e4648f2673a4f252b2e3815f933a26bd653d80a10`
- `953e34e046f11226cab1f8267b3c4deb8743db068c600c5ebc8cb5c6728198dc`

Codex a decodat și inspectat șapte cadre per MP4 (21 în total), inclusiv
8,5 s. `inspection/decoded-evidence.json` păstrează adresele și PTS-urile.
Fluxurile audio codate sunt identice între alternative; existența și durata
lor sunt verificate, sunetul nu a fost ascultat.

`verification.json` consemnează decodarea integrală fără erori, video/audio de
12 s, 60 fps, recitirea MP4 pentru `output_identity.matches`, recalcularea
strictă a amprentei v2 și `caption_filter=false` din comanda efectivă. Inventarul
cu SHA256 al celor 390 de fișiere existente în `*/exports/*` este identic
înainte/după. Directorul exports al proiectului selectat este gol; de aceea
verificarea folosește exporturile existente din toate proiectele.

Acestea sunt probe tehnice și observații eșantionate, nu aprobare de calitate.
Un prim apel a eșuat doar la afișarea finală Unicode; scriptul corectat a fost
rulat din nou integral, cu exit 0 și un director nou (cel de mai sus).

## Următoarea probă

`PRPs/clipper-reaction-panel-fit.md` cere un tratament explicit pentru regiunea
urmărită: fit proporțional în panoul de sus, cu fundal blurat din aceeași
regiune. Nu cere schimbarea implicitelor aplicației. Decupajul de probă
x348,y128,w1314,h798 exclude browserul, dar exclude și 108 px din stânga
videoclipului urmărit pentru a evita camera deja inserată acolo. Acesta este
un cost declarat, nu păstrarea integrală a videoclipului urmărit.
În cadrele de început, această margine taie și elemente din pagina fictivă
desenată în animație. „Boogah” și numeralul păstrate nu înseamnă tot textul
din sursă păstrat. Cele șase `content-fit-sheet-*.jpg` consemnează inspecția
regiunii propuse pe toate cele 24 de cadre sursă deja eșantionate.

## D — fit în panou, implementat și verificat

Claude Code a implementat constructorul explicit `reaction_layout.py` și
calea opt-in din `layout_geom.py`. Codex a reprodus și corectat problemele de
coordonate impare, cădere pe fullscreen la cameră lipsă, rază de blur prea mare
pentru cadre mici și lipsa dimensiunilor sursei din plan. Codex a completat
refuzul dreptunghiurilor modificate/nevalide și testul prin exportul comun:
plan salvat, amprentă v2, identitate recitită și pixeli decodați. Textul despre
rotunjire a fost corectat: helperul existent poate devia cu până la 1,5 pixeli
de la dimensiunea ideală, nu întotdeauna sub un pixel.

Noul MP4:
`data/claude-xqc-reference/panel_fit_20260913_214419_372541/reaction_content_fit.mp4`.
SHA256 `9c8e61a35456c9a077d38aec4fdeef9ebdaf3adca718c3b453f5922273350c1c`,
4.132.870 octeți, 12 s, H.264, 1080×1920/60 fps, audio prezent.
Versiunea statică: `render_static_split_v3_reaction_fit`.

Conținut x348,y128,w1314,h798; cameră x8,y476,w326,h232; panou cameră 40%.
Imaginea din panoul de sus are 1080×656 px la x0,y248, peste un fundal blurat
derivat din aceeași regiune. Întreaga regiune DECLARATĂ intră în prim-plan;
nu intră tot videoclipul urmărit. Regiunea nu se lărgește înapoi în browser.

Codex a inspectat toate cele 24 de cadre noi de la 0,25 + n×0,5 s, pe șase foi
de contact, plus comparația C/D/sursă la 8,75 s. Ce arată:

- taburile, adresa, titlul YouTube și bara Windows din C dispar;
- „Boogah” și numeralul sunt vizibile la 8,75 și 9,25 s;
- capul persoanei din cameră rămâne în cadru în toate aceste mostre, cu mai
  puțină mărire decât în C;
- fâșia stângă pierdută, elementele paginii fictive tăiate, mărimea redusă a
  desenului în unele scene și fragmentul de ornament al camerei rămân costuri;
- blurul ocupă spațiu și preia variațiile imaginii urmărite. Nu este evaluat
  drept tratamentul final preferat de utilizator.

`inspection/verification-decoded.json`: PTS sursă/export aliniate la toate cele
24 de momente, audio codat identic cu C. Diferența RGB medie față de un decupaj
al sursei scalat independent cu PIL este 0,70–1,50 din 255 în prim-plan; este un
diagnostic al geometriei, nu un scor de calitate sau o verificare a sunetului
ascultat. Interpolarea și compresia sunt diferite între cele două imagini.

`verification.json`: decodare integrală fără erori, sidecar citit de pe disc,
amprentă v2 recalculată, `output_identity.matches`, `caption_filter=false`,
390/390 fișiere existente cu aceleași SHA256 înainte/după. Proba a ieșit cu 0.

Validare cod: 42 teste dedicate; suita completă **2.138 passed, 2 deselected**
(numai cele două TikTok 404 preexistente), exit 0, 220,54 s. 210 filtre pentru
modurile existente, cu flag absent/False, sunt identice cu `bb99f81`.
Loguri în `data/claude-xqc-reference/panel-fit-full-tests.log` și
`legacy-filter-comparison.json`. Toate fișierele de cod atinse au sub 500 linii.

Tratamentul este disponibil constructorului explicit și folosit în această
probă. Nu este legat încă la selecția automată a regiunilor sau la interfața
de export. Următorul lot trebuie să decidă intervalele potrivite și să lege
regiunile observate de exportul obișnuit; nu se aplică acest crop întregului
VOD. Detectorul implicit rămâne Haar. Niciun verdict de publicare automată.

Claude: prima sesiune s-a oprit la limita internă de 28 pași, a doua a încheiat
cu exit 0. Nu s-a observat epuizarea cotei Claude și nu s-a programat o reluare.
