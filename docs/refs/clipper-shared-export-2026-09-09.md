# Export comun — implementare și probe, 9 septembrie 2026

Lotul unifică execuția exportului și înregistrarea rezultatului. Nu este o
aprobare editorială, o activare a reasoning-ului v2 sau o rezolvare a încadrării.

## Ce s-a schimbat

`server/workers/clipper_render_output.py::render_export` primește decizia deja
rezolvată și execută randarea statică sau dinamică. Aceleași opțiuni de fps,
CRF, preset, watermark și dimensiuni sunt transmise encoderului și scrise în
sidecar. Amprenta nouă este explicit v2; rezultatul poartă `render_record`,
`output_identity`, politica de captions/layout și identitatea selecției originale.
Observațiile temporare sunt eliminate dintr-o copie, fără modificarea deciziei
primite. Evaluarea editorială, feedback-ul și actualizarea DB rămân în worker.

Apelanți: exportul normal, `replan_and_rerender`, `rerender_pilots` și probele
camera/letterbox. Preview-ul rapid rămâne intenționat la rezoluție redusă.
Instrumentele experimentale de rampă și randare manuală nu sunt exporturi de
producție și nu au fost migrate în acest lot.

Re-planificarea are `--output-dir DIR`: scrie în `DIR/project`, refuză fișierele
de probă deja existente și păstrează exporturile curente. Fără această opțiune,
copierea generației precedente rămâne obligatorie. Replay-ul unui plan stocat
folosește opțiunile sale originale; dacă politica de captions sau opțiunile
encoderului lipsesc, refuză și indică re-planificarea. Nu ghicește politica din ASS.
Proba letterbox își scrie acum și ASS-ul în directorul probei, nu în exports.

Două defecte demonstrate în timpul testării:

- Randarea statică nu înregistra filtrul efectiv. După conectare, cititorul
  comenzii raporta încă `caption_filter=false` pentru `[v]subtitles=...`:
  lipsea recunoașterea separatorului `]`. Corectat și verificat pe pixeli decodați.
- Eliminarea pauzelor pe o sursă statică fără audio introducea totuși `[0:a]`
  în filtru. Ramura audio este acum condiționată de existența pistei.

## Verificare

Suita completă: **1933 passed, 2 deselected**. Cele două excluderi sunt
`test_tiktok_transform::test_list_endpoint_ok` și `::test_create_rejects_invalid_url`;
ambele au fost rulate și au reprodus 404-ul preexistent înainte de excludere.

Testele de integrare folosesc encoderul real, surse sintetice și DB temporară.
Patru perechi normal/replan acoperă static/dinamic, burn/suppress, pauze eliminate
și audio prezent/absent. Fișierele fiecărei perechi sunt identice la nivel de octet;
testul citește și pixelii subtitrării din MP4. Două perechi suplimentare verifică
replay-ul planului stocat după schimbarea setărilor curente, inclusiv un ASS orfan
în cazul suppress. Teste separate verifică anularea și eșecul encoderului.

Patru ferestre pilot complete, prin plannerul curent și executorul comun:

| Proiect / clip | Durată MP4 | Abatere | Strat ClipForge în comandă |
|---|---:|---:|---|
| pilotf81b / 30d7c6d4eae5 | 18.133 s | 23 ms | suppress / false |
| pilotee0e / c04e7960179b | 18.533 s | 13 ms | burn / true |
| pilot6b38 / a6cf2a591a2c | 18.467 s | 17 ms | burn / true |
| pilot2c8a / 20c670227eda | 15.600 s | 0 ms | burn / true |

Toate patru: 1080×1920, 30 fps, decodare integrală fără erori, amprentă v2 validă,
identitate MP4 reverificată după randare. Inventar SHA-256 înainte/după pentru
301 fișiere existente `.mp4`, `.json`, `.ass`: **0 modificate, 0 adăugate**.

Artefacte locale: `data/codex-analysis-20260909/shared-export/verification.json`,
`exports-before.json`, `pilots/<project>/<clip>.mp4` și cadrele de la 3 secunde.

## Limitele rezultatului

Inspecția câte unui cadru la 3 s arată în continuare textul sursei tăiat lateral
pe go ghost, benzile/elementele UI ale sursei pe Moist și un cadru dominat de
perdea și o porțiune de umăr pe pilotul românesc. Nu s-a dat verdict de vizionare
integrală și nu s-a măsurat rata de clipuri postabile. Verificările de mai sus
închid consistența execuției pe cazurile testate; nu închid calitatea montajului.

Următorul lot: încadrare împreună cu subtitrările, demonstrată prin videoclipuri,
apoi comparația editorială legacy/v2 în prezentare echivalentă. Politicile și
adnotările experimentale nu au fost declarate automat pe corpus.
