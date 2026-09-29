# Clipper — test în cloud pe un VOD Twitch (29 septembrie 2026)

Un test al Clipper-ului în afara PC-ului: un VOD Twitch de 64 de minute a trecut prin tot pipeline-ul
într-un container cloud (fără GPU, fără cheie LLM), iar board-ul rezultat a fost comparat cu reacțiile din chat.
Continuă [`clipping-apps-survey-2026-09-29.md`](clipping-apps-survey-2026-09-29.md) și
[`clipper-scoring-selection-2026-09-29.md`](clipper-scoring-selection-2026-09-29.md); planurile CA și SL sunt de acolo.

| etichetă | înseamnă |
|---|---|
| **[MĂS]** | măsurat în acest test, pe această sursă; o singură rulare, un singur stream |
| **[CF]** | starea ClipForge, verificată în cod pe 29.09, cu fișierul |
| **[ASM]** | deducția noastră; se verifică înainte de a construi pe ea |

Scripturile folosite sunt în [`clipper-cloud-test-2026-09-29/`](clipper-cloud-test-2026-09-29/) (§9). Datele brute
(VOD-ul, transcriptul, chatul) nu sunt în repo: sunt mari și conțin mesajele altor oameni. Se regenerează cu §9.

---

## 1. Pe scurt

1. **Pipeline-ul merge cap-coadă pe o mașină modestă** [MĂS]: 4 vCPU, fără GPU. 64 de minute de VOD dau un board
   în ~50–55 de minute de calcul, iar un export de 24–59 s durează 1,5–3 minute.
2. **Board-ul ignoră ultimele 20 de minute** [MĂS]: niciun clip după 44:20, deși acolo sunt 5 din cele mai puternice
   12 momente din chat, inclusiv primele trei. Candidații există: 60 după minutul 44. Cel mai bun are însă 60,1,
   cu 1,8 puncte sub pragul board-ului (61,9). Problema e la scorare, nu la generarea candidaților (§7).
3. **Board-ul nu prinde momentele la care reacționează chatul** [MĂS]. Decalajul acestui stream, măsurat din sunet,
   e ~10 s. La acest decalaj, 0 din 12 momente din chat cad într-un clip, iar un board aleator ar prinde 12%. Cifra de
   3 din 12 apare doar la un decalaj de 35 s, ales după rezultat. E un singur stream și un proxy necalibrat, deci un
   indiciu, nu un verdict (§5).
4. **Ce a ales board-ul** [MĂS]: clipul #1 e vocea naratorului din trailer, nu streamerul, iar 3 din 10 clipuri sunt
   logistică (reclame, green screen). Fără LLM, titlurile sunt primele cuvinte din transcript („Huh"); §4.
5. **Exportul se blochează pe ffmpeg < 8** [MĂS]. Schimbarea mărimii `crop` prin `sendcmd` oprește ffmpeg 6.1 și 7.0
   fără niciun mesaj; pe ffmpeg 8.x merge. PC-ul are 8.1, deci nu e afectat, dar nimic nu verifică versiunea (§6).
6. **Retry pe o sursă URL descarcă din nou VOD-ul** [MĂS] [CF]: încă 3,67 GB, pentru că sursa e considerată locală
   doar la upload (§8).

## 2. Ce s-a rulat

| | |
|---|---|
| sursa | VOD Twitch [`2858049515`](https://www.twitch.tv/videos/2858049515), „GTA 6 LIVE REACTION *First Look*" (KaiCenat, 27.08.2026): 63:48, 1920×1080 la 60 fps, 3,67 GB |
| mașina | container cloud: 4 vCPU, 15 GB RAM, fără GPU |
| ffmpeg | 6.1.1 (Ubuntu) pentru ingest și analiză; pentru export, un build master 8.x (§6) |
| transcriere | faster-whisper `medium` pe CPU |
| mod | euristic: mediul cloud n-are cheie LLM, deci judge-ul LLM și titlurile LLM **n-au rulat** |
| setări | cele implicite; proiectul a fost creat doar cu `source_url` |
| chat | reluarea de chat Twitch: 37.595 de mesaje (~35.300 pe oră) |

## 3. Timpi [MĂS]

| etapă | durată | notă |
|---|---|---|
| descărcare | ~3–7 min | 3,67 GB |
| proxy | ~11 min | |
| transcriere | 27 min | 0,42 din durata VOD-ului, pe 4 CPU |
| analiză | ~8 min | |
| scorare și selecție | < 1 min | |
| export | 1,5–3 min pe clip | clipuri de 24–59 s, `preset slow`, 1080×1920 la 30 fps, ffmpeg 8.x |

Containerul a fost repornit în timpul proxy-ului (encode-ul a ieșit cu rc=255), iar proiectul a fost reluat cu Retry.

## 4. Board-ul [MĂS]

| # | interval | durată | scor | ce e |
|---|---|---|---|---|
| 1 | 13:13–13:40 | 27 s | 71,0 | naratorul trailerului („The sprawling satirical reimagining…"), apoi streamerul reglează volumul („too loud… good catch") |
| 2 | 24:57–25:21 | 24 s | 68,5 | cum se opresc reclamele — logistică |
| 3 | 35:01–36:00 | 59 s | 68,5 | discuție despre ce să poarte („Sequins?") |
| 4 | 16:33–17:09 | 37 s | 65,7 | „de cât timp așteptăm": anticipare, vorbește cu chatul |
| 5 | 09:28–10:31 | 63 s | 64,4 | răspunde celor care spun că a jucat deja jocul („leaked") |
| 6 | 43:27–44:20 | 54 s | 63,7 | aproape fără vorbire (23 de cuvinte în 54 s); titlul e „Huh" |
| 7 | 23:15–24:32 | 78 s | 63,7 | green screen și full screen pentru trailer — logistică; scorerul notează că e peste banda de durată |
| 8 | 33:34–34:08 | 34 s | 63,7 | dialog din trailer sau din joc („they just killing him") |
| 9 | 07:23–08:53 | 90 s | 62,2 | reclame — logistică; peste banda de durată |
| 10 | 10:22–11:01 | 38 s | 61,9 | apare categoria GTA 6 pe Twitch („chat, this is crazy") |

Pe axa timpului: o coloană = un minut, cifrele marchează zecile de minute. █ = clip din board,
▲ = moment din chat (fereastra de vârf minus 25 s, §5).

```
min    0         1         2         3         4         5         6
board  ·······█████·█··██·····███·······████······██···················
chat        ▲     ▲ ▲ ▲          ▲ ▲     ▲           ▲   ▲   ▲  ▲   ▲
```

Exporturile #1, #2, #3 și #10 au fost randate cu ffmpeg 8.x și verificate vizual pe câte un cadru. Încadrarea dinamică
(crop pe față, apoi fit cu fundal blurat) și subtitrarea arsă apar corect.

## 5. Board-ul față de chat [MĂS]

**Metoda** ([`analyze_chat.py`](clipper-cloud-test-2026-09-29/analyze_chat.py)) e un proxy după Lightor, cu
ponderi egale, deci necalibrat:

- ferestre de 25 s, cu pas de 5 s;
- trăsături: lungimea medie a mesajelor (mai scurt = mai reactiv) și asemănarea mesajelor cu centroidul ferestrei;
- volumul, doar dacă variază. Aici reluarea chatului e plafonată (505–640 de mesaje pe minut, coeficient de variație
  sub 0,10), așa că în locul volumului intră ponderea emote-urilor de hype și de râs;
- primele și ultimele 120 s sunt excluse, iar vârfurile sunt la cel puțin 120 s unul de altul.

Momentul la care reacționează chatul = centrul ferestrei minus decalaj. Baza de comparație e un board aleator, cu
aceleași lungimi de clip (2.000 de trageri).

| vârfuri | decalaj | toleranță | în board | board aleator | P(≥ la fel de multe, la întâmplare) |
|---|---|---|---|---|---|
| 12 | 25 s | strict | 0 / 12 | 12,2% | — |
| 12 | 25 s | +15 s după final | 3 / 12 (#1, #8, #10) | 15,7% | 0,29 |
| 12 | 35 s | strict | 3 / 12 (#1, #8, #10) | 12,2% | 0,17 |
| 15 | 25 s | strict | 1 / 15 (#9) | 12,5% | 0,86 |
| 15 | 35 s | strict | 3 / 15 | 12,4% | 0,28 |

Cele 12 momente (decalaj 25 s), în ordinea tăriei:

| moment | emote dominant | în board? |
|---|---|---|
| 50:02 | hype | nu |
| 61:02 | hype | nu |
| 46:28 | râs | nu |
| 05:32 | hype | nu |
| 11:02 | hype | la 1 s după finalul #10 |
| 34:12 | hype | la 4 s după finalul #8 |
| 13:42 | hype | la 2 s după finalul #1 |
| 15:52 | hype | nu (#4 începe la 16:33) |
| 28:32 | hype | nu (singurul clip făcut de un spectator, §8, începe la 29:20) |
| 54:22 | hype | nu |
| 26:08 | râs | nu |
| 57:32 | hype | nu |

**Corecție, după măsurarea decalajului** ([`clipper-reaction-signals-2026-09-29.md`](clipper-reaction-signals-2026-09-29.md)
§3.1): pe acest stream, emote-urile de râs urmăresc sunetul cu 8–10 s întârziere. 25 și 35 s au fost presupuse, nu
măsurate. La 10 s și la 15 s, **0 din 12** momente cad în board. Rândurile cu 3/12 de mai sus arată doar că un
decalaj ales după rezultat poate face board-ul să pară aliniat.

Cele mai puternice trei momente (50:02, 61:02, 46:28) sunt toate în afara board-ului, la orice decalaj.

## 6. Exportul blocat: ffmpeg < 8 [MĂS]

**Simptomul:** exportul #10 a rămas la 20%, fără mesaj, iar ffmpeg n-a scris niciun cadru. Jobul s-ar fi terminat
abia la `RENDER_TIMEOUT` (3600 s, `server/services/clipper/dynamic_render.py`) [CF]. Mesajul ar fi fost
`dynamic clip render failed (rc=255): `, cu motivul gol, pentru că ffmpeg rulează cu `-loglevel error`.

**Bisecția** a folosit aceeași comandă, cu un encoder fără întârziere (`-preset ultrafast -tune zerolatency`) și
ieșire `-f null`, pe ffmpeg 7.0.2:

| variantă | cadre ieșite | memorie |
|---|---|---|
| graful original | 60, apoi nimic | 3 GB în ~18 s, în creștere |
| fără `subtitles`, `eq`, `fps` sau alpha (câte una) | 60–62 | la fel |
| doar ramura `[a]` (fără `split` și `overlay`) | 62 | 0,2 GB |
| **fără `sendcmd`** | **541 în 25 s** | 0,3 GB |
| graful original pe ffmpeg master 8.x | **737 în 40 s** | 0,3 GB |

Cadrul 60 cade la 1,995 s, adică la prima schimbare de mărime a crop-ului (584×1040 → 572×1018). După ea, ramura
din față nu mai scoate cadre. Ramura fundalului, prin `split`, continuă să tragă cadre, așa că memoria crește fără
limită: pe 7.0, comanda completă a ajuns la 8 GB în mai puțin de un minut. Pe 6.1, bucla principală se învârte în
gol la 2,4 GB. Același export, pe ffmpeg 8.x, s-a terminat în 1,5 minute.

`docs/dynamic-edit-recipe.md` §9 spune deja că `crop` își schimbă `w/h` la rulare „în ffmpeg 8.1". PC-ul are 8.1.x,
deci nu e afectat. Riscul e pe orice altă mașină (ffmpeg din apt, un build Windows mai vechi, cloud), unde fiecare
export cu mai multe cadraje se blochează o oră. În sesiunea asta a fost propus un task separat: aflarea versiunii
minime și un refuz rapid, cu mesaj, sub ea.

## 7. De ce lipsește finalul VOD-ului [MĂS]

- 169 de candidați (10 în board + 159 alternative). Scorul median e 53,3, iar intervalul intercuartil 47,5–57,5.
- Pragul board-ului e 61,9. Imediat sub el, în 5 puncte, stau 33 de candidați.
- 60 de candidați încep după minutul 44; cel mai bun are 60,1.

Generatorul a găsit momentele târzii, dar scorul euristic nu le separă de restul. Pe o bandă atât de îngustă,
diferențe de 1–2 puncte decid ce intră [ASM]. Un semnal de la public — chatul (CA7), clipurile spectatorilor (CA4) —
ar lucra tocmai aici, ca departajare în banda comprimată. Testul acesta e un prototip de SL5 (evaluare offline pe
etichete de la public) pe un singur stream.

## 8. Alte observații [MĂS]

- **CA1** („clip that" în transcript): 0 apariții în 506 segmente (5.491 de cuvinte). În chat au fost însă ~24 de
  mesaje „clip it". Pe acest stream CA1 n-ar fi ajutat; CA7 ar fi ajutat.
- **CA4** (clipurile spectatorilor): s-a găsit un singur clip din acest VOD. E „w chat": 30 s de la 29:20, 4.018
  vizualizări, în afara board-ului. Fără credențiale de aplicație Twitch se vede doar prima pagină de rezultate
  (paginile următoare au fost refuzate), deci CA4 are nevoie de Helix cu credențiale.
- **Transcript rar:** ~86 de cuvinte pe minut. Pe un stream de reacție, mult timp e trailer sau muzică, iar scorerul
  bazat pe text are puțin de lucrat.
- **Retry re-descarcă sursa:** `server/workers/clipper_pipeline.py` consideră sursa locală doar pentru `upload`
  [CF], așa că un Retry pe o sursă URL ia de la capăt cei 3,67 GB. A fost propus un task separat.
- **Titlurile în modul euristic** sunt primele cuvinte ale transcriptului („Huh", „I have minimum ads what I do I
  do how"). Nu se pot posta fără LLM.

## 9. Cum se reia pe PC

Scripturile folosesc doar biblioteca standard Python. Rulează-le dintr-un folder de lucru din afara repo-ului, ca
`board.json`, `chat.json` și `chat_result.json` să nu ajungă în Git.

1. Adu branch-ul `claude/fireclaw-clipping-apps-fnmlou` (sau `main`, după ce PR #32 e unit) și pornește backend-ul
   ca de obicei, pe 8420.
2. În AI Stream Clipper, creează un proiect nou din `https://www.twitch.tv/videos/2858049515`. Folosește setările
   obișnuite ale PC-ului, **cu judge-ul LLM pornit**: aceasta e partea pe care cloud-ul n-a putut-o testa. Apoi
   pornește Analyze.
3. Când proiectul e gata, rulează (cu `<repo>` = folderul ClipForge):

   ```
   <repo>\server\.venv\Scripts\python.exe <repo>\docs\research\clipper-cloud-test-2026-09-29\extract_results.py <project_id> <repo>\data\db\clipforge.db
   <repo>\server\.venv\Scripts\python.exe <repo>\docs\research\clipper-cloud-test-2026-09-29\fetch_chat.py 2858049515 3828 chat.json
   <repo>\server\.venv\Scripts\python.exe <repo>\docs\research\clipper-cloud-test-2026-09-29\chat_lag.py <repo>\data\clipper\<project_id>\audio\speech.wav chat.json
   <repo>\server\.venv\Scripts\python.exe <repo>\docs\research\clipper-cloud-test-2026-09-29\analyze_chat.py chat.json board.json --lag <decalajul măsurat>
   ```

   - `fetch_chat.py` face ~680 de cereri și ia câteva minute. `gql.twitch.tv` e endpoint-ul intern al site-ului
     Twitch, nu un API documentat, așa că poate să nu mai răspundă la fel.
   - `chat_lag.py` măsoară decalajul din sunet. Iese cu 2 când corelația nu trece pragul obținut la întâmplare, iar
     atunci nu există un decalaj de folosit.
   - `analyze_chat.py` iese cu 2 când nu are ce compara (chat gol sau 0 momente).
4. Compară patru liste: board-ul de pe PC (cu LLM), board-ul din cloud (§4), momentele din chat (§5) și clipurile
   OpusClip pe același VOD. Pentru fiecare clip OpusClip trebuie intervalul din VOD (sau prima frază) și scorul.
   Scorurile OpusClip sunt comprimate: 82% din 7.101 clipuri au ≥ 80, după cercetarea SL. Contează ordinea, nu
   valoarea.

## 10. Ce n-a testat

- judge-ul LLM și titlurile LLM (fără cheie în cloud); asta e prima întrebare pentru rularea de pe PC;
- căile GPU (transcriere, detecție);
- mai mult de un stream; toate cifrele de mai sus sunt de pe o singură sursă și o singură rulare;
- comparația cu OpusClip: rezultatele lui sunt la utilizator și se adaugă aici când sunt comparate.
