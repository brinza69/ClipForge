# Runbook — pornirea întregului proces pe un cont nou

Ghid complet pentru a replica fabrica de conținut pe alt cont: de la link în sheet
până la postarea programată. Scris pe baza a ceea ce a funcționat efectiv, cu
capcanele care ne-au costat timp marcate explicit.

---

## 1. Ce face lanțul, pe scurt

```
link în sheet
   ↓  dispecer (dual_dispatch / herstory_dispatch)
descarcă (yt-dlp) → șterge subtitrările originale (OCR + inpaint)
   ↓
transcrie (faster-whisper, local) → curăță/traduce textul (OpenAI)
   ↓
voce sintetică (ElevenLabs) → potrivire viteză + ardere subtitrări (un singur encode)
   ↓
suprapune avatarul (chroma key) → taie în părți dacă e nevoie
   ↓
urcă pe Google Drive → scrie linkul + descrierea în sheet → status "ready"
   ↓  script de postare (buffer)
programează pe TikTok/Facebook prin Buffer
```

Randarea cere GPU și credite ElevenLabs. Descrierile și transcrierile **nu** cer
credite ElevenLabs — se pot face oricând.

---

## 2. Ce conturi îți trebuie

| Serviciu | Pentru ce | Cost |
|---|---|---|
| Google (Drive + Sheets) | sheet-ul de lucru + stocarea video | gratuit |
| ElevenLabs | vocea sintetică | **plan plătit obligatoriu** — vezi §4 |
| OpenAI | curățarea transcrierii + descrieri | consum mic |
| Buffer | programarea postărilor | Free merge, cu limite (§7) |
| TikTok / pagină Facebook | destinația | — |

---

## 3. Google — Drive și Sheets

1. În aplicație, **Connect Google Drive**. Dacă e nevoie de link manual:
   `curl -X POST http://127.0.0.1:8420/api/drive-auth/connect` → întoarce `auth_url`.
2. **Capcană dovedită:** aplicația OAuth e în modul "Testing", deci tokenul
   **expiră cam săptămânal**. Când expiră, tot lanțul se oprește (nu poate citi
   sheet-ul, nu poate urca). Se rezolvă doar reconectând din browser.
3. **După ORICE reconectare**, copiază tokenul pe al doilea backend, altfel
   backendul B strică tokenul comun:
   ```
   cp data/drive_oauth_token.json  data_b/
   cp data/drive_oauth_client.json data_b/
   ```
4. Creează câte un folder Drive pentru fiecare rol/personaj. Id-ul lui se pune în
   presetul rolului (`drive_folder`).

---

## 4. ElevenLabs — capcana care ne-a blocat o zi

Cheia se pune din interfață sau direct:

```python
CLIPFORGE_DATA_DIR=data   python -c "import sys;sys.path.insert(0,'server');
from services.elevenlabs import set_api_key; set_api_key('CHEIA')"
# apoi la fel cu CLIPFORGE_DATA_DIR=data_b
```

**Vocile românești/franceze profesionale sunt „library voices". Pe planul Free ele
NU pot fi folosite prin API**, oricâte adaugi în cont. Eroarea e:

```
402 paid_plan_required: Free users cannot use library voices via the API.
```

Semnul distinctiv: vocile standard (Adam, George) funcționează, cele profesionale
nu. **Trebuie plan plătit.** După plată funcționează direct după ID, fără să mai
adaugi ceva în cont.

**Cheile sunt „scoped":** `/v1/voices` merge, dar `/v1/user` dă 401 — deci **cota
rămasă nu se poate citi**. Când se termină creditele afli doar prin eșecul
rândurilor la etapa de voce (`quota_exceeded`). Nu există avertisment în avans.

Test rapid că o cheie e bună pentru rolurile tale:

```python
import json, urllib.request
k = "CHEIA"
for rol, vid in (("narator","8nBBDfYxYXmDNaqTCxPH"), ("comentator","0okaJWIq26j9LWMEOE8N")):
    b = json.dumps({"text":"Test.","model_id":"eleven_multilingual_v2"}).encode()
    r = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                               data=b, headers={"xi-api-key":k,"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as x: print(rol, "OK", len(x.read()))
    except Exception as e: print(rol, "ESUAT", e)
```

---

## 5. Sheet-ul

Două scheme, în funcție de pistă.

**Sheet-ul mare (română, mai multe roluri):**

```
A=NR | B=LINK | C=TRANSCRIPT | D=DESCRIERE(ro) | E=descriere_fr
F=narator_url | G=comentator_url | H=povestitor_url | I=status | J=victoria_url | K=status_fr
```

**Sheet dedicat unui singur personaj** (recomandat pentru un cont nou — mai curat):

```
A=NR | B=LINK | C=TRANSCRIPT | D=DESCRIERE | E=VIDEO_URL | F=STATUS
```

Reguli învățate:

- **Numele fișierelor video vin din coloana A.** `<NR>.mp4`, sau `<NR>_p1.mp4`,
  `<NR>_p2.mp4` când e împărțit. De asta numerotarea contează.
- **Nu refolosi același NR pe două rânduri.** S-a întâmplat (rândurile 149/150 cu
  NR 147) și face imposibilă legarea fișierelor de rânduri.
- Dacă renumerotezi sheet-ul, **redenumește și fișierele de pe Drive**, altfel
  deduplicarea nu le mai găsește și re-randează tot degeaba.
- Un rând se consideră „de făcut" dacă îi lipsește descrierea **sau** videoul de
  pe Drive. Doar pe descriere nu e destul.

---

## 6. Presetele de rol (voce + avatar)

`data/variant_presets/<rol>.json` și **identic în `data_b/`** (fiecare backend își
citește propriul folder — dacă uiți al doilea, o placă randează cu setări vechi).

Câmpurile care contează:

```json
{
  "tts_engine": "elevenlabs",
  "tts_voice_id": "<id voce>",
  "tts_language": "ro",
  "tts_speed": 1.1,
  "tts_stability": 1.0,
  "tts_similarity": 1.0,
  "split_into_parts": true,
  "match_to_source_duration": true,
  "caption_template_id": "bold_impact",
  "drive_folder": "<id folder Drive>",
  "commentator_preset_id": "<rol>"
}
```

Avatarul stă în `data/commentators/<rol>/` (`video.mp4` + `meta.json`).
**Cheia de fundal verde se ia din `meta.json` al avatarului, nu din presetul de
variantă** — dacă `chroma_key` e gol în preset, e normal.

Ieșirea e forțată la **1080×1920, 60fps** (`speed_match.py` are `target_fps=60`).

Regula de împărțire: un clip **sub 90s rămâne întreg**; peste, se taie în bucăți
de 60s, iar restul devine parte separată doar dacă are ≥30s, altfel se pliază în
ultima (care ajunge 60-89s).

---

## 7. Buffer — conectare și limite

1. Cont pe buffer.com → **Settings → API → Personal Key**.
2. Salveaz-o în `data/buffer_config.json` (folderul `data/` e în `.gitignore`):
   ```json
   { "api_key": "..." }
   ```
3. Conectează canalele: avatar (stânga jos) → **Channels** → **Connect**.
   - **TikTok:** login normal.
   - **Facebook:** alegi **Page**, te loghezi cu profilul personal care e admin.
     **Acceptă TOATE paginile la ecranul de permisiuni** — dacă limitezi acolo,
     Buffer nu-ți mai găsește niciun cont. Filtrezi la pasul următor.
     Profilurile personale **nu** se pot conecta, doar Pagini.

**Limitele planului Free, verificate:**

| Limită | Valoare |
|---|---|
| Canale simultane | 3 |
| Conectări unice **pe viață** | 8 — se consumă și de canalele deconectate |
| Postări în coadă | **10 per canal** (se eliberează pe măsură ce publică) |
| Postări/zi TikTok | 25 (limita rețelei) |
| Postări/zi Facebook | 35 |

Deconectarea unui canal **șterge definitiv** coada, analiticele și istoricul lui.

---

## 8. API-ul Buffer (GraphQL)

Endpoint `https://api.buffer.com`, antet `Authorization: Bearer <cheie>`.

```graphql
mutation P($i: CreatePostInput!) {
  createPost(input: $i) {
    ... on PostActionSuccess { post { id status dueAt } }
    ... on MutationError { message }
  }
}
```

Input:

```json
{
  "channelId": "...",
  "text": "descrierea = captionul",
  "schedulingType": "automatic",
  "mode": "shareNow | customScheduled | addToQueue",
  "dueAt": "2026-07-30T06:00:00Z",
  "assets": [{ "video": { "url": "<URL public>" } }],
  "metadata": { "tiktok": { "isAiGenerated": true },
                "facebook": { "type": "reel" } }
}
```

Detalii care ne-au costat timp:

- `channels(input:{organizationId})` — cere id-ul organizației, îl iei din `account`.
- **`editPost` ÎNLOCUIEȘTE postarea.** Dacă nu retrimiți `assets`, videoul dispare
  și primești „TikTok posts require at least one image or video". Citește
  activul existent și trimite-l înapoi împreună cu `dueAt`.
- `thumbnailUrl` pe un video dă `InvalidInputError` — folosește
  `metadata.thumbnailOffset` (milisecunde).
- Filtrarea cozii pe canal: `posts(input:{organizationId, filter:{channelIds:[...]}})`.

---

## 9. CAPCANA CEA MAI IMPORTANTĂ — linkurile de Google Drive

Buffer **nu acceptă upload de fișiere**. Cere un URL public de unde descarcă el.

Forma obișnuită `https://drive.google.com/uc?export=download&id=<ID>`
**servește pagina HTML de scanare antivirus** pentru fișierele peste ~100 MB.
Buffer primește HTML în loc de video și postarea eșuează.

**Folosește mereu:**

```
https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=t
```

Verificat că întoarce `Content-Type: video/mp4` real la orice dimensiune
(testat de la 13 MB până la 115 MB). Adăugarea lui `confirm=t` la forma veche
**nu** funcționează.

Fișierele trebuie să rămână accesibile **până la ora publicării** — Buffer le ia
atunci, nu la programare. Mutarea între foldere e sigură (ID-ul nu se schimbă),
ștergerea nu.

---

## 10. Ordinea pornirii

```bash
# 1. tot stack-ul (backend per GPU + frontend)
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -NoComfy -NoLlm

# 2. supraveghetorul (repornește singur ce cade)
powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1

# 3. verifică ce ar face dispecerul, fără să trimită nimic
server\.venv\Scripts\python.exe scripts\herstory_dispatch.py --dry
```

Dashboard: `http://localhost:8420/exports/live.html` · loguri: `data/dispatch.log`.

**Reține:** watchdog-ul e instanță unică și **nu repornește un dispecer care deja
rulează**. Dacă schimbi configurația sau vrei reluate rânduri marcate ca
încercate, omoară procesul `dual_dispatch` / `herstory_dispatch` și lasă
watchdog-ul să-l repornească.

---

## 11. Probleme întâlnite și cauza reală

| Simptom | Cauza reală |
|---|---|
| „Un GPU nu merge", placa la 0% | **Nu placa.** Dispecerul se învârtea pe un rând respins cu 422, de 725 de ori. Un rând nevalid nu mai trebuie să blocheze backendul. Verifică `data/dispatch.log` înainte de drivere. |
| Rând respins cu 422 | Sursa nu se poate descărca — TikTok cu restricție de vârstă cere cookies. Permanent, nu se rezolvă prin reîncercare. |
| Rânduri picate la „Generating voice" | Credite ElevenLabs epuizate. Cota nu se poate citi în avans (cheie scoped). |
| Descrieri lipsă la rândurile picate | Descrierile se generează **ultima etapă**, după randare. Dacă jobul moare la voce, se pierd — deși depind doar de transcriere. Se pot reface separat, fără credite. |
| Videoclipuri duplicate pe Drive | `files().create()` creează mereu fișier nou, Drive acceptă nume identice. Deduplicarea verifică numele `<NR>.mp4` înainte de urcare. |
| Postare fără caption | Statusul `ready` trebuie pus **doar** când există și link și descriere. |
| Fabrica moare peste noapte | PC-ul adoarme. Sarcina programată trebuie să aibă `StartWhenAvailable=True`, altfel o oră ratată nu se recuperează niciodată. |
| 2 procese `python.exe` per rol | **Normal.** Venv-ul e un lansator care pornește interpretorul real. Nu „curăța" duplicatele — omori procesul real. |

---

## 12. Reguli de conținut, ca să nu pierzi contul

- **Conținut neoriginal:** republicarea materialului altcuiva fără permisiune e
  împotriva regulilor pe ambele platforme. Meta ia amprenta fiecărui video și
  reține cine l-a urcat primul; sancțiunea e **distribuție redusă pe tot ce
  postează contul** plus pierderea monetizării. TikTok scoate din For You.
  Adăugarea de subtitrări sau schimbarea vitezei sunt enumerate explicit ca
  modificări insuficiente; o narațiune nouă suprapusă e argumentul cel mai
  puternic în favoarea originalității, dar nu e o garanție.
- **Eticheta AI:** vocea sintetică intră sub obligația de declarare pe TikTok.
  Câmpul e `metadata.tiktok.isAiGenerated`. Platforma detectează automat
  conținut AI nedeclarat și dă strike direct.
- **Nu pune conținut de risc pe contul care găzduiește o Pagină monetizată.**
  Sancțiunile pentru neoriginalitate ating accesul la monetizare al contului.
- Paginile importante să aibă **doi admini** — dacă profilul unic cade, Pagina
  devine inaccesibilă.
- Conturile personale duplicat (același nume, dispozitiv, IP) sunt detectate și
  suspendate. O Pagină construită pe un astfel de cont dispare odată cu el.

---

## 13. Scripturi

| Fișier | Ce face |
|---|---|
| `scripts/start_all.ps1` | pornește backend per GPU + frontend |
| `scripts/watchdog.ps1` | supraveghează și repornește tot |
| `scripts/dual_dispatch.py` | dispecerul român (mai multe roluri, sheet-ul mare) |
| `scripts/herstory_dispatch.py` | dispecerul francez (un rol, sheet propriu) |

Ambele dispecere acceptă `--dry`: arată ce ar face, fără să trimită nimic.
**Folosește-l mereu înainte de o rulare pe date noi.**
