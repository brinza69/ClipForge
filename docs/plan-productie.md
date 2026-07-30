# Plan de productie video — cozi de prioritate

Verificat 2026-07-29. Cifrele vin din `tracks_state.json` (228 randuri sheet mare + 36 randuri
sheet FR), `data/pov_inventory.json`, `data/fb_post_list_povestitor.json`, `data/pov_post_list.json`
si din codul dispecerelor. Nimic estimat "din cap".

---

## 0. Debitul real al rigului

Un rand = un job `parallel_pipeline` pe un GPU, cu toate rolurile cerute in acelasi job. ~40 min/rand.

| Scenariu | Randuri/ora | Randuri/zi |
|---|---|---|
| 1 GPU | 1,5 | 36 |
| 2 GPU, 24/7, zero pierderi | 3 | 72 |
| 2 GPU, 24/7, 80% uptime (reporniri, esecuri) | 2,4 | ~57 |
| 2 GPU, doar 12h/zi (PC treaz ziua) | 3 | 36 |

Planifica pe **~57 randuri/zi**. 72 e limita teoretica, nu s-a atins niciodata.

---

## 1. Starea celor 3 cozi

| Coada | De randat | Ore la 2 GPU (24/7) | La 80% uptime | Prioritate |
|---|---|---|---|---|
| 1. narator + comentator (sheet mare RO) | **182 randuri** | 61 h ≈ 2,5 zile | ~3,2 zile | 1 |
| 2. herstory FR (sheet Victoria) | **9 randuri** | 3 h | ~4 h | 2 |
| 3. povestitor | 0 acum (are stoc) | — | — | 3 |

Total coada 1 + 2 = 191 randuri = **~64 ore de rig continuu**, adica 2 zile si 16 ore la 24/7,
sau ~3,4 zile la uptime realist.

---

## Coada 1 — narator + comentator (sheet mare, RO)

### Cifre

| | Valoare |
|---|---|
| Randuri in sheet | 228 (224 cu link) |
| Randuri RO utilizabile (cu link, fara @herytstory) | 213 |
| Au narator | 31 → **lipsa 182** |
| Au comentator | 40 → **lipsa 173** |
| Lipsa ambele roluri (1 job face 2 videoclipuri) | 173 |
| Lipsa doar narator | 9 |
| Lipsa doar comentator | 0 |
| Fara descriere RO | 13 (randurile 25, 63, 168-172, 174, 175, 182, 193, 194, 209) |
| Marcate "ready" | 40 |
| Marcate "esuat" | 1 |

### BLOCAJ CRITIC — MIN_ROW

`scripts/dual_dispatch.py` linia 25:

```python
MIN_ROW = int(os.environ.get("CLIPFORGE_DISPATCH_MIN_ROW", "199"))
```

La 199, dispecerul vede doar **27** din cele 213 randuri RO, iar dintre ele doar **8** au narator
lipsa. **174 din cele 182 de randuri de facut sunt sub randul 199 si sunt invizibile.**
Rigul se va opri dupa ~3 ore si va parea "gata".

Distributia randurilor lipsa narator:

| Bloc randuri | Cate |
|---|---|
| 2-24 | 21 |
| 25-49 | 25 |
| 50-74 | 25 |
| 75-99 | 24 |
| 100-124 | 24 |
| 125-149 | 25 |
| 150-174 | 18 |
| 175-199 | 12 |
| 200-229 | 8 |

**Fix: coboara MIN_ROW la 2.** Vezi sectiunea 4 — variabila de mediu NU e suficienta cand
dispecerul e pornit de watchdog.

### Config pentru coada 1

| Fisier | Linie | Acum | Pune |
|---|---|---|---|
| `scripts/dual_dispatch.py` | 34 | `PRESETS = ["narator", "comentator"]` | ramane la fel |
| `scripts/dual_dispatch.py` | 25 | `MIN_ROW ... "199"` | `"2"` |
| `scripts/dual_dispatch.py` | 29 | ambele backend-uri | ramane (ambele GPU pe coada 1) |

Verificare inainte de pornire:

```
D:\clipforge\server\.venv\Scripts\python.exe D:\clipforge\scripts\dual_dispatch.py --dry
```

Trebuie sa listeze ~182 randuri. Daca listeaza 8, MIN_ROW n-a fost schimbat efectiv.

---

## Coada 2 — herstory FR (sheet Victoria)

### Cifre reale

| Stare | Randuri | Ce se intampla |
|---|---|---|
| link + descriere + video (gata) | 4 (randurile 10-13, NR 9-12) | nimic |
| link + descriere, fara video (randurile 2-9, NR 1-8) | 8 | vezi mai jos |
| link, fara descriere, fara video (randurile 14-21, NR 13-20) | 8 | **de randat** |
| fara link (randurile 22-37, NR 21-36) | 16 | blocate pe scraper |

### Sheet-ul e desincronizat de Drive — 7 randuri se rezolva GRATIS

Pe Drive sunt 11 videoclipuri FR: NR 1,2,3,4,6,7,8,9,10,11,12 (lipseste 5). Sheet-ul are doar
NR 9-12 completate. `herstory_dispatch.py` listeaza folderul Drive la fiecare pas
(`drive_numbers()`, `NAME_RE = ^(\d+)(?:_p\d+|_part\d+of\d+)?\.mp4$`) si pentru un rand al carui
video exista deja **scrie linkul in coloana E si sare randul** — fara GPU, fara credite.

Rezultat la prima rulare:

- randurile 2, 3, 4, 5, 7, 8, 9 (NR 1,2,3,4,6,7,8) → **doar rescriere de link**, ~1 minut total;
- randul 6 (NR 5) → de randat;
- randurile 14-21 (NR 13-20) → de randat + descriere.

**Deci coada 2 reala = 9 randuri de randat, nu 32.** ~3 ore la 2 GPU.

Conditia: fisierele de pe Drive sa fie numite `<NR>.mp4` / `<NR>_p1.mp4`, exact conventia
verificata de `NAME_RE`. Un fisier numit altfel nu e recunoscut si randul se re-randeaza.

### Config pentru coada 2

Script separat, nu se atinge de sheet-ul mare:

```
D:\clipforge\server\.venv\Scripts\python.exe D:\clipforge\scripts\herstory_dispatch.py --dry
```

| Fisier | Linie | Valoare |
|---|---|---|
| `scripts/herstory_dispatch.py` | 24-26 | `SID` = `1cW00MxCZvX6eGj-3PZGCoZLnQes4q42KbTkjs0cwQ9c`, `TAB` = `Victoria` |
| `scripts/herstory_dispatch.py` | 27 | `PRESET = "victoria"` |
| `scripts/herstory_dispatch.py` | 28 | `DRIVE_FOLDER = "19cbqRJWO8R0fWQ7GLuwJMRFvVhqssWHU"` |
| `scripts/herstory_dispatch.py` | 34 | `LANG = "fr"` |

Nu e nimic de editat — se porneste asa cum e. Se poate suprascrie sheet-ul/tab-ul cu
`CLIPFORGE_FR_SHEET` / `CLIPFORGE_FR_TAB`.

### `victoria_dispatch.py` nu se confunda cu asta

`scripts/victoria_dispatch.py` lucreaza pe randurile @herytstory din **sheet-ul mare**
(coloanele E / J / K), cu `MIN_ROW = 211`. In sheet exista 11 randuri @herytstory:
156-162 (sub MIN_ROW, ignorate) si 211-214 (toate 4 au deja `victoria_url` + status buffer).
**Nu mai are de lucru.** Nu-l porni odata cu celelalte — ar consuma acelasi GPU si aceleasi credite.

---

## Coada 3 — povestitor

Nu se randeaza acum. Stocul acopera ~2 saptamani.

| Canal | Stoc | Fisiere | Postari | La 3 postari/zi |
|---|---|---|---|---|
| Facebook "Povestitorul" | `data/fb_post_list_povestitor.json` | 62 | 42 (37 NR + 5 neidentificate; 15 NR sunt in 2-3 parti) | ~14 zile |
| TikTok povestitorul.ro | `data/pov_post_list.json` | 48 ramase din 59 | 40 grupuri NR | ~16 zile |

Inventar total: `data/pov_inventory.json` — 67 fisiere, 4.172 MB. 64 RO, 2 EN (101.mp4, 102.mp4 —
deja publicate din greseala), 1 neverificat. 5 excluse: 100.mp4 si 124.mp4 (inlocuite de parti),
101/102 (engleza), 1 duplicat.

**Pornire coada 3 doar cand cozile 1 si 2 sunt goale**, prin `scripts/dual_dispatch.py` linia 34:

```python
PRESETS = ["narator", "comentator", "povestitor"]   # sau doar ["povestitor"]
```

`ROLE_COLS` (linia 47) mapeaza deja `povestitor` → coloana H, iar preset-ul exista
(`data/variant_presets/povestitor.json`, cu `split_into_parts: true`). Nu e nimic de adaugat.

---

## 4. Cum comuti efectiv intre cozi

### Regula de aur: un singur dispecer pe un GPU

`dual_dispatch.py` si `herstory_dispatch.py` au **acelasi** dict `BACKENDS` (linia 29 in ambele):

```python
BACKENDS = {"A(:8420)": "http://127.0.0.1:8420", "B(:8421)": "http://127.0.0.1:8421"}
```

Fiecare isi tine evidenta proprie in `inflight` si verifica joburile pornite doar la start
(`adopt_running`). Daca merg simultan, fiecare crede ca ambele GPU-uri sunt libere si trimit dublu.

**Optiunea A — exclusiv (recomandat pentru viteza maxima pe o coada):**
o singura coada ruleaza, ambele GPU-uri pe ea. Debit 3 randuri/h.

**Optiunea B — split pe GPU (ambele piste in paralel):**
in `dual_dispatch.py` linia 29 lasi doar `{"A(:8420)": "http://127.0.0.1:8420"}`,
in `herstory_dispatch.py` linia 29 lasi doar `{"B(:8421)": "http://127.0.0.1:8421"}`.
Debit 1,5 randuri/h pe fiecare coada. Coada 2 se termina in ~6 h, apoi dai GPU-ul B inapoi cozii 1.

### Watchdog-ul rescrie orice ai facut manual

`scripts/watchdog.ps1` linia 163 reporneste `dual_dispatch.py` in maxim 30 s daca a murit:

```powershell
Ensure-Proc 'dual_dispatch\.py' "$root\scripts\dual_dispatch.py" "$root\data\dispatch.log" ...
```

Consecinte:

1. **Nu porni dispecerul cu variabila de mediu din shell-ul tau.** `Start-Process` din watchdog
   mosteneste mediul procesului watchdog, nu al shell-ului tau. `CLIPFORGE_DISPATCH_MIN_ROW=2`
   setat in terminalul tau NU ajunge la procesul repornit de watchdog. Editeaza linia 25 din
   `dual_dispatch.py` sau seteaza variabila la nivel de utilizator si reporneste watchdog-ul.
2. **Ca sa treci pe herstory in mod exclusiv**, comenteaza linia 163 din `watchdog.ps1`, opreste
   dispecerul RO, porneste-l pe cel FR. Altfel cel RO revine singur in 30 s.
3. **Pastreaza redirectionarea stdout in `data\dispatch.log`** — `dual_status_writer.py` parseaza
   numerele de rand din acel fisier pentru dashboard-ul live.

Repornire dispecer dupa o modificare de config:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'dual_dispatch\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Watchdog-ul il reporneste cu noua configuratie in ≤30 s.

---

## 5. Creditele ElevenLabs — resursa care decide tot

### Cat costa fiecare coada

Mediana transcriptului RO masurat: **1.281 caractere** (medie 1.415, n=65). FR: medie **2.246**
caractere (n=4). O sinteza = un rol.

| Coada | Sinteze | Caractere estimate | % din total |
|---|---|---|---|
| 1. narator + comentator | 173×2 + 9×1 = **355** | ~502.000 | 96,1% |
| 2. herstory FR | 9×1 = **9** | ~20.000 | 3,9% |
| 3. povestitor | 0 acum | 0 | 0% |
| **Total pentru a goli cozile 1 + 2** | 364 | **~522.000** | |

### Cota nu se poate citi — nu incerca

Cheia e scoped. `GET /api/tts/elevenlabs/status` cheama `get_user_info()` → `/v1/user`, endpoint
la care cheile scoped nu au acces, deci raspunsul vine cu `error` populat, nu cu `info`.
Cheia e valida (are `voices_read` + `text_to_speech`), doar cota e ascunsa.

**Singurul semnal real e esecul unui job:**
`RuntimeError: ElevenLabs API error 401: ...` (terminal, fara retry — `elevenlabs.py:94`) sau
`429` (cu backoff prin `with_retry`).

### Ce se pierde exact cand pica creditele

Ordinea etapelor in `workers/remix_pipeline.py`:

```
_stage_download → _stage_transcribe → _stage_erase → _stage_audio_chain (ElevenLabs)
  → _stage_commentator → _stage_match_and_caption → _stage_descriptions
```

Vocea e la mijloc, **descrierile sunt ultimele**. Daca pica creditele, jobul moare la
`_stage_audio_chain` si **nu primesti nici descrierea**, desi transcrierea si stergerea s-au facut
deja. Nu exista "salvare partiala" a descrierii dintr-un job caruia i-au picat creditele.

### Ce se poate face fara ElevenLabs (cost zero)

| Actiune | Cum |
|---|---|
| Transcriere | `POST /api/transcript/upload` + `POST /api/transcript/clean` (faster-whisper local sau OpenAI) |
| Descriere RO/FR | `services/descriptions.py::generate_video_descriptions(original_description, transcript, engine, target_language)`, `engine="ollama"` = Qwen local, zero cost extern |
| Umplut cele 13 randuri RO fara descriere | acelasi apel, scriere in coloana D |
| Umplut cele 8 randuri FR fara descriere (14-21) | acelasi apel, `target_language="fr"`, coloana D din sheet-ul Victoria |
| Recuperat linkurile FR de pe Drive | pornesti `herstory_dispatch.py` — sare randurile cu video existent |

Un rand ramane cu status gol (nu "ready") pana cand are **si** link, **si** descriere — asa e scris
in ambele dispecere. Deci descrierile facute fara credite pregatesc randurile, dar nu le fac
postabile singure.

### Ordinea de consum a creditelor

1. **Coada 2 mai intai, ca burst de 3 ore.** Costa 20.000 caractere (3,9% din buget), goleste o
   coada intreaga si deblocheaza canalul FR care a ramas fara stoc. Intarzie coada 1 cu 3 ore.
2. **Coada 1, continuu**, pana la epuizarea creditelor sau a randurilor. 502.000 caractere.
3. **Coada 3 (povestitor), niciodata inainte de golirea primelor doua** — are stoc pentru ~14 zile
   pe Facebook si ~16 zile pe TikTok. Fiecare credit cheltuit aici e un credit furat de la
   narator/comentator, unde stocul e zero.

Daca creditele se termina la mijlocul cozii 1: opresti dispecerul, rulezi doar transcriere +
descrieri pe randurile ramase (cost zero), astfel incat la reincarcarea creditelor rigul sa faca
doar voce + render, nu si munca de LLM.

---

## 6. Riscuri

| Risc | Cauza reala, deja intalnita | Efect | Ce faci |
|---|---|---|---|
| **Rand respins 4xx** | TikTok cu restrictie de varsta — yt-dlp cere login. 1 rand deja in starea asta. | Jobul nu porneste. `dual_dispatch` marcheaza coloana I `esuat — 422: ...`, adauga randul in `done` si merge mai departe (`note_bad`, liniile 322-334). Nu mai blocheaza GPU-ul ore intregi, cum se intampla inainte. | `Select-String "SKIP — respins" D:\clipforge\data\dispatch.log`. Randul se rezolva manual sau se abandoneaza. |
| **PC-ul adoarme** | Setare de alimentare Windows. | Rigul sta. Watchdog-ul repornaste backend-urile la trezire, dar orele pierdute nu se recupereaza — la 40 min/rand, o noapte pierduta = ~30 randuri. | Verifica planul de alimentare inainte de a lansa o coada de 3 zile. |
| **Watchdog mort** | Mutex `Local\ClipForgeWatchdog` + lock `data\watchdog.lock`; un lock ramas de la o sesiune veche poate face un watchdog nou sa iasa instant. | Nimic nu mai reporneste nimic. | `Get-Content D:\clipforge\data\watchdog.log -Tail 5` — heartbeat la fiecare 20 tick-uri (~10 min). Fara heartbeat recent = mort. |
| **Duplicate pe Drive** | Dedup se face dupa numele `<NR>.mp4`. `drive_upload.upload_files()` refuza duplicate cu acelasi nume; cache-ul Drive tine 900 s (`DRIVE_CACHE_TTL`). | Un rand **fara NR in coloana A** nu poate fi verificat pe Drive si se poate re-randa la infinit. Randul 182 e exact in situatia asta. | Completeaza coloana A inainte de a porni coada 1. |
| **Sheet desincronizat de Drive** | 11 videoclipuri FR pe Drive vs 4 linkuri in sheet. | Fara `herstory_dispatch`, 7 randuri s-ar re-randa degeaba (7 × 40 min = ~4,7 ore de GPU + credite aruncate). | Porneste `herstory_dispatch.py` — recupereaza linkurile automat. Nu randa FR manual. |
| **Doua dispecere pe acelasi GPU** | Ambele au acelasi `BACKENDS` si nu se vad reciproc dupa pornire. | Joburi dublate, coada per GPU se dubleaza, credite consumate de doua ori pe acelasi rand. | Optiunea A sau B din sectiunea 4. Niciodata ambele pe ambele porturi. |
| **Randuri fara descriere raman nepostabile** | 13 randuri RO + 8 randuri FR. | Statusul nu devine "ready", posterul nu le ia. | Genereaza descrierile fara ElevenLabs (sectiunea 5). |
| **Job 9.mp4 esuat pe Buffer (94,7 MB)** | Eroare tranzitorie de media la upload. | O postare FR lipsa. | Se re-creeaza postarea; nu necesita re-randare — videoclipul exista. |

---

## 7. Ordinea de executie, pe scurt

1. Completeaza coloana A pe randul 182 (sheet mare).
2. Porneste `herstory_dispatch.py --dry`, confirma ca listeaza randurile 6 si 14-21 ca de randat si
   restul ca "video deja pe Drive". Ruleaza-l ~3-4 ore, pana se goleste. (~20k caractere)
3. Opreste-l. Editeaza `dual_dispatch.py` linia 25 → `MIN_ROW = 2`. Verifica cu `--dry` ca vezi
   ~182 randuri, nu 8.
4. Reporneste dispecerul RO cu ambele GPU-uri. ~2,5-3,5 zile continuu. (~502k caractere)
5. Cand coada 1 e goala, adauga `"povestitor"` in `PRESETS` (linia 34).
6. Daca creditele pica pe parcurs: opreste vocea, ruleaza transcriere + descrieri (cost zero) pe
   randurile ramase, ca la revenirea creditelor rigul sa faca doar voce + render.
