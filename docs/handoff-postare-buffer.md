# Handoff — postare Povestitor (romana) pe Buffer

Pentru o sesiune Claude care face **DOAR distributie**. Nu randeaza, nu produce
video, nu atinge pipeline-ul. Ia fisiere gata de pe Google Drive, le pune
descrierea potrivita si le programeaza pe Buffer.

Verificat pe rig la 25 august 2026.

---

## 1. Ce canale sunt ale tale

| canal | retea | ce posteaza | profil |
|---|---|---|---|
| `povestitorul.ro` | TikTok | **romana** | `tiktok` |
| `Povestitorul` | Facebook | **romana** | `facebook` |

**AMANDOUA canalele sunt pe romana din 29 august 2026.** Engleza a tinut patru
zile (TikTok de pe 27 aug, Facebook de pe 25) si s-a inchis. Daca planul
romanesc se goleste, **spui — nu comuti singur pe engleza.**

Profilurile `tiktok_en` si `facebook_en` refuza acum sa porneasca, si asa
trebuie sa ramana. Fisierele engleze raman pe Drive, nepostate.

**Fara coperta, nicaieri.** Clipurile se posteaza asa cum sunt, fara cardul de
la inceput. Copertile mai exista doar pe 19 fisiere ENGLEZE care nu se mai
posteaza — le ignori.

**Un singur stoc conteaza: folderul romanesc.** 51 de videoclipuri / 74 de
fisiere la 29 august, si creste — pe rig randeaza povestitorul romanesc cu voce
locala gratuita (F5), rand cu rand din sheet.

Folderul `POVESTITOR ENGLEZA` ramane pe loc, dar **nu-l mai atingi**.

**Comenzile de mai jos sunt scrise cu `server\.venv\Scripts\python.exe` pentru
ca asa arata pe rigul de randare.** Pe un aparat care doar posteaza nu exista
venv si nici nu trebuie — dupa `pip install -r server/requirements-postare.txt`
scrie simplu `python`.

**NU sunt ale tale** si nu le atingi, nici macar ca sa le citesti coada:
`Contouse` si `journal.dune.conteuse` (pista franceza) raman pe celalalt cont
Buffer, administrate de alta sesiune. `Narativ` (YouTube) e oprit.

---

## 2. Ce NU face aceasta sesiune

- nu porneste randari (`/api/auto`, `dual_dispatch.py`, `herstory_dispatch.py`)
- nu modifica presetele din `data/variant_presets/`
- nu re-encodeaza si nu modifica fisiere video
- nu sterge nimic de pe Drive

Daca lipseste un videoclip, **nu il produce** — raporteaza ca lipseste.

---

## 3. De unde se iau videoclipurile

**Un singur folder, si el alimenteaza AMANDOUA canalele:**

```
Povestitor RO = <povestitor_drive_folder>   -> Facebook Povestitorul
                                            -> TikTok  povestitorul.ro
```

Cheia e in `data/targets.json`, sub `povestitor_drive_folder`.

Folderul romanesc **este al tau**. Pana pe 28 august pista romana era inchisa si
documentul asta spunea sa nu te atingi de el — nu mai e adevarat. La 29 august
are 87 de fisiere, din care planul retine 51 de videoclipuri (74 de fisiere);
restul sunt fisiere straine sau in alta limba, iar `build_pov_post_list.py` le
sare singur.

Folderul mai creste: pe rig randeaza acum povestitorul romanesc cu voce locala
gratuita (F5), rand cu rand din sheet. Nu astepta un stoc fix.

`povestitor_en_drive_folder` **nu mai e al tau** — pista engleza s-a inchis pe
29 august.

### CAPCANA care a produs deja o greseala

**Nu identifica un fisier dupa nume.** `262.mp4` exista in patru foldere, cu
continut complet diferit: narator, comentator, povestitor RO, povestitor EN.
Potrivirea corecta e pe **(folder parinte + nume)**. Intr-o sesiune anterioara
s-a urcat varianta povestitor peste narator si comentator pentru ca s-a cautat
global dupa nume.

**Numele fisierului = NR-ul din sheet**: `252.mp4`, sau `252_p1.mp4` /
`252_p2.mp4` cand clipul a fost taiat in parti. Partile se posteaza **consecutiv
si in ordine**, cu sufix `(1/2)`, `(2/2)` in caption — asta face scriptul singur.

---

## 4. De unde se iau descrierile

Sheet-ul romanesc — `targets.json` -> `pov_sheet_id`, tab `Sheet1`:

| coloana | continut |
|---|---|
| A | NR (numele fisierului pe Drive) |
| D | **descriere ROMANA** — captionul tau, pentru amandoua canalele |
| L | descriere engleza — pista inchisa, nu o folosi |

Coloana D e singura care conteaza acum. Documentul asta a spus, pe rand, si
"D — nu o folosi" (cat romana a fost inchisa), si "D pentru Facebook, L pentru
TikTok" (in cele patru zile cu TikTok englez). Ambele sunt depasite.

**Fara descriere nu se posteaza** — captionul ar iesi gol. Se raporteaza randul,
nu se inventeaza text. `build_pov_post_list.py` sare singur randurile fara
descriere in coloana D si le raporteaza.

---

## 5. Setup pe contul Buffer NOU

Ordinea e asta; nimic din sectiunea 7 nu functioneaza pana nu e facuta.

**1. Cheia.** Buffer, Settings, API, Personal Keys — de pe contul **NOU**.
Se scrie in `data/buffer_config.json`:

```json
{"api_key": "..."}
```

Fisierul e gitignored. **Nu tipari cheia** in raspunsuri sau loguri.

**Nu copia `buffer_config.json` de pe rigul de randare.** Acolo e cheia contului
vechi, care administreaza canalele franceze — ai ajunge sa citesti si sa scrii
exact pe canalele care nu sunt ale tale. Fisierul asta se creeaza nou.

**2. Canalele se conecteaza din interfata Buffer**, de catre om — tu nu poti.
Cere-i utilizatorului sa conecteze `Povestitorul` (Facebook) si `povestitorul.ro`
(TikTok) inainte sa continui.

**3. Vezi ce s-a conectat:**

```
server\.venv\Scripts\python.exe scripts\buffer_api.py
```

Listeaza organizatia si fiecare canal cu `id`, `service`, `type`, fus orar si
eventuale steaguri (`isDisconnected`, `isQueuePaused`).

**3bis. Conecteaza Google Drive + Sheets.** Tokenul **nu se copiaza** de pe rig
— expira cam saptamanal, fiindca aplicatia OAuth e in modul Testing. Il faci
local:

```
python scripts\conecteaza_drive.py
```

Se deschide browserul, omul aproba cu contul care detine folderele, iar tokenul
se scrie in `data/drive_oauth_token.json`. Scriptul face imediat si o citire de
proba din sheet: daca aia pica cu 403, contul aprobat n-are acces; 503 e Google,
se reincearca. Singurul fisier care trebuie copiat de pe rig e
`data/drive_oauth_client.json` (clientul OAuth de tip Desktop).

Cand mai tarziu apar 401/403 pe Drive sau Sheets, se ruleaza din nou acelasi
script. Nu e nevoie de backend si nici de interfata ClipForge.

**4. Pune numele reale in `data/targets.json`.** Scripturile cauta canalele
**dupa nume, exact**. Cheile care te privesc:

| cheie | ce e acum |
|---|---|
| `tiktok_channel_ro` | `povestitorul.ro` |
| `facebook_channel` | `Povestitorul` |

Daca pe contul nou au alte nume, **actualizeaza aici**, altfel
`post_povestitor.py` se opreste cu "canalul X nu e conectat in Buffer" si iti
listeaza ce a gasit. Nu atinge cheile `*_fr` — sunt ale celuilalt cont.

---

## 6. ATENTIE: evidenta postarilor NU se muta odata cu contul

Scriptul stie ce a postat deja din doua surse diferite, si **doar una
supravietuieste schimbarii de cont**:

| canal | evidenta | supravietuieste? |
|---|---|---|
| `povestitorul.ro` (TikTok) | folderul `posted/` de pe Drive | **DA** — Drive nu depinde de Buffer |
| `Povestitorul` (Facebook) | istoricul de postari din Buffer | **NU** — contul nou porneste gol |

Ce inseamna concret: pe contul nou, Facebook **nu are cum sa stie** ce a fost
publicat de pe contul vechi si ar reprograma acele clipuri. La 25 august
expunerea e mica — o singura postare engleza a apucat sa iasa pe Facebook — dar
**intreaba utilizatorul ce a fost deja publicat inainte de prima rulare** si sari
peste acele NR-uri. Nu presupune ca un istoric gol inseamna ca nu s-a postat nimic.

Pentru TikTok nu ai grija asta: fisierele deja predate sunt in `posted/`, iar
Drive pastreaza ID-ul la mutare, deci URL-ul pe care Buffer il descarca la
publicare ramane valid.

**Dar Buffer iti da singur raspunsul.** La conectarea unui canal, **importa
istoricul de postari al contului** de pe platforma. Postarile au campul `via`:
`network` = publicata nativ, inainte de Buffer (sau de pe contul vechi),
`buffer` = publicata de unealta. Deci ce a iesit deja se citeste asa:

```
posts(input: {organizationId: …, filter: {channelIds: [<canal>], status: ["sent"]}})
  { edges { node { id dueAt text via assets { ... on VideoAsset { source } } } } }
```

Scoate id-ul de Drive din `assets[].source` si potriveste-l cu `nr` din
`data/pov_en_post_list.json`. Alea sunt NR-urile de sarit. Importul poate sa nu
acopere chiar tot, deci confirma cu utilizatorul inainte de prima rulare reala.

---

## 7. Cum se posteaza

Intai se construieste planul, apoi se posteaza din el:

```
server\.venv\Scripts\python.exe scripts\build_pov_post_list.py
server\.venv\Scripts\python.exe scripts\post_povestitor.py --channel facebook --dry
server\.venv\Scripts\python.exe scripts\post_povestitor.py --channel tiktok --dry
```

Profilurile tale sunt **`facebook`** si **`tiktok`**, amandoua romanesti,
amandoua din acelasi plan si acelasi folder. Difera doar evidenta a ce s-a
postat: Facebook se uita in istoricul Buffer, TikTok in folderul `posted/` de pe
Drive.

| profil | plan | folder | descriere |
|---|---|---|---|
| `facebook` | `data/pov_post_list.json` | RO | coloana D |
| `tiktok` | `data/pov_post_list.json` | RO | coloana D |

`tiktok_en` si `facebook_en` refuza sa porneasca — pista engleza e inchisa din
29 august. Nu le fortezi cu `--si-inchise`.

Ruleaza **intai cu `--dry`**, mereu. Fara `--dry` trimite pe bune.
`--limit N` opreste dupa N postari, `--first 252,253` pune anumite NR-uri in fata.

**Sloturile** sunt patru pe zi: **08:00, 13:00, 18:30, 20:30** (`SLOTS_LOCAL` in
`post_povestitor.py`). Alese din date reale — mediana vizualizarilor pe ultimele
45 de zile arata 20:00 cea mai buna ora si 13:00 cea mai slaba, deci al patrulea
slot e seara, nu la pranz.

Scriptul impune singur doua reguli: nu refoloseste un slot deja ocupat, si nu
imparte un videoclip cu parti intre doua rulari (partea 2 fara partea 1 ar strica
ordinea).

---

## 8. Capcanele Buffer — citeste-le inainte sa scrii cod

**URL-ul de Drive.** Forma obligatorie:

```
https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=t
```

Pe `drive.google.com/uc?export=download&id=...` Google raspunde cu pagina de
avertisment antivirus la fisierele mari, iar Buffer o citeste ca
"Video could not be read from its URL". 28 din 40 de postari au picat asa.

**Metadata pe platforma.** Fara ea, postarea e respinsa la creare:

- Facebook: `{"facebook": {"type": "reel"}}` — iar daca raspunde "aspect ratio is
  too narrow" sau "no longer than", se reia cu `{"type": "post"}` (scriptul face
  deja asta singur)
- TikTok: fara metadata

**`editPost` NU face actualizare partiala.** Daca trimiti doar `dueAt`, raspunde
"Post must have either text or media". Trimite si textul, si asset-ul.

**`editPost` ignora `dueAt` daca nu trimiti si `mode` + `schedulingType`** — si
NU da eroare: raspunde `PostActionSuccess`, cu postarea neatinsa. Schema le
marcheaza optionale (`ShareMode`, `SchedulingType`), in practica nu sunt. Pe
Contouse au iesit asa 76 de „mutari" raportate ca reusite, cu coada nemiscata.
Trimite mereu `"schedulingType": "automatic", "mode": "customScheduled"`, si
**verifica `dueAt`-ul intors de API** in loc sa te iei dupa lipsa erorii — un
script care verifica doar `message` va minti.

**`thumbnailOffset` sta pe ASSET, nu pe metadata postarii:**
`assets: [{video: {url, metadata: {thumbnailOffset: 450}}}]`. Functioneaza doar
pe TikTok, Instagram si Pinterest. `thumbnailUrl` e respins de API.

**`deletePost` foloseste alta uniune** decat celelalte mutatii:
`DeletePostSuccess` si `VoidMutationError`, nu `PostActionSuccess`/`MutationError`.

**Orice re-creare de postari trebuie sa sara peste ce e deja programat.** S-au
creat duplicate de doua ori pentru ca scriptul recitea lista de erori si
reprograma continut care era deja in coada. Verifica pe ID-ul fisierului de Drive.

**Limitele: 100 de cereri / 15 minute si 250 / 24 de ore.** A doua e cea care te
opreste. O analiza care citeste tot istoricul consuma sute de cereri. Cand
lovesti plafonul de 24h, **nici citirile nu mai merg** — nu are rost sa reincerci
cu backoff, trebuie asteptat. Sondeaza rar (30 min): o cerere respinsa tot
consuma din fereastra.

---

## 9. Starea la 25 august 2026

**Cat incape in coada.** Plafonul de 10 postari/canal era al planului Free. Pe
25 august `Povestitorul` avea **38 de postari programate**, deci pe planul platit
limita nu mai e in vigoare. Posterul are inca 10 ca implicit prudent — ridica-l
cu variabila de mediu `CLIPFORGE_QUEUE_MAX=40`, ca sa umpli pe doua saptamani in
loc de doua zile. Daca totusi exista o limita, posterul **se opreste singur la
prima eroare**, cu ce a apucat sa programeze deja pus — nu strica sa incerci.

In folderul EN: 42 de fisiere, din care **24 de videoclipuri (40 de fisiere) au
descriere engleza** si intra in plan. NR 191 nu are descriere in coloana L.

16 dintre ele sunt taiate in parti: 193, 200, 201, 203, 206, 252, 253, 254, 256,
257, 258, 259, 260, 271, 272, 273. Fiecare ocupa doua sloturi consecutive.

---

## 10. Verificari utile

Starea generala a rigului:

```
server\.venv\Scripts\python.exe scripts\stare.py
```

Erorile de pe canale: postarile in stare `error` al caror fisier **nu e deja
reprogramat** sunt singurele care cer actiune. Restul sunt istoric.

---

## 11. Cand ceva pare ca a esuat

**Timeout-ul de retea Google raporteaza fals esec.** De patru ori intr-o zi,
`upload failed` a insemnat de fapt "urcat, dar confirmarea a expirat".
**Verifica intai Drive-ul.** La fel, `503 Service unavailable` de la Sheets sau
Drive e problema la Google, nu autentificare expirata — aceea da `401`/`403`.

Tokenul Google expira cam saptamanal (aplicatia OAuth e in mod Testing). Se
reconecteaza din Settings in interfata ClipForge.
