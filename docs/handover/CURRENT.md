# ClipForge — Current Application Handover

**Scope:** aplicația întreagă, nu doar AI Stream Clipper.  
**Data hărții:** 3 septembrie 2026.
**TikTok:** exclus din această hartă, conform cerinței proiectului.

## Actualizare Clipper, 11 septembrie 2026

Randarea comună, sincronizarea captions/editor și evitarea automată a fețelor
observate au ajuns în probe MP4 separate. Starea actuală și limitele sunt în
[handover-ul Clipper](areas/clipper/CURRENT.md); cifrele din secțiunea istorică
de mai jos nu descriu aceste probe noi. Publicarea automată nu este aprobată.

La 12 septembrie, lotul A al observațiilor de față este închis: separă erorile
tehnice de zero detecții și păstrează adresa ferestrei analizate. 2.005 teste
trec; proba Speed rămâne identică la nivel de octeți. Detectorul Haar nu este
încă înlocuit. Contractul și probele sunt în handover-ul Clipper de mai sus.

Lotul B1 al detectorului nou este verificat: adaptor și comparație pe cadre,
cu 2.053 teste trecute și aceleași două erori TikTok cunoscute. Încă nu schimbă
detectorul aplicației; urmează profilul pentru fețe mici și proba video.

B2 este verificat (2.096 teste trecute, 2 TikTok excluse), dar proba Speed
respinge înlocuirea directă: zoom excesiv și subtitrare peste față. Profilul
nou rămâne opțional pentru probe; încadrarea trebuie separată de dimensiunea
cutiei returnate de detector. Detaliile și fișierele sunt în handover-ul Clipper.

La 13 septembrie, Claude a accesat doar metadatele și miniatura referinței xQc.
Codex a respins deducerea unei specificații de montaj din miniatură; analiza
temporală și audio așteaptă acces la video-ul efectiv. Starea este în handover-ul
Clipper de mai sus; nu s-a implementat un tratament nou din această referință.

Sursa locală indicată apoi de utilizator a produs trei probe de 12 secunde:
cropul îngust taie text, cel larg aduce browserul în cadru. Codex a verificat
MP4-urile și păstrarea celor 390 de fișiere existente. Fit-ul explicit a produs
apoi un MP4 fără barele browserului, verificat pe 24 cadre noi; 2.138 teste trec,
2 TikTok excluse. Rămâne experimental, cu pierderea laterală declarată și fără
activare automată în interfață; probele și limitele sunt în handover-ul Clipper.

## Stare, 29 august 2026

Reasoning v2 al Clipper-ului este implementat până la Batch 6 inclusiv, rulează în
`story_v2_shadow` — calculează ordinea nouă și o înregistrează, dar livrează în continuare ordinea
legacy — și **nu este aprobat implicit**: `story_v2` este refuzat de API până la comparația oarbă pe
corpus. Shadow păstrează separat `shadow_rank`, fără să schimbe board-ul livrat.

Rendererul Clipper este acum `render_v3_letterbox`: exporturile `fit` păstrează cadrul complet cu
fundal blurat, iar toate cele 58 de clipuri pilot au fost re-randate complet. Geometria trece, dar
auditul a găsit montaj excesiv, 116 tăieturi fără schimbare vizuală, boundaries agresive, captions
duble și UI de browser. Motorul nu este încă aprobat pentru publicare automată.

**Batch R0 este închis.** Auditul celor 58 de exporturi este acum cod care se rerulează
(`python scripts/audit_clipper_exports.py pilotf81b pilotee0e pilot6b38 pilot2c8a`) și reproduce
baseline-ul exact: 58 clipuri, 1.341 shot-uri, 29,3349/min, 116 tăieturi `fit → fit`. Este un gate,
nu un raport — iese cu 2 pe artefacte corupte, sidecar-uri care numesc alt clip sau alt proiect și
fingerprint-uri care nu mai corespund planului. **Batch R1 este de asemenea închis:** o tăietură
există acum numai dacă imaginea livrată se schimbă, iar pe cele 58 de planuri 1.341 shot-uri devin
1.225 — exact cele 116 invizibile. Exporturile randate le mai poartă până la re-randarea piloturilor,
fiindcă auditul citește sidecar-urile, iar R1 a schimbat plannerul. **Batch R2 este închis:** fiecare clip își rezolvă gramatica de montaj din tipul lui de conținut, cu
regula că o clasificare slabă cumpără un montaj mai sigur, niciodată unul mai agresiv — profilul este
înregistrat lângă fiecare export și aplicat pe niciunul. Punctul de reluare este
**Batch S8** din
[`plans/ai-stream-clipper-production-engine-v1.md`](../plans/ai-stream-clipper-production-engine-v1.md).
Detaliile și cifrele sunt în [`areas/clipper/CURRENT.md`](areas/clipper/CURRENT.md).

**În paralel cu planul, ÎNCADRAREA PE FAZE (5–9 septembrie).** A pornit de la un defect
livrat — pe surse fără a doua cameră, 48,1 s din `pilotf81b` aveau o încadrare care nu
conținea subiectul — și e reparat și măsurat pe cadre randate (0,0 s, 71/71 fețe). Ce a
urmat nu e închis: cele patru regiuni pe faze ale lui `b23c14c41495` sunt înghețate dar
**neverificate**, fiindcă instrumentul cu care fuseseră adnotate — citirea proxy-ului
480×270 mărit — s-a dovedit de șase ori mai imprecis decât toleranța pe care o declara.
Re-măsurarea pe sursa 2560×1440 e în curs, 27 din 258 de perechi cadru/parte. Nimic din
asta nu e cablat la randare. Starea și punctul de reluare sunt în handover-ul modulului;
istoricul și cele cinci defecte de instrument în
[`archive/clipper/handoff-clipper-session-5.md`](archive/clipper/handoff-clipper-session-5.md).

R3–R7 sunt livrate ca instrumentare/shadow, R8 este închis, S7a a livrat envelope-ul comun,
S7b recuperează promises și anchors per chunk, S7c recuperează verdictul judge per rundă și
întrebare exactă, S7d propagă identitatea canonică a ancorei la toate variantele, S7e separă
gruparea euristică de alegerea liderului pe `selection_score`, iar S7f leagă fiecare clip și sidecar
de rularea care l-a selectat. **S7 este închis.** S8a leagă sesiunile noi de review de amprentele
întregului export și sidecar, folosind versiunea reală declarată, nu cea instalată. Schimbarea
fișierului oprește continuarea. S8b ascunde rezultatele până la ultimul răspuns valid și păstrează
răspunsurile imuabile, inclusiv la retry sau cereri concurente. Este protecția evaluării, nu dovada
calității; S8 rămâne deschis. Urmează randarea neutră și rubrica completă, apoi cohortele umane.

Verificare pe working tree-ul local, 3 septembrie: **1.752 teste backend trec, 2 pică** — ambele din
`test_tiktok_transform.py`, cu 404, pentru că routerul TikTok nu este montat. Typecheck curat.
S8a/S8b nu schimbă nicio alegere sau randare și nu creează sesiuni reale.

## Cum folosești acest document

Acest fișier oferă imaginea de ansamblu. Pentru implementare sau debugging, deschide handover-ul modulului respectiv din [`INDEX.md`](INDEX.md).

## Structura produsului

ClipForge este un studio local de procesare video AI cu:

- Next.js frontend în `src/`;
- FastAPI backend în `server/`;
- SQLite + SQLAlchemy async pentru entitățile persistente;
- job queue comun pentru operații lungi;
- FFmpeg și servicii AI locale/externe pentru procesare;
- fișiere pe disc pentru media, proiecte, cache și rezultate.

## Fluxurile principale

```text
Remix:
input → preview → download/upload → transcript → TTS → speed match → captions → export → descriptions

Parallel:
input + variante → reutilizare Remix → procesare concurentă → rezultate separate

Doodle:
project → script → voiceover → images → storyboard → render

Captions:
template/font/source → preview → optional transcription → FFmpeg burn → download

Transcript:
text/upload → engine selectat → clean job → result/download

TTS:
engine/voice → synthesize job → audio result/download

Utilities:
upload → erase/silence/upscale job → result/download

Clipper:
source → ingest → transcribe → analyze → score → candidate clips → preview/export
```

## Reguli globale de lucru

- Frontend-ul trebuie să apeleze backend-ul prin proxy-ul Next `/worker-api/...`.
- Job-urile lungi trebuie urmărite prin status persistent, nu doar prin state local în browser.
- Orice proces extern trebuie să aibă timeout și cleanup.
- Orice output final trebuie verificat înainte să fie raportat ca finalizat.
- Modificările de DB trebuie documentate și migrate atât în model, cât și în schema existentă.
- TikTok nu este inclus în această hartă.

## Priorități globale

1. Gate-ul vizual pentru R3a, R3b și R4: toate trei sunt instrumentate și niciunul nu e închis,
   fiindcă verdictul cere un om care se uită la patru clipuri.
2. Re-score al piloturilor: singurul lucru care face gate-ul R5 măsurabil, și nu cere
   pe nimeni.
3. Clipper Batch R6: captions și source hygiene. **Livrat 30-31 august 2026**, plus un defect
   livrat găsit de review uman: pe shot-urile `fit` caption-ul nu era desenat deloc (barele
   transparente + subtitrări arse înainte de `overlay`), 331 de secunde din 27 de clipuri. Reparat,
   cele 58 de exporturi ale piloturilor re-randate.
4. Clipper R7: preflight de publicare. **Livrat 31 august 2026.** Pe corpus: 0 APPROVE, 33 REVISE,
   37 REJECT, 31 UNDECIDED — cele 37 de respingeri sunt captions-urile duble din sursă, defectul cu
   care s-a deschis R0. Nu e cablat la randare.
5. Clipper R8: reconstrucția montajului după trim — ÎNCHIS 2 septembrie 2026; salturile create de
   `drop_spans` sunt măsurate separat de tăieturile plannerului.
6. Clipper S7–S8: S7a–S7f sunt închise, inclusiv identitatea comună selection/render; continuă
   S8a/S8b protejează media și blinding-ul; continuă review-ul golden S8 înainte de activarea `story_v2`.
7. Consistență între DB și filesystem și idempotency pentru job-urile de export.
8. Upload streaming și limite reale de memorie/disk.
9. Readiness checks și teste de reziliență pentru aplicația întreagă.

## Handover pe module

- [`Clipper`](areas/clipper/CURRENT.md)
- [`Remix`](areas/remix/CURRENT.md)
- [`Parallel`](areas/parallel/CURRENT.md)
- [`Parallel from Sheets`](areas/parallel-sheets/CURRENT.md)
- [`Doodle`](areas/doodle/CURRENT.md)
- [`Captions`](areas/captions/CURRENT.md)
- [`Transcript`](areas/transcript/CURRENT.md)
- [`TTS`](areas/tts/CURRENT.md)
- [`Utilities`](areas/utilities/CURRENT.md)
- [`Infrastructure`](areas/infrastructure/CURRENT.md)
