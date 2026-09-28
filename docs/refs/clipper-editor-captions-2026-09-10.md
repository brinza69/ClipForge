# Editorul și subtitrarea folosesc timpul exportului

Lot implementat la 10 septembrie 2026. Repară randarea și instrumentul de
editare; nu validează alegerea momentelor și nu rezolvă încadrarea subtitrării
native din sursă.

## Defecte reproduse

1. `_write_ass` muta evenimentele după eliminarea pauzelor, dar ambele
   renderere ardeau ASS-ul înainte de `select/setpts`. În proba reală cu o
   secundă eliminată, SECOND lipsea la 1,7 s în MP4. Același test fără tăiere
   trecea. Subtitrarea se arde acum după scurtare, pe imaginea compusă, inclusiv
   pe benzile de letterbox. Comenzile camerelor rămân înainte de tăiere.
2. `remap_overlays` muta intervalul rândului, nu și timestampurile cuvintelor.
   LEFT rămânea evidențiat tot intervalul LEFT RIGHT. Se mută acum ambele,
   fără modificarea planului stocat.
3. `/preview-frame` citea cadrul complet al sursei, fără încadrarea din export
   și fără politica de suprimare; overlay-urile relative la clip erau evaluate
   la timpul absolut al sursei. Ruta folosește acum `_decide_render` și aceiași
   constructori de comandă ca exportul, într-un director temporar separat.
4. Prima corecție a previzualizării încă selecta alt cadru: pe Speed, cererea
   de 10,5 s corespundea cadrului MP4 631, nu 630. La conversia 60→30 fps,
   `-r` și filtrul `fps` puteau selecta cadre diferite după un start fracționar.
   Ambele renderere au acum o grilă explicită comună de cadre, după compoziție
   și subtitrare; editorul selectează un index din ea și raportează timpul său.
   Testul folosește o sursă cu altă valoare de gri în fiecare cadru, la 30 și
   60 fps, nu un fundal constant care ar ascunde decalajul.
5. Salvarea presetului modifica preferința de reconstrucție, dar ASS-ul citea
   stilul vechi din `caption_plan`. Salvarea actualizează acum și stilul,
   păstrând textul, cuvintele și poziția manuală. Un preset necunoscut este
   refuzat. Rebuild cere salvarea modificărilor din editor înainte de aplicare.
6. O editare a subtitrării sau încadrării putea lăsa disponibil vechiul export
   ca și când ar descrie editarea nouă. Se invalidează acum referințele la
   preview/export și review-ul, inclusiv la reconstruirea subtitrării.
   Fișierele rămân pe disc. Un export activ blochează modificările care i-ar
   schimba imaginea; câmpurile nepermise rămân ignorate conform contractului API.

Versiunile rendererelor sunt `render_static_split_v2_caption_clock` și
`render_v4_caption_clock`. Grila explicită de cadre se aplică și fără eliminarea
pauzelor; nu pretindem identitate de octeți cu vechiul renderer. Versiunile intră
deja în amprentă. Sidecar-urile vechi nu sunt reetichetate.

## Verificarea pe Speed

Sursă: proiectul `2d3375ee3420`, clipul `d789060e273d`, 386,47–404,37 s.
Directorul candidatului final:
`data/codex-analysis-20260910/editor-speed-final/`.

- MP4 nou de 17,9 s, 1080×1920, 60 fps, audio prezent; decodare integrală.
- Cadre ale editorului și cadre extrase din MP4 la 3,5 și 10,5 s; verificare
  geometrică și a textului, plus comparație numerică în `verification.json`.
  Ambele imagini trec prin conversia PNG a ffmpeg, pentru a nu confunda o
  diferență între decodoare cu una a renderului.
  Eroarea medie absolută este 1,206 și 1,298 niveluri RGB din 255; nu este
  identitate de pixeli cu un PNG necomprimat.
- Amprentă v2 și identitatea MP4-ului verificate.
- Inventar SHA256 înainte/după pentru cele 301 de fișiere MP4/JSON/ASS din
  directoarele `exports/`: fără modificări sau adăugiri din probă.
- `editor-speed/` păstrează prima probă, cu decalajul de un cadru; imaginile
  numite `editor-final-*` din acel director verifică numai corecția selecției
  față de primul MP4. Rezultatul complet actual este în `editor-speed-final/`.

## Limite și pașii următori

Previzualizarea este o randare nouă a setărilor salvate, nu o certificare a unui
MP4 mai vechi. Prima generare și schimbarea cadrului pot dura câteva secunde,
fiindcă rulează planificarea și prefixul video. Există stare de încărcare,
eroare explicită și temporizare pentru slider.

Nu s-au schimbat declarațiile de captions/layout pe proiecte. Detectorul de
captions native rămâne necalibrat. Regiunile experimentale și adnotările din
loturile go ghost nu au fost promovate la adevăr în producție. Rămân de făcut
tratamentul subtitrării native pe intervale, verificarea încadrării și
lizibilității, apoi lotul de clipuri nevăzute și judecarea montajului în mișcare.

O altă limită existentă: schimbarea manuală a limitelor clipului nu reconstruiește
automat textul/timingurile planului stocat; utilizatorul trebuie să folosească
rebuild după salvarea noii ferestre. Acest lot invalidează fișierul vechi, nu
pretinde că a reanalizat transcrierea.

Suita completă: **1953 passed, 2 deselected** (cele două 404 TikTok cunoscute).
După aceasta, testul evidențierii cuvintelor a primit și verificarea culorii în
MP4; toate cele cinci teste de caption clock au trecut din nou.
TypeScript și ESLint au trecut. Testele HTTP/randare folosesc DB și media
temporare, inclusiv salvarea stilului, dispariția referinței la exportul vechi,
refuzul editării în timpul exportului și lipsa stratului suprimat în pixeli.
Controlul vizual al paginii prin Chrome și computer-use a fost indisponibil:
ambele instrumente au eșuat la inițializare cu `failed to write kernel assets`.
Cadrele PNG au fost inspectate direct; nu se revendică o vizionare a întregului
clip cu sunet sau un verdict de publicare.

În proba Speed, rândul plasat la centru traversează gura la 10,5 s. Acum se
vede și în editor; lotul nu a schimbat automat poziția aleasă pentru acest
proiect. Fidelitatea previzualizării nu înseamnă că plasarea este bună.
