# Claude — lot A: observații de față adresabile și stări distincte

Utilizatorul a delegat implementarea către Claude; Codex orchestrează și verifică
independent. Acest fișier este sarcina completă pentru primul lot. Implementează
doar lotul A, apoi oprește-te și predă rezultatul. Nu începe înlocuirea detectorului
sau urmărirea fețelor în această rundă.

## Context minim

Repo: `F:\ClipForge`, branch existent `claude/ai-stream-clipper`, HEAD de referință
`386e694`. Citește `CLAUDE.md`, PRP relevant și intrările necesare din
`docs/clipper-map.md`; nu reciti toate handover-urile. Starea recentă este în
`docs/refs/clipper-caption-faces-2026-09-11.md`. Tratează constatările ca ipoteze
de verificat. Sunt modificări ale utilizatorului în alte module; păstrează-le.

Detectorul actual este în `server/services/clipper/signals.py`:
`face_cascades`, `detect_faces`, `face_presence`. Folosește Haar, nu îl schimba
în acest lot. `face_presence` produce aceleași `boxes: []` când nu poate citi
cadrul, detectorul lipsește sau a citit un cadru fără detecții. `t` reprezintă
timpul cerut la seek. `dynamic_window.analyse_window` copiază numai t și boxes,
deci metadatele noi ar dispărea dacă ai repara numai detectorul.

## Livrabil

1. Contract explicit pentru fiecare observație, păstrând compatibilitatea
   necesară a lui `t`/`boxes`: cadru citit cu detecții sau fără detecții, cadru
   necitibil, detector indisponibil/eroare. Un cadru citit fără detecție nu
   demonstrează că nu există o față. Nu promova un eșec tehnic la absență.
2. Înregistrează timpul cerut, indexul cadrului efectiv decodat și timestampul
   decodat atunci când backendul le poate furniza. Refuză sau marchează ca
   indisponibil ceea ce nu poate fi stabilit. Probează convenția OpenCV pentru
   POS_FRAMES/POS_MSEC înainte și după read; nu deduce indexul doar din fps*t.
3. Păstrează metadatele prin fereastra de analiză și până la consumatorul local
   `caption_faces`. Identifică precis ce numește indexul: cadrul din fișierul
   analizat, nu automat cadrul sursei originale. Fereastra este re-encodată
   după seek; adăugarea startului cerut nu certifică timestampul sursei.
   Nu schimba tăcut ceasul plannerului și nu eticheta `source_requested` drept
   timp decodat exact. Datele legacy fără noile câmpuri rămân necunoscute.
4. Raportul de evitare a fețelor separă cadrele citite fără detecții de cadrele
   necitite și detectorul indisponibil. Nu promova coverage_complete la true.
   Metadatele de lucru continuă să fie eliminate din planul livrat.

## Probe obligatorii

- Teste pentru detector indisponibil, cadru necitibil, cadru valid fără
  detecții, cadru valid cu detecții și metadate legacy. Fără fallback care
  inventează detecții, timestampuri sau dimensiuni.
- Test cu video REAL care schimbă conținutul la fiecare cadru și cereri de
  seek nealiniate la grila sa. Verifică indexul/timpul împotriva pixelilor
  decodați, nu împotriva aceleiași formule folosite în implementare.
- Test de integrare care pornește din observație și arată că stările ajung
  în decizia de captions; nu este suficient un test al structurii returnate.
- O probă read-only pe Speed `d789060e273d`, proiect `2d3375ee3420`, fereastră
  386.47–404.37 s. Salvează observațiile și inventarul stărilor în
  `data/claude-face-detection/batch-a/`. Datele sursei sunt locale.
- Rulează testele țintite; poți rula și suita server. Cele două excluderi
  cunoscute sunt `tests/test_tiktok_transform.py::test_list_endpoint_ok` și
  `tests/test_tiktok_transform.py::test_create_rejects_invalid_url`.

## Limite de lucru

Fără schimbări de UI, DB, scoring, praguri Haar, tracking sau model neural.
Fără re-ștampilare de sidecar-uri, rescriere de exports/, ștergeri de artefacte,
instalări globale, reset/checkout sau schimbarea politicilor de captions/layout.
Nu modifica setări de securitate, autentificare ori permisiuni. Nu porni alți
agenți. Nu modifica fișierele pe care le-a editat utilizatorul.

Fișierele schimbate trebuie să rămână <=500 linii. signals.py are deja peste
limită; dacă îl atingi, extrage numai funcțiile coerente necesare acestei
reparații, păstrând importurile publice și comentariile utile. Actualizează harta.
Nu face commit încă: Codex va verifica diff-ul, va rula independent testele și
va decide commitul lotului. Aceasta este o instrucțiune a orchestratorului în
cadrul delegării autorizate de utilizator.

## Predare compactă

Scrie `data/claude-face-detection/batch-a/RESULT.md`: fișiere schimbate,
contractul final, teste/comenzi și rezultate, căile probelor, limite și orice
blocaj. Păstrează outputurile lungi în fișiere. În mesajul final: maximum
15 rânduri și calea raportului. Nu declara lotul acceptat; verdictul îl dă Codex.
