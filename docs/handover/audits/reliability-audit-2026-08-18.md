# ClipForge — Audit de fiabilitate și reziliență

**Data:** 18 august 2026  
**Scop:** audit complet al aplicației pentru reducerea riscului de blocare, pierdere de date și job-uri rămase suspendate.  
**Exclus explicit:** secțiunea TikTok nu face parte din acest audit.

## Verdict executiv

ClipForge are o bază bună și frontend-ul principal trece build-ul și typecheck-ul, însă aplicația nu este încă suficient de robustă pentru a fi prezentată ca proiect `production-grade`.

Cele mai mari riscuri sunt:

- job-uri care pot rămâne blocate;
- două procese backend care pot procesa același job;
- procese externe fără deadline global;
- inconsistențe între SQLite și fișiere;
- upload-uri care pot consuma memoria sau discul;
- erori ignorate în frontend;
- migrații de bază de date care pot eșua fără să oprească aplicația;
- teste care nu oferă încă un semnal de încredere.

Nu există o garanție realistă de zero erori. Obiectivul corect este ca orice eroare să devină vizibilă, recuperabilă și izolată: job-ul trebuie să ajungă într-o stare terminală, coada trebuie să continue, iar datele valide nu trebuie șterse.

## Dovezi din starea actuală

- frontend build: trece;
- TypeScript typecheck: trece;
- ESLint: 84 de erori în `src`;
- teste backend fără testul TikTok: 578 trecute, 21 eșuate, 51 cu erori;
- multe erori de test provin din incompatibilitatea dintre `pytest` și `pytest-asyncio`, deci test harness-ul nu este stabil;
- mai multe componente frontend depășesc limita de 500 de linii, ceea ce crește riscul de regresii și state coupling.

Concluzia este că aplicația funcționează ca demo, dar încă nu poate demonstra reziliență la restart, timeout, anulare, disk full, upload mare sau backend indisponibil.

## Probleme critice — P0

### P0-1 — Coada de job-uri poate executa același job de mai multe ori

Fișier principal: [`server/job_queue.py`](../server/job_queue.py)

Coada păstrează în memorie job-urile active, anulările și task-urile locale, în timp ce mai multe procese backend pot folosi aceeași bază SQLite.

Riscuri:

- un proces poate considera un job blocat și îl poate reintroduce în coadă, deși alt proces încă îl execută;
- un job anulat într-un proces poate fi finalizat ulterior de alt proces;
- `complete_job()` poate transforma un job `cancelled` în `done`;
- două procese pot executa aceeași analiză sau același export;
- un worker vechi poate suprascrie progresul unui worker nou.

Soluție recomandată:

1. Pentru SQLite, rulează un singur queue worker activ.
2. Dacă sunt necesare mai multe procese, introdu:
   - `worker_id`;
   - `lease_expires_at`;
   - heartbeat periodic;
   - `attempt_count`;
   - `cancellation_requested`;
   - tranziții atomice de stare.
3. Finalizarea trebuie permisă doar worker-ului care deține lease-ul:

   ```sql
   UPDATE jobs
   SET status = 'done'
   WHERE id = ?
     AND status = 'running'
     AND worker_id = ?;
   ```

4. Adaugă teste cu două worker-e, restart în timpul job-ului și anulare concurentă.

### P0-2 — Migrațiile SQLite pot eșua silențios

Fișier principal: [`server/database.py`](../server/database.py)

Mai multe modificări de schemă sunt înconjurate de `except Exception: pass`. Aplicația poate porni chiar dacă schema este incompletă sau incompatibilă.

Riscuri:

- baza de date este blocată;
- permisiunile sunt greșite;
- SQL-ul este invalid;
- o coloană nu a fost creată;
- indexurile lipsesc;
- eroarea apare mult mai târziu într-un endpoint aparent nevinovat.

Soluție:

- tabel `schema_migrations` cu versiuni numerotate;
- ignorarea numai a erorilor cunoscute, precum `column already exists`;
- orice altă eroare oprește startup-ul;
- backup automat înainte de migrare;
- lock pentru migrare;
- readiness dezactivat până când schema este validă.

Aplicația nu trebuie să pornească într-o stare parțial migrată.

### P0-3 — Procese externe fără timeout global

Fișiere relevante:

- [`server/services/upscaler.py`](../server/services/upscaler.py)
- [`server/services/inpaint.py`](../server/services/inpaint.py)
- [`server/services/downloader.py`](../server/services/downloader.py)
- workers care rulează FFmpeg, yt-dlp, Real-ESRGAN sau alte binare externe.

Real-ESRGAN este pornit cu `Popen()` și monitorizat printr-un loop, dar nu are un deadline real de execuție. Dacă procesul intră în deadlock, pierde GPU-ul sau rămâne blocat, job-ul poate rămâne `running` pentru totdeauna.

Downloader-ul are timeout-uri de rețea și retry-uri, dar nu are o limită globală suficient de strictă pentru descărcarea completă. Un blocaj înainte de progress hook poate împiedica și anularea.

Soluție comună pentru toate procesele externe:

- deadline global per job;
- timeout separat pentru fiecare etapă;
- kill al întregului process tree pe Windows;
- `wait()` după kill pentru a confirma oprirea;
- cleanup garantat pentru fișiere parțiale;
- verificare de output după proces;
- coduri de eroare standardizate;
- marcarea job-ului `failed`, niciodată suspendat indefinit.

`inpaint` are deja o protecție de wall-clock, ceea ce este un model bun care trebuie extins și uniformizat.

### P0-4 — Uploadurile pot consuma memoria și au limite incompatibile

Probleme identificate:

- unele endpoint-uri folosesc `await file.read()` și încarcă tot fișierul în RAM;
- backend-ul clipper acceptă fișiere foarte mari;
- proxy-ul Next este configurat la `200mb`;
- uploadul clipper trece prin proxy, deci limita frontend-ului nu corespunde limitei backend-ului.

Soluție:

- upload în stream, pe chunk-uri;
- scriere directă pe disc;
- verificarea limitei incremental;
- verificarea spațiului înainte de upload;
- upload chunked/resumable pentru fișiere mari;
- checksum final;
- progress real în frontend;
- cleanup TTL pentru upload-uri abandonate.

Nu este suficient să crești limita proxy-ului la zeci de GB.

### P0-5 — Baza de date și filesystem-ul pot deveni inconsistente

Fișiere relevante:

- [`server/routers/clipper.py`](../server/routers/clipper.py)
- [`server/services/clipper/storage.py`](../server/services/clipper/storage.py)
- [`server/services/cleanup.py`](../server/services/cleanup.py)

Exemple:

- fișierul este mutat, dar commit-ul DB eșuează;
- commit-ul DB reușește, dar fișierul nu este mutat;
- artifact-ul este scris, dar statusul job-ului nu se actualizează;
- proiectul este șters din DB, dar fișierele rămân;
- cleanup-ul raportează spațiu eliberat chiar dacă ștergerea a eșuat;
- cleanup-ul poate șterge workspace-ul întregului proiect și rezultate valide.

Soluție:

- workspace separat pentru fiecare job;
- directoare distincte pentru input, scratch, artifacts și exports;
- scriere în fișier temporar;
- checksum și marker `.complete`;
- mutare atomică după finalizare;
- manifest de artefacte;
- reconciliere DB/filesystem la startup;
- raportarea cleanup-ului ca succes numai după verificarea ștergerii.

Existența unui fișier nu trebuie să fie suficientă pentru a-l considera valid.

## Probleme importante — P1

### P1-1 — Ștergerea proiectului are race conditions

În prezent, ștergerea încearcă să anuleze job-urile, elimină fișierele și apoi șterge rândurile DB. Un worker care încă rulează poate încerca ulterior să actualizeze proiectul sau să scrie fișiere.

Soluție:

- stare intermediară `deleting`;
- proiectul devine inaccesibil imediat;
- worker-ul verifică starea proiectului înaintea fiecărei etape;
- cleanup-ul se execută ca job separat;
- rândurile nu dispar până când cleanup-ul nu confirmă succesul;
- tombstone pentru job-urile care termină după ștergere.

### P1-2 — Tranzițiile de stare nu sunt suficient de stricte

Trebuie definită o mașină explicită de stări:

```text
queued → running → done
queued → cancelled
running → cancelling → cancelled
running → failed
failed → queued
```

Tranziții invalide trebuie refuzate:

- `cancelled → done`;
- `done → running`;
- `failed → done`;
- proiect șters → job nou.

Validarea trebuie aplicată atomic în DB, nu doar în Python.

### P1-3 — Pornirea analizelor poate crea job-uri duplicate

Endpoint-urile de start, patch settings și auto-export verifică existența job-urilor active, apoi fac commit separat. Două request-uri simultane pot trece ambele verificarea.

Soluție:

- idempotency key pentru acțiunile de start;
- index unic sau lock logic pentru `(project_id, stage, active)`;
- tranzacție atomică pentru verificare + creare job;
- răspuns consistent pentru double-click.

### P1-4 — Eșecul unui clip poate marca întreg proiectul ca failed

Un failure la export, preview sau la un singur clip poate schimba statusul întregului proiect în `failed`, chiar dacă restul rezultatelor sunt utilizabile.

Soluție:

- status separat pentru proiect, etapă și clip;
- `partial_success` pentru proiecte cu rezultate valide;
- eroare izolată la clipul afectat;
- retry doar pentru etapa eșuată.

### P1-5 — Health check-ul nu verifică readiness

`/api/health` indică doar că procesul răspunde. Nu verifică dacă aplicația poate efectiv procesa job-uri.

Introdu:

- `/health/live` — procesul există;
- `/health/ready` — DB, disk, FFmpeg, worker și dependențele critice sunt disponibile;
- status separat pentru GPU și provider-ele externe;
- mesaj clar în frontend când backend-ul nu este ready.

### P1-6 — Frontend-ul folosește două strategii API incompatibile

Unele pagini folosesc proxy-ul same-origin `/worker-api`, iar altele folosesc direct `http://localhost:8420`.

Asta produce probleme de CORS, deployment și URL-uri invalide în producție.

Soluție:

- un singur API client;
- toate request-urile printr-un singur transport;
- timeout și abort centralizat;
- verificare centralizată a `response.ok`;
- mesaje de eroare standardizate;
- retry doar pentru request-uri idempotente.

### P1-7 — Erorile critice sunt ignorate în frontend

În unele locuri, inclusiv pornirea automată a analizei, erorile sunt prinse cu `catch` gol sau ignorate.

Utilizatorul poate ajunge într-un proiect fără analiză activă și fără să știe de ce.

Pentru orice acțiune critică trebuie să existe:

- mesaj vizibil;
- retry;
- status verificabil;
- posibilitate de reluare;
- diferențiere între timeout, backend offline, input invalid și failure intern.

### P1-8 — Polling-ul poate deveni costisitor și fragil

Polling-ul fix la 1–4 secunde poate multiplica request-urile când există mai multe tab-uri sau mai mulți utilizatori.

Soluție:

- backoff progresiv;
- pauză când tab-ul este ascuns;
- jitter pentru evitarea sincronizării request-urilor;
- heartbeat SSE;
- reconnect controlat;
- limită de conexiuni per job și utilizator;
- fallback la polling numai când SSE eșuează.

### P1-9 — Artefactele pot fi considerate valide când sunt depășite

Verificarea bazată în principal pe versiune și dimensiunea sursei nu detectează sigur:

- înlocuirea sursei cu un fișier de aceeași dimensiune;
- schimbarea modelului;
- schimbarea promptului;
- schimbarea versiunii FFmpeg;
- schimbarea setărilor;
- artefacte parțial scrise.

Soluție: fingerprint al sursei și manifest cu source hash, settings hash, versiune pipeline, versiune model, versiune schema, checksum și marker de completare.

### P1-10 — Scrieri non-atomice pe disc

Mai multe componente folosesc direct `write_text()` sau `write_bytes()` pentru configurări și metadate.

O întrerupere poate lăsa JSON invalid sau fișier parțial.

Toate scrierile persistente trebuie să folosească:

1. fișier temporar;
2. flush;
3. replace atomic;
4. backup opțional;
5. lock când există scrieri concurente.

### P1-11 — Lipsesc limitele de backpressure

Trebuie limitate explicit:

- numărul de job-uri queued;
- job-urile active per proiect;
- durata maximă a inputului;
- numărul de cadre intermediare;
- utilizarea maximă a discului;
- memoria estimată;
- vârsta job-urilor abandonate.

Când limita este depășită, request-ul trebuie refuzat clar, nu lăsat să blocheze coada.

## Calitatea codului și mentenanța

Fișierele frontend foarte mari, precum:

- [`src/app/remix/page.tsx`](../src/app/remix/page.tsx);
- [`src/app/captions/page.tsx`](../src/app/captions/page.tsx);
- [`src/app/tts/page.tsx`](../src/app/tts/page.tsx);
- [`src/app/transcript/page.tsx`](../src/app/transcript/page.tsx),

conțin prea multă logică într-un singur loc.

Trebuie separate:

- API calls;
- polling;
- validare;
- state machine;
- componente vizuale;
- download logic;
- error handling.

Erorile ESLint, în special `any`, trebuie reduse. Tipurile slabe nu sunt doar o problemă de stil; ele ascund bug-uri de runtime.

## Plan de remediere

### Faza 1 — Blocaje și pierderi de date

1. Un singur worker SQLite sau lease-uri complete.
2. State machine pentru job-uri.
3. Timeout global pentru toate procesele externe.
4. Kill al process tree pe Windows.
5. Upload streaming și limite reale.
6. Cleanup pentru fișiere parțiale.
7. Repararea compatibilității `pytest`/`pytest-asyncio`.

### Faza 2 — Persistență și consistență

1. Migrații versionate.
2. Workspace per job.
3. Manifest și checksum.
4. Scrieri atomice.
5. Cleanup verificabil.
6. Reconciliere DB/filesystem la startup.
7. Delete flow bazat pe cleanup job.

### Faza 3 — API și frontend

1. Un singur API client.
2. Eliminarea URL-urilor directe către localhost.
3. Timeout și abort pentru toate request-urile.
4. Eliminarea `catch {}` pentru acțiuni critice.
5. Retry explicit și mesaje utile.
6. Readiness state în UI.
7. Polling cu backoff și visibility handling.

### Faza 4 — Observabilitate

Fiecare job trebuie să aibă:

- `job_id`;
- `project_id`;
- `worker_id`;
- `attempt`;
- `started_at`;
- `finished_at`;
- `last_heartbeat`;
- `failure_code`;
- mesaj sigur pentru utilizator;
- detaliu tehnic în log;
- durată;
- utilizare estimată de disk și memorie.

Logurile nu trebuie să conțină token-uri API, cookie-uri sau date sensibile.

### Faza 5 — Teste de reziliență

Adaugă teste pentru:

- restart backend în timpul jobului;
- două worker-e simultan;
- cancel în fiecare etapă;
- crash în timpul scrierii unui artefact;
- disk full;
- upload întrerupt;
- FFmpeg lipsă;
- FFmpeg blocat;
- GPU indisponibil;
- API extern cu timeout;
- proiect șters în timpul procesării;
- retry după failure;
- double-click pe Start;
- două exporturi simultane;
- artefact corupt;
- backend offline în frontend.

## Criterii de acceptanță pentru versiunea CV-ready

Aplicația poate fi prezentată ca proiect serios când:

- toate testele rulează într-un mediu curat și sunt verzi;
- niciun job nu poate rămâne `running` fără deadline;
- anularea este persistentă și respectată de orice worker;
- restart-ul nu dublează job-uri;
- uploadurile mari nu consumă memoria procesului;
- cleanup-ul nu șterge rezultate valide;
- schema DB nu poate rămâne parțial migrată;
- frontend-ul afișează orice eroare critică;
- există health/readiness checks;
- fiecare risc P0 are cel puțin un test automat de regresie.

## Concluzie

ClipForge este promițător ca produs și arată bine ca demo. Pentru a deveni proiectul principal din CV, trebuie însă închise mai întâi riscurile de infrastructură: ownership-ul job-urilor, timeout-urile, consistența storage-ului, migrațiile SQLite, uploadurile mari și test harness-ul.

Acestea sunt elementele care separă o aplicație care „funcționează pe laptop” de o aplicație serioasă, predictibilă și rezistentă la erori.
