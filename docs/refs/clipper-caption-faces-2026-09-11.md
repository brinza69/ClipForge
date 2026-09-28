# Subtitrarea peste față — 11 septembrie 2026

## Efectul livrat

Pe Speed `d789060e273d` (proiect `2d3375ee3420`), subtitrarea de la 51% din
înălțime acoperea gura în cadrele de la 3,5 și 10,5 s. Poziția automată este
acum 75%, constantă pe clip. MP4-ul nou arată textul pe piept în aceste cadre
și în câte un cadru din fiecare dintre cele nouă shot-uri inspectate.

Probele sunt separate de corpus:

- înainte: `data/codex-analysis-20260910/editor-speed-final/d789060e273d.mp4`;
- după: `data/codex-analysis-20260910/caption-faces-final/d789060e273d.mp4`;
- în directorul „după”: `before-shots.png`, `after-shots.png`, cadrele individuale
  la 3,5 și 10,5 s și `verification.json`.

Planul dinamic, planul de captions stocat și eliminările de pauze sunt identice
între cele două sidecar-uri. Se schimbă poziția efectiv arsă. Exportul nou:
1080×1920, 60 fps, 17,9 s, audio prezent; decodare integrală fără erori.
Diferența medie PNG-editor versus cadru extras cu ffmpeg din MP4 este 1,213 și
1,301 niveluri RGB din 255, nu identitate de pixeli după compresie.
Amprenta v2 și identitatea MP4 trec. Inventarul SHA256 al celor 301 de fișiere
MP4/ASS/JSON existente în `exports/` este identic înainte și după probă.

## Ce face codul

`dynamic_window` transmite dimensiunile ferestrei chiar decodate. Observațiile
locale despre față sunt proiectate prin `evidence_map.crop_window` și `map_box`,
prin shot-ul corespunzător timpului lor. Nu folosește `shot.rect` ca poziție
livrată și nu folosește media feței pe clip.

`caption_faces.place` mută numai un strat propriu ars, fără poziție manuală,
când banda estimată de captions intersectează fețe observate și există o altă
poziție care evită toate proiecțiile disponibile și zonele rezervate de UI.
Rămâne o singură poziție pe clip. Nu modifică camera, textul sau dimensiunea lui.
Păstrează poziția dacă nu găsește loc, lipsesc coordonatele observației, cropul
își schimbă dimensiunea, există shake ori captions depășesc domeniul aproximării
(font peste 72 px, scară peste 1 sau entry-pop).

Căutarea include și poziția exactă a presetului de jos, 0,75. Grila comună se
termină la 0,7442; nu este rotunjită în sus și nu este modificată pentru ceilalți
consumatori. Zonele statice etichetate `face` nu sunt folosite pentru un crop
dinamic: descriu altă compoziție. Celelalte rezervări sunt păstrate.

Decizia ajunge înainte de `_write_ass`, în calea comună editor/export/replan.
`caption_y` efectiv intră deja în amprenta v2. `caption_face_placement` este
explicație separată, fără putere de aprobare. `_face_space` și detecțiile rămân
date de lucru și sunt eliminate din planul scris în sidecar.

## Limite care rămân deschise

Pe această probă: 39 de cutii proiectate, 6 în afara cadrului, 30 de probe cu
zero detecții și 3 shot-uri fără fețe proiectate. Cu banda **aproximată** de
192 px, 32 de intersecții devin zero. Acestea sunt rezultatele euristicii,
nu măsurători ale conturului literelor arse și nu o verificare temporală completă.

`face_presence` raportează timpii ceruți la seek, nu indexuri decodate garantate;
asocierea lângă tăieturi are această limită. Detectorul poate rata fețe și poate
găsi altele false. Nu știe identitatea sau ținta editorială. `coverage_complete`
rămâne **false inclusiv când mutarea se aplică**. Dimensiunea standard nu
garantează lungimea unui text atipic sau orice stil personalizat.

Subtitrarea nativă, mâinile/obiectele de prezentare, lizibilitatea pe tot clipul
și selecția semantică nu sunt rezolvate de acest lot. Nu extinde verdictul probei
la întregul corpus și nu promovează `captions_complete` sau alte porți la pass.

## Verificare

1.970 de teste trec, două teste TikTok cu 404 preexistent excluse. Cele 17 noi
includ asocierea timp/anchor, lipsa observațiilor, refuzul geometriei mobile,
poziția manuală, rezervări UI și trei encodări reale: mutare, poziție manuală,
strat suprimat. Oracle-ul video citește pixelii decodați, fără să derive
coordonatele așteptate din funcția care a ales poziția.

Aplicația locală a fost repornită fără joburi active. Cererea de preview la
3,5 s, prin proxy-ul frontend, întoarce 200, `burn`, `no-store` și un PNG cu
același SHA256 ca proba editorului. Aceasta verifică ruta live, nu interacțiunea
vizuală cu controalele Chrome.
