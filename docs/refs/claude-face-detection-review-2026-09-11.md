# Review independent Codex — lotul A delegat lui Claude

**Raport istoric.** Defectele de mai jos au fost corectate de Codex, iar Claude
a revizuit candidatul final. Starea curentă este în
[raportul lotului A închis](clipper-face-observations-2026-09-12.md).
Instrucțiunile de reluare de la sfârșit descriu blocajul din 11 septembrie;
nu trebuie aplicate peste candidatul final.

**Stare la 11 septembrie 2026, aproximativ 20:40 București: DE REVIZUIT.**
Ultimul commit acceptat rămâne `386e694`. Codul de mai jos este o lucrare
intermediară necomisă, nu un lot livrat. Aplicația nu a fost repornită cu el.

## Delegare și blocaj

Utilizatorul a autorizat explicit Claude Code să citească și să modifice
`F:\ClipForge`, transmițând către Claude.ai codul necesar. Autentificarea a fost
reînnoită de utilizator. Nu cere din nou această aprobare.

Brief: `docs/refs/claude-face-detection-batch-a-2026-09-11.md`.
Sesiune Claude: `fd2c5474-b1d2-4c91-bc2e-da744f2029a0`.
Metadate: `data/claude-face-detection/batch-a/session-02.json`.
CLI: `C:\Users\vlado\.local\bin\claude.exe`.

Claude a atins limita sesiunii (429); mesajul său indică resetarea la **00:30,
Europe/Bucharest**, adică 12 septembrie față de momentul acestui raport.
Ultima reluare, `claude-stream-04.jsonl`, s-a oprit înainte de aplicarea
instrucțiunilor din `feedback-02.txt`. Nu confunda trimiterea lor cu executarea.

## Ce a implementat până acum

- A extras detectorul Haar și `face_presence` în `face_detector.py`, cu
  re-exporturi din `signals.py`; pragurile detectorului nu au fost schimbate.
- A adăugat state/frame_index/decoded_t și a păstrat câmpurile prin
  `dynamic_window`, plus contoare noi în `caption_faces`.
- A început teste ale stărilor și cu video real.
- Nu a livrat încă inventarul Speed, raportul final și toate verificările
  cerute. Modelul neural și tracking-ul nu fac parte din acest lot.

## Verificări făcute de Codex

1. Prima versiune citea POS_MSEC înainte de read și raporta timpul cadrului
   precedent. Proba independentă `review-a/frame-clock.mp4` codifică alt nivel
   de gri per cadru. Cererile .16/.31/.56 s identifică prin pixeli cadrele
   2/3/6, dar prima versiune raporta .1/.2/.5 s în loc de .2/.3/.6 s.
   `initial-clock-counterexample.json` păstrează rezultatul inițial.
2. După corecția lui Claude, aceeași probă trece și la rerularea Codex:
   `clock-fix-independent.json`. Această reparație particulară este confirmată.
3. `review-a/check_contract.py` reproduce probleme rămase: POS_FRAMES=NaN
   aruncă ValueError; -1 este acceptat; 2.5 este trunchiat la 2. Starea necunoscută
   este numărată ca empty; legacy cu boxes nu intră în contorul legacy; un
   sample unreadable cu boxes poate muta subtitrarea. Vezi `contract-first.json`.
4. Testul video al lui Claude permite încă un cadru întreg de abatere. Codex
   a substituit numai în procesul testului `decoded_t -= 1/fps`, apoi a rulat
   testul real al lui Claude: **testul a trecut și cu defectul reintrodus**.
   `clock-test-mutation.json`: `mutant_rejected: false`. Codul de producție nu
   a fost modificat de această verificare.
5. Codex a rulat independent:
   `server/.venv/Scripts/python.exe -B -m pytest server/tests/test_clipper_face_detector.py server/tests/test_clipper_caption_faces.py server/tests/test_clipper_signals.py -q --tb=short`
   Rezultat: **51 passed în 9,59 s**. Acest rezultat nu anulează punctele 3–4.

Toate probele de review sunt în `data/claude-face-detection/review-a/`.

## Reluare

Reia aceeași sesiune Claude cu conținutul
`data/claude-face-detection/batch-a/feedback-02.txt`, care conține cerințele
corectoare complete. Folosește loguri noi; nu suprascrie probele vechi.
Reamintește-i și proba de mutație, produsă după oprirea pentru limită.

Nu accepta lotul înainte de validarea indicilor, tratarea coerentă a stărilor,
identificarea în date a ceasului ferestrei re-encodate, testul video care respinge
decalajul inițial și proba Speed cerută. Rulează independent probele Codex și
testele relevante; apoi suita server dacă nu mai sunt defecte deschise.
Actualizează harta și handover-ul, apoi un singur commit pentru lotul acceptat.

Fișiere Claude în lucru: `server/services/clipper/{face_detector,signals,dynamic_window,caption_faces}.py`,
`server/tests/test_clipper_{face_detector,caption_faces}.py`.
Scripturile auxiliare `check_faces_probe.py` și `probe_faces.py` din rădăcină
au fost create de Claude în acest lot; nu le amesteca cu livrabilul și păstrează
probele în data/. Există multe modificări ale utilizatorului în alte module;
nu le modifica și nu le include în commit.
