# Specificație — scraper de surse (umple automat sheet-urile cu linkuri)

Scop: să nu mai adaugi manual linkuri. Un script listează periodic videoclipurile
conturilor-sursă și **scrie doar coloanele NR + LINK** pe primele rânduri libere,
sărind peste ce există deja. Restul lanțului (transcriere, descriere, randare,
upload, postare) rămâne exact cum e azi — dispecerele îl preiau singure.

Fișier propus: **`D:/clipforge/scripts/feed_scraper.py`**
Stare/cache: **`D:/clipforge/data/scraper_state.json`**

Verificat pe 2026-07-29 pe rig, cu `server/.venv` (yt-dlp 2026.06.09, Python 3.13.1).

---

## 1. Ce există deja și se refolosește (nu introducem tehnologie paralelă)

| Ce | Unde | Cum îl folosește scraper-ul |
|---|---|---|
| yt-dlp (deja instalat în `server/.venv`) | `server/services/downloader.py` | Singurul mod de listare. **Nu** adăugăm Playwright, Selenium, API oficial TikTok/Meta sau parsare HTML. |
| `validate_url()` | `downloader.py:124` | Validare ieftină a fiecărui link înainte de scriere (http(s), ≤2000 caractere). |
| `fetch_metadata()` | `downloader.py:138` | **Doar în modul `--deep`**, opțional, pentru durată/dată exactă per video. Întoarce `{error, error_code, suggestion}` în loc să arunce — exact ce ne trebuie ca un video stricat să nu blocheze restul. |
| `_classify_error()` | `downloader.py:93` | Clasificarea deja existentă: `login_required`, `private_video`, `geo_blocked`, `not_found`… O refolosim în log, nu rescriem alta. |
| `services.sheets._service()`, `write_cell()` | `server/services/sheets.py` | Citire/scriere în sheet, cu același token OAuth ca Drive. |
| Convenția de citire a sheet-ului FR | `scripts/herstory_dispatch.py:98` (`read_pending`, range `'Victoria'!A1:F400`) | Scraper-ul citește identic, ca să vadă aceleași rânduri ca dispecerul. |
| Regula NR ↔ nume fișier pe Drive | `herstory_dispatch.py:37` `NAME_RE = ^(\d+)(_p\d+)?\.mp4$` | **Constrângere dură:** NR trebuie să fie unic și stabil, altfel deduplicarea pe Drive a dispecerului se strică. |
| `MIN_ROW = 199` | `dual_dispatch.py:25` | Rândurile noi se adaugă la coadă (rând > 199), deci sunt preluate automat de dispecerul RO. |
| Filtrul „herytstory" | `dual_dispatch.py:212` | Dispecerul RO sare peste linkurile `herytstory`. Deci pista FR trebuie scrisă **doar** în sheet-ul FR. |

Ce **adăugăm** nou: o singură funcție de scriere pe interval (2 coloane × N rânduri
într-un apel), pentru că `write_cell` scrie o singură celulă și ar însemna 2×N apeluri.

---

## 2. Surse

Configurare în constantă în capul scriptului (nu fișier de config — o singură listă,
citită de om):

```python
SOURCES = [
  # name        | listing URL                                  | sheet_id            | tab       | limit
  ("herstory_tt", "https://www.tiktok.com/@herytstory",          FR_SHEET,  "Victoria", 60),
  ("herstory_yt", "https://www.youtube.com/@<handle>/shorts",    FR_SHEET,  "Victoria", 60),
  ("herstory_fb", "https://www.facebook.com/<page>/videos",      FR_SHEET,  "Victoria", 40),
]
```

Sheet FR: `<FR_SHEET_ID>`, tab `Victoria`,
`A=NR B=LINK C=TRANSCRIPT D=DESCRIERE_FR E=VIDEO_URL F=STATUS`.
Aceeași structură merge și pe sheet-ul mare RO (`A=NR B=LINK`), cu alt `SOURCES`.

---

## 3. Listarea contului cu yt-dlp (fără API oficial)

### 3.1 Ce am verificat efectiv azi

```
python -m yt_dlp --flat-playlist --playlist-end 5 \
  --print "%(id)s|%(title).60s|%(duration)s|%(timestamp)s|%(url)s" \
  "https://www.tiktok.com/@herytstory"
```

Rezultat real: **funcționează fără cookies și fără login**. Contul `@herytstory`
listează **792 videoclipuri**, cel mai nou primul, cu toate câmpurile de care avem
nevoie direct din listarea „flat" (fără request per video):

```
7667863696114224414|She Said My Daughter Was Faking 😡 ...|63|1785313688|https://www.tiktok.com/@herytstory/video/7667863696114224414
```

Apare un `WARNING: [tiktok:user] The extractor is attempting impersonation, but no
impersonate target is available` — `curl_cffi` **nu** e instalat în venv. Azi merge
și fără; dacă TikTok începe să dea 403/gol, primul lucru de încercat este
`pip install "yt-dlp[default,curl-cffi]"` în `server/.venv`. Nu-l instalăm preventiv.

### 3.2 Apelul din Python (nu prin subprocess)

```python
from yt_dlp import YoutubeDL

OPTS = {
    "quiet": True, "no_warnings": True, "skip_download": True,
    "extract_flat": "in_playlist",   # NU coborî în fiecare video — 1 request/pagină
    "playlistend": limit,            # niciodată toate cele 792
    "socket_timeout": 20, "retries": 2,
    "ignoreerrors": True,            # un video stricat = entry None, nu excepție
    "sleep_interval_requests": 2,    # anti-rate-limit
    "no_color": True,
}
with YoutubeDL(OPTS) as ydl:
    info = ydl.extract_info(listing_url, download=False)
entries = [e for e in (info.get("entries") or []) if e]
```

### 3.3 Câmpurile luate din fiecare entry

| Câmp | Sursă în entry | Ce facem cu el |
|---|---|---|
| `id` | `e["id"]` | **Cheia de deduplicare.** Nimic altceva. |
| `url` canonic | reconstruit, vezi 4.2 | Se scrie în coloana B. |
| `title` | `e["title"]` | Doar în log/`--dry`, ca să recunoști videoclipul. **Niciodată** cheie. |
| `duration` | `e["duration"]` (secunde) | Filtru mecanic: sărim < 10s și > 600s (configurabil). |
| `timestamp` | `e["timestamp"]` (epoch) | Filtru `--since`; ordonare cronologică la scriere (cel mai vechi primul, ca numerotarea să crească natural). |
| `uploader` | `e["uploader"]` / `e["channel"]` | Verificare de siguranță: dacă nu e contul așteptat, nu scriem. |

`--deep` (opțional) apelează `fetch_metadata()` per video pentru durată/dată când
listarea flat nu le dă (se întâmplă pe unele pagini Facebook). E lent — un request
per video — deci **nu e implicit**.

---

## 4. Deduplicare — cheia este ID-ul din URL, nu titlul

### 4.1 De ce nu titlul

- Titlul TikTok = descrierea postării: emoji + hashtag-uri, se poate **edita după
  publicare**, deci se schimbă între două rulări → același video ar fi adăugat de două ori.
- În listarea flat titlul vine **trunchiat** și cu newline-uri în interior (vezi
  ieșirea reală de la 3.1) — nu e stabil nici măcar ca formă.
- Același subiect e re-încărcat de cont cu altă legendă → titluri diferite, video diferit.
  Invers, conturile de story folosesc legende șablon („She Said…") → **titluri identice
  pentru videoclipuri diferite**. Titlul dă și fals-pozitive, și fals-negative.
- ID-ul e imutabil, e generat de platformă, apare în URL și e exact ce întoarce yt-dlp
  ca `id`. Comparație de șiruri, zero ambiguitate.

### 4.2 Regex-uri (normalizare URL → id)

```python
import re

RE_TIKTOK = re.compile(r"tiktok\.com/@[\w.\-]+/(?:video|photo)/(\d+)", re.I)
RE_YOUTUBE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})", re.I)
RE_FACEBOOK = re.compile(
    r"facebook\.com/(?:[^/?#]+/videos/(?:[^/?#]+/)?|watch/?\?(?:[^#]*&)?v=|reel/)(\d+)", re.I)
RE_SHORT = re.compile(r"^https?://(?:vm|vt)\.tiktok\.com/|^https?://fb\.watch/", re.I)

def video_key(url: str) -> str | None:
    """'tt:7631352705327320350' / 'yt:dQw4w9WgXcQ' / 'fb:123…', sau None."""
    for tag, rx in (("tt", RE_TIKTOK), ("yt", RE_YOUTUBE), ("fb", RE_FACEBOOK)):
        m = rx.search(url or "")
        if m:
            return f"{tag}:{m.group(1)}"
    return None
```

Cheia are prefix de platformă pentru că un ID YouTube (11 caractere) și unul TikTok
(19 cifre) n-au de ce să se ciocnească, dar un ID Facebook numeric ar putea semăna
cu unul TikTok.

URL-ul canonic scris în sheet se **reconstruiește**, nu se copiază:

```python
def canon(key, uploader):
    tag, vid = key.split(":", 1)
    return {"tt": f"https://www.tiktok.com/@{uploader}/video/{vid}",
            "yt": f"https://www.youtube.com/watch?v={vid}",
            "fb": f"https://www.facebook.com/watch/?v={vid}"}[tag]
```

**Capcană verificată azi:** `yt-dlp --print "%(webpage_url)s"` pe un link TikTok cu
parametri a întors linkul **cu tot cu `?is_from_webapp=1&sender_device=pc`**. Deci
`webpage_url` NU e canonicalizat de yt-dlp — canonicalizarea o facem noi, cu regex-ul
de mai sus. Linkurile deja existente în sheet au exact acei parametri, iar comparația
brută de string între ele ar rata orice potrivire.

### 4.3 Linkuri scurte (`vm.tiktok.com`, `vt.tiktok.com`, `fb.watch`, `youtu.be`)

`youtu.be/<id>` conține deja ID-ul → intră în `RE_YOUTUBE`, nu are nevoie de nimic.
`vm.tiktok.com/ZM…` și `fb.watch/…` **nu conțin ID-ul** → trebuie rezolvate *înainte*
de comparație, altfel un video deja în sheet sub formă scurtă va fi adăugat a doua oară:

```python
def resolve_short(url, cache):
    if url in cache:
        return cache[url]
    final = None
    try:
        # HEAD e adesea refuzat de TikTok -> GET cu stream, citim doar URL-ul final
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            final = r.geturl()          # urllib urmează redirect-urile singur
    except Exception:
        pass
    key = video_key(final or "")
    if not key:                          # ultimă instanță: 1 request yt-dlp
        try:
            with YoutubeDL({"quiet": True, "skip_download": True,
                            "extract_flat": True}) as ydl:
                info = ydl.extract_info(url, download=False)
            key = f"tt:{info['id']}" if info else None
        except Exception:
            key = None
    cache[url] = key                     # se salvează în scraper_state.json
    return key
```

Rezoluțiile se memorează permanent în `data/scraper_state.json` → un link scurt se
rezolvă **o singură dată în viață**, nu la fiecare rulare.

### 4.4 Unde se face comparația

Sursa de adevăr pentru dedup este **sheet-ul**, nu fișierul de stare:

1. Citim `'<TAB>'!A1:F400` (un singur apel).
2. Pentru fiecare rând cu B care începe cu `http`: `video_key(B)`; dacă e `None` și
   linkul e scurt → `resolve_short`. Rezultatul intră în `seen: set[str]`.
3. Din listarea yt-dlp păstrăm doar entry-urile cu `key not in seen`.

Dacă `scraper_state.json` e șters, dedup-ul funcționează în continuare (se pierde doar
cache-ul de linkuri scurte). Fișierul de stare **nu** e niciodată singura apărare.

---

## 5. Scrierea în sheet fără să strici rândurile existente

Reguli, în ordine:

1. **Nu se atinge niciodată o coloană în afară de A și B.** C–F (transcript, descriere,
   video, status) aparțin dispecerului.
2. **Nu se scrie pe un rând care are conținut în orice coloană** — nici măcar dacă B e gol.
   Un rând cu descriere dar fără link e o problemă de investigat, nu un loc liber.
   (Concret azi în sheet-ul FR: rândurile 2–9 au descriere fără video, rândurile 14–21
   au link fără descriere — scraper-ul le lasă complet în pace.)
3. Primul rând de scriere = `ultimul rând cu orice conținut + 1`. Rândurile 22–37, goale,
   se umplu în ordine; când se termină, se scrie în continuare (Sheets extinde grila).
4. **NR = max(NR numeric existent) + 1**, incremental. NR nu se reutilizează niciodată,
   nici după ștergerea unui rând, pentru că videoclipurile de pe Drive se numesc `<NR>.mp4`
   (vezi `herstory_dispatch.py:37`) — un NR reciclat ar face dispecerul să creadă că
   rândul nou e deja randat și să-l sară.
5. Se scriu **doar A și B**, ordonat cronologic crescător (cel mai vechi video primul),
   într-un singur apel pe interval:

```python
svc.spreadsheets().values().update(
    spreadsheetId=SID, range=f"'{TAB}'!A{start}:B{start+len(rows)-1}",
    valueInputOption="USER_ENTERED", body={"values": rows},   # rows = [[nr, url], ...]
).execute()
```

6. Nu se șterg și nu se mută rânduri niciodată. Un video șters de pe TikTok dispare din
   listare, dar rândul lui rămâne în sheet (dispecerul îl va marca `esuat` singur).
7. Scrierea e ultima operație. Dacă listarea a eșuat parțial, scriem ce am adunat; dacă
   a eșuat complet, nu atingem sheet-ul.

---

## 6. Limite reale și cum le tratăm

| Situație | Comportament cerut |
|---|---|
| **Rate limiting / 429** | `sleep_interval_requests: 2`, `playlistend` mic (60, nu 792), o sursă odată, secvențial. La 429/`Too Many Requests`: backoff 60s → 300s, maxim 2 reîncercări, apoi **abandonăm sursa** și trecem la următoarea. Ce am listat deja se scrie. Rulare recomandată: 2–3×/zi, nu în buclă. |
| **Cont privat / listare goală** | `extract_info` întoarce `None` sau `entries` gol → log `sursa X: 0 entries (privat sau extractor stricat)`, **zero scrieri**, exit code 0. Nu tratăm „gol" ca pe o eroare fatală, dar nici nu ștergem nimic. |
| **Video șters între listare și `--deep`** | `fetch_metadata()` întoarce `error_code=private_video`/`not_found` → sărim doar acel video, îl notăm în log, continuăm. Nu se scrie rând pentru el. |
| **yt-dlp eșuează pe UN video** | `ignoreerrors: True` + entry `None` filtrat + `try/except` per video în `--deep`. Un video stricat **nu** oprește restul listării. Contorul final raportează `n_ok / n_skip / n_err`. |
| **Extractor stricat de update TikTok/Facebook** | Log explicit + exit code 2 pentru sursa respectivă, ca watchdog-ul să poată alerta. Primul remediu: `pip install -U yt-dlp` în `server/.venv`; al doilea: `curl_cffi` (vezi 3.1). |
| **Impersonation warning** | Ignorabil azi (verificat că merge). Devine acțiune doar dacă listarea începe să întoarcă 0. |
| **Sheets 401/403** | `services.sheets` aruncă deja `SheetsScopeMissing` cu mesaj clar → îl lăsăm să iasă, cu exit code 1. Reconectare Drive din Settings. |
| **Duplicat cu ID diferit** (aceeași poveste re-postată pe alt cont) | Nedetectabil prin ID. Opțional, `--dry` semnalează perechile cu aceeași durată ±1s și titlu foarte similar ca **avertisment**, nu ca blocare. Decizia rămâne a omului. |

---

## 7. Ce NU poate face (explicit)

- **Conținut care cere login/cookies.** TikTok privat/friends-only, pagini și grupuri
  Facebook private, YouTube age-restricted. `downloader.py` clasifică deja asta ca
  `login_required`. Nu punem cookies în repo și nu automatizăm login-ul.
- **Facebook e best-effort.** Extractorul FB din yt-dlp e cel mai fragil dintre cele trei
  și cere cookies pe multe pagini. Așteptarea corectă: e prima sursă care se strică.
  TikTok e cea sigură (verificată azi), YouTube a doua.
- **Nu verifică Drive-ul** și nu știe dacă un video a fost deja randat — asta rămâne
  treaba dispecerelor, care fac dedup pe NR.
- **Nu scrie transcript, descriere, link de video sau status** și **nu pune joburi în
  coadă**. Nu consumă GPU și nu consumă credite ElevenLabs.
- **Nu judecă relevanța conținutului.** Filtrează doar mecanic (durată, dată, cont).
  Un video nepotrivit ajunge în sheet și trebuie șters manual.
- **Nu repară desincronizarea Drive ↔ sheet** (cele 11 videoclipuri franceze de pe Drive
  vs. 4 `VIDEO_URL` completate). Aia e o problemă de reconciliere, o rezolvă
  `herstory_dispatch.py` care rescrie linkul când găsește videoul pe Drive.
- **Nu descarcă nimic.** `skip_download` peste tot.

---

## 8. Plan de implementare, în pași mici

Fiecare pas se termină cu un scraper care rulează. Sub 500 de linii în total
(estimat ~280), conform regulii din CLAUDE.md.

**Pas 1 — schelet + listare (fără sheet).**
`scripts/feed_scraper.py` cu `SOURCES`, `list_source()` (yt-dlp flat) și `--dry`.
Tipărește `id | durată | dată | titlu | url` pentru fiecare entry. Zero acces la Sheets.
Test: `... feed_scraper.py --source herstory_tt --limit 5 --dry` → 5 linii, ca la 3.1.

**Pas 2 — chei și normalizare.**
`video_key()`, `canon()`, `resolve_short()` + `data/scraper_state.json` (cache).
Test: rulează `--dry` pe o listă de linkuri de test în `--check-keys "url1,url2"`,
inclusiv unul cu `?is_from_webapp=…` și unul `vm.tiktok.com` → aceeași cheie pentru
același video sub trei forme diferite.

**Pas 3 — citirea sheet-ului + dedup (tot fără scriere).**
`read_sheet()` (`A1:F400`, ca `herstory_dispatch.read_pending`), construiește `seen`,
calculează `start_row` și `next_nr`.
Test: `--dry` afișează exact:
```
sursa herstory_tt: 60 listate | 11 deja in sheet | 3 filtrate (durata/data) | 46 noi
primul rand liber: 38 | urmatorul NR: 37
ar scrie: rand 38 NR 37 https://www.tiktok.com/@herytstory/video/7667674810901613855
          rand 39 NR 38 ...
NU ating: rand 22 (are NR completat), rand 5 (are descriere)
```

**Pas 4 — scrierea (`values().update` pe A:B).**
Rulare reală limitată: `--source herstory_tt --limit 5` → 5 rânduri noi.
Verificare manuală în sheet: C–F neatinse, NR crescător, linkuri canonice.

**Pas 5 — dovada de idempotență.**
Imediat după pasul 4, din nou `--source herstory_tt --limit 5 --dry` →
**`0 noi`**. Dacă nu e 0, dedup-ul e greșit; nu se merge mai departe.

**Pas 6 — rezistență la erori.**
`ignoreerrors`, backoff pe 429, `try/except` per sursă, contoare finale, exit codes
(`0` ok, `1` config/Sheets, `2` sursă stricată).
Test: rulează cu o sursă inventată în `SOURCES` (`https://www.tiktok.com/@nu_exista_xyz`)
→ log clar, celelalte surse continuă, sheet neatins pentru sursa moartă.

**Pas 7 — YouTube + Facebook.**
Adaugă cele două surse `herstory_*` în `SOURCES`, testează cu `--dry` fiecare separat.
Dacă FB cere login, se lasă comentat în `SOURCES` cu motivul scris pe un rând.

**Pas 8 — programare.**
Sarcină în Task Scheduler (sau linie în `scripts/watchdog.ps1`), 2×/zi, **înainte**
de dispecere. Log în `data/logs/scraper.log`.

### Flag-uri

| Flag | Efect |
|---|---|
| `--dry` | Nu scrie nimic, nici în sheet, nici în state. Tipărește planul complet. **Implicit în primele 5 rulări.** |
| `--source <name>` | O singură sursă din `SOURCES`. |
| `--limit N` | Suprascrie `playlistend`. |
| `--since YYYY-MM-DD` | Ignoră videoclipurile mai vechi (pe `timestamp`). |
| `--deep` | Metadate per video prin `fetch_metadata()` (lent). |
| `--check-keys "url,url"` | Doar normalizare + cheie, offline. Pentru testarea regex-urilor. |

### Comanda de test

```powershell
D:\clipforge\server\.venv\Scripts\python.exe D:\clipforge\scripts\feed_scraper.py --dry
D:\clipforge\server\.venv\Scripts\python.exe D:\clipforge\scripts\feed_scraper.py --source herstory_tt --limit 5 --dry
D:\clipforge\server\.venv\Scripts\python.exe D:\clipforge\scripts\feed_scraper.py --source herstory_tt --limit 5
D:\clipforge\server\.venv\Scripts\python.exe D:\clipforge\scripts\feed_scraper.py --source herstory_tt --limit 5 --dry   # trebuie: 0 noi
```

---

## 9. Criterii de acceptare

1. Rulat de două ori la rând, a doua rulare adaugă **0 rânduri**.
2. Niciun rând existent nu are vreo celulă modificată (comparație înainte/după pe `A1:F400`).
3. Rândurile 22–37 din sheet-ul FR se umplu cu link-uri `@herytstory` valide și NR unice.
4. `herstory_dispatch.py --dry` vede imediat rândurile noi ca „de lucru".
5. O sursă căzută nu împiedică scrierea celorlalte.
6. Zero credite ElevenLabs consumate, zero joburi puse în coadă.
