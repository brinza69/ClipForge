# Handover — AI Stream Clipper

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

## Istoric și reluarea investigațiilor vechi

Actualizările de mai sus descriu starea curentă. Secțiunile anterioare au fost
mutate integral în arhivă pentru a respecta limita de 500 de linii; conțin și
afirmații ulterior retrase. Nu combinați cifre din rulări diferite.

- [snapshot, R0–R3](../../archive/clipper/2026-09-20-current-history-1.md)
- [R4–R6 și corecții](../../archive/clipper/2026-09-20-current-history-2.md)
- [R7, review și gate vizual](../../archive/clipper/2026-09-20-current-history-3.md)
- [R8, S7–S8 și încadrarea pe faze](../../archive/clipper/2026-09-20-current-history-4.md)
- [puncte de reluare și limite istorice](../../archive/clipper/2026-09-20-current-history-5.md)
