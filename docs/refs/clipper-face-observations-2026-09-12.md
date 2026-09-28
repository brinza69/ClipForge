# Observații de față — lot A, 12 septembrie 2026

## Domeniu

Codex a continuat implementarea începută de Claude după limita sesiunii sale.
Claude Code a făcut review static al candidatului final fără alte constatări
acționabile; nu a rulat testele. Codex a executat verificările de mai jos și
acceptă lotul A. Review-ul brut: `data/claude-face-detection/batch-a/claude-final-review.md`.
Acesta este lotul de
adresare/stări, nu înlocuirea detectorului Haar și nu tracking.

`face_presence` păstrează `t`/`boxes`, cu patru stări: `detected`, `empty`,
`unreadable`, `detector_unavailable`. `empty` înseamnă zero detecții, nu absență
demonstrată a feței. Fișierul lipsă, seek/read eșuat și detectorul indisponibil
au motive separate. O eroare de detecție după citire păstrează adresa cadrului.
Un index negativ, fracționar sau nefinit devine null, nu un întreg inventat.
PTS se citește după `read()`; citirea înainte dădea cadrul anterior.
Convenția este verificată pentru backendul FFmpeg și marcată
`address_basis: opencv_ffmpeg_metadata`. Alte backends păstrează observația,
cu adresa null; eșecul citirii unei proprietăți invalidează proprietatea,
nu cadrul decodat.

`dynamic_window` păstrează metadatele observației. `t` rămâne pe ceasul
`source_requested`, în timp ce `decoded_t`/`frame_index` numesc
`reencoded_window`. Acestea nu sunt o adresă certificată în sursa originală.
Un eșantion legacy nu primește tăcut o stare ori o adresă decodată.

Raportul `clipper_caption_faces_v2` numără separat detecțiile, zero detecții, eșecurile
de citire, eșecurile detectorului, legacy și stările contradictorii/necunoscute.
O stare contradictorie nu poate furniza cutii pentru mutarea subtitrării.
Pozitivele legacy rămân propuneri cu incertitudinea numărată. Acoperirea rămâne
incompletă. Metadatele de lucru sunt eliminate din planul livrat; rezumatul
observațiilor rămâne în sidecar-ul produs de rendererul comun.

Integrarea a descoperit și corectat un defect numeric: `(.47 + 2) - .47`
ajunge sub 2 și includea proba terminală în ultimul shot. Comparația se face
acum pe ceasul comun al sursei cerute. Testul cu o fereastră reală la 5fps
păstrează separat proba interioară necitibilă produsă de seek-ul nealiniat.

## Verificări executate

- 2.005 teste backend trecute, 2 excluderi TikTok preexistente; fără relaxări.
- Oracle video cu conținut diferit per cadru: citește pixelii dați efectiv
  detectorului, nu un cadru recitit după indexul raportat. Toleranță 1ms la
  interval de 200ms. Două mutații, PTS cu un cadru în urmă și index cu un cadru
  înainte, sunt respinse de test.
- Integrare cu encode/decode real al ferestrei, `_dynamic_plan` și decizia
  captions, inclusiv legacy și erori de detector. Testele MP4/editor existente
  verifică în continuare poziția manuală și politica de suprimare.
- Contraexemplele independente inițiale sunt rerulate în
  `data/claude-face-detection/review-a/contract-codex-final.json`.
  Proba independentă a mutațiilor este în `review-a/final-clock/verification.json`.

## Speed, fișier nou separat

`data/claude-face-detection/batch-a/codex-final-03/` conține observațiile,
fereastra analizată și exportul `d789060e273d.mp4` cu sidecar v2.
Fereastră sursă cerută: 386,47–404,37s, proiect `2d3375ee3420`.

- 72 observații: 42 cu detecții, 30 fără detecții; zero erori de citire sau
  detector, zero adrese indisponibile.
- Implementarea Haar de la `386e694`, executată pe aceeași fereastră, produce
  exact aceleași `t`/`boxes`.
- 39 cutii mapate și 6 în afara cropului, 3 shot-uri fără fețe mapate.
  Y rămâne 0,75; cele 32→0 intersecții sunt ale euristicii, nu o măsurătoare a
  tuturor pixelilor sau o certificare a absenței suprapunerii.
- MP4 1080×1920, 60fps, 17,9s cu audio, decodat integral fără eroare.
  SHA256 identic cu proba acceptată din `caption-faces-final/`.
- Fingerprint v2 și output identity valide; 301 fișiere existente din exports
  comparate prin hash, fără modificări.

Prima încercare a scriptului de probă a folosit cheia greșită a deciziei și
s-a oprit înainte de encode. Directorul `codex-final/` nu este rezultatul final.
Probele nu rescriu corpusul și nu repornesc aplicația. Activarea backendului
se face separat, după verificarea joburilor și închiderea lotului.

## Rămâne deschis

Recall-ul și falsele detecții Haar, identitatea vorbitorului, tracking-ul,
acoperirea temporală, textele native și alegerea țintei potrivite nu sunt
rezolvate de acest lot. Următorul detector are acum observații ale căror erori
tehnice pot fi deosebite de zero detecții; calitatea lui necesită probe proprii.
