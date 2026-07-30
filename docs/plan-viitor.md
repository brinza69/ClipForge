# Plan — ce trebuie făcut în continuare

Stare verificată **2026-07-30**. Cifrele de aici au fost citite din sheet-uri, din
Drive și din Buffer în ziua respectivă, nu estimate. Ce nu s-a putut verifica e
marcat explicit.

---

## 1. Unde suntem

| pistă | făcut | rămas | observație |
|---|---|---|---|
| narator (RO) | 36 / 224 | **188** | prioritatea 1 — le postezi manual |
| comentator (RO) | 47 / 224 | 177 | merge împreună cu naratorul |
| povestitor (RO) | 12 / 224 | — | are deja stoc de postat |
| herstory / Victoria (FR) | 20 videoclipuri pe Drive | **0 rânduri de randat** | a rămas fără linkuri, nu fără GPU |

23 de rânduri din sheet-ul mare n-au descriere. Descrierile **nu** consumă credite
ElevenLabs — se pot reface oricând, separat de randare.

**Stoc de postat:** Facebook 47 fișiere rămase din 57, TikTok RO 48 din 59.
Coada Facebook e plină (10/10) până sâmbătă 1 aug 13:00. TikTok RO are 1 slot liber.

---

## 2. Ce e rupt acum — de reparat primul

### 2.1 Interfața web nu pornește
`npm run dev` rămâne agățat la `Compiling /`. Măsurat: **0,02 secunde de CPU în 15
secunde reale** — nu compilează, e blocat. Nu e înfometare de disc: presiunea
scăzuse (benchmark-ul Next 1681ms → 470ms) și tot se agăța.

Am șters `.next` (remediul standard). **Netestat după ștergere** — unealta prin
care pornesc servere de dezvoltare era picată. Următorul pas: `npm run dev` și, dacă
se agață iar, e ceva în aplicație, nu în cache. Suspectul principal e legătura
`/tiktok` din bara laterală, care duce la o pagină inexistentă.

### 2.2 Wizardul TikTok e pe jumătate construit
Există: `services/tiktok_transform/` (8 module), tipurile de job, banda de coadă,
linkul din meniu.
**Lipsesc:** `routers/tiktok.py`, `workers/tiktok_pipeline.py`, `src/app/tiktok/`,
`src/components/tiktok/`, `src/types/tiktok.ts`.

Consecințe reale: 2 teste picate cu 404 (suita e 22 trecute / 2 picate), și un
element de meniu care duce în gol. Ori se termină, ori se scoate linkul din meniu.
PRP-ul există: `PRPs/tiktok-transformation.md`.

### 2.3 Dashboardul nu s-a putut genera
`scripts/build_dashboard.py` e scris și verificat sintactic, dar Buffer a intrat în
**rate limit (250 cereri/24h)** înainte să-l pot rula. Se deblochează singur.
Rulează-l după ce trece limita:

```bash
cd D:\clipforge; server\.venv\Scripts\python.exe scripts\build_dashboard.py
```

Apoi e la `http://localhost:8420/exports/dashboard.html`.

---

## 3. Cozile de producție, în ordine

**1. narator + comentator (RO) — 188 de rânduri.** Rulează acum. `MIN_ROW=2`, deci
ia de la începutul sheet-ului. La 2 plăci × ~40 min/rând, teoretic ~70 rânduri/zi,
realist mult mai puțin (rândurile vechi au linkuri moarte care se resping).

**2. herstory (FR) — 0 rânduri, dar 772 de linkuri gata de adăugat.** Canalul e pe
zero stoc de postat. Ca să repornească, trebuie scrise linkuri în sheet (§4.1).
Asta e cea mai urgentă acțiune de conținut din tot planul.

**3. povestitor (RO).** Are 47 + 48 fișiere de postat. Nu se randează până se
golesc primele două cozi.

**Comutarea între cozi:** dispecerul RO e `scripts/dual_dispatch.py`
(`PRESETS`, `MIN_ROW`), cel francez `scripts/herstory_dispatch.py`. Watchdog-ul e
instanță unică și **nu repornește un dispecer care deja rulează** — dacă schimbi
configurația, omoară procesul și lasă-l pe watchdog să-l ridice.

---

## 4. Automatizări de construit

### 4.1 Scraper — umplerea sheet-urilor (următorul pas concret)
Spec completă: `docs/spec-scraper.md`. Deja demonstrat că funcționează:

- `yt-dlp --flat-playlist` pe `@herytstory` listează **793 videoclipuri fără
  cookies**, cu `id`, `title`, `duration`, `timestamp`, `view_count`, `like_count`.
- Deduplicare pe **id de video**, nu pe titlu. Cele **60 de linkuri scurte**
  `vm.tiktok.com` din sheet-uri au fost rezolvate întâi — altfel suprapunerile
  ascunse în ele scapă. Rezultat: 195 id-uri cunoscute → **772 noi**.
- Din cele 772: 346 sub 90s (o parte), 401 → 2 părți, 25 → 3 părți. Adică ~1223
  de fișiere livrabile, peste un an la 3 postări/zi.

**Rămâne de decis** câte se scriu și în ce ordine (cronologic sau după `view_count`).
Sortarea după popularitate e posibilă — câmpul există.

Pentru pista română sursa echivalentă e **@hisytstory** (49 de linkuri deja
folosite). Încă nefiltrat.

**Capcană de reținut:** titlurile de pe @herytstory sunt în engleză. Pentru pista
română limba trebuie verificată la final — exact așa au scăpat `101` și `102`.

### 4.2 Verificarea limbii, permanentă
Metoda care a funcționat: ffmpeg ia ~20s de audio direct din URL-ul de Drive
(un fișier de 100 MB costă câțiva MB), faster-whisper `tiny` spune limba.
Rezultat pe povestitor: 67 fișiere, 64 română, 2 engleză.
De mutat din scratchpad în `scripts/` și de rulat automat înainte de fiecare lot de
postare.

### 4.3 Pornire automată la deschiderea PC-ului
Făcut: sarcină programată la logon care pornește `watchdog.ps1`, iar watchdog-ul
ridică restul. **Local, nu prin Claude** — rutinele din cloud nu pot atinge plăcile
video de pe acest PC. De verificat că a supraviețuit unui restart complet.

### 4.4 Colectorul de stare
`tracks_state.py` (starea celor 3 piste din sheet-uri) e încă în scratchpad-ul
temporar. Dashboardul citește `data/tracks_state.json`. De mutat în `scripts/`,
altfel dashboardul afișează zerouri când folderul temporar e curățat.

---

## 5. Riscuri, în ordinea probabilității

| risc | de ce | ce se vede |
|---|---|---|
| **credite ElevenLabs** | 188 rânduri × 2 roluri ≈ 376 randări cu voce. Cota **nu se poate citi** (cheie scoped, `/v1/user` dă 401). | rândurile pică la etapa de voce; se reiau singure la rulare următoare |
| linkuri moarte în rândurile vechi | TikTok cu restricție de vârstă cere cookies | respingere 422, rândul e marcat și sărit (nu mai blochează backendul) |
| rate limit Buffer | 250 cereri/24h pe Free | orice script de postare refuză; dashboardul cade pe cache |
| PC-ul adoarme | randările în curs se pierd | de-dup-ul face ca repornirea să nu re-randeze ce s-a urcat deja |
| pene de rețea la Google | timeout SSL/handshake | **rezolvat** — dispecerul reîncearcă în 30s în loc să moară |

---

## 6. Decizii care te așteaptă

1. **Câte linkuri @herytstory scriu în sheet-ul FR** și ordinea — cronologic sau după `view_count`.
2. **Wizardul TikTok:** îl terminăm sau scoatem linkul din meniu?
3. **Cele 5 `neidentificate`** — nu sunt în sheet, iar 4 din 5 se potrivesc pe același
   rând, deci pot fi aproape-duplicate. Le postăm sau le lăsăm?
4. **Branch-ul `claude/portable-setup`** nu e unit în `main`. Îl unim?
5. **Id-ul sheet-ului mare rămâne expus** în `HANDOVER-2026-06-25.md` și
   `n8n/clipforge-tiktok-poster.json` dintr-un commit vechi. Curățarea cere
   rescrierea istoriei publice — decizie separată. Alternativa simplă: repo privat.
