# Gate vizual uman — 31 august 2026

Verdictele date de Vladica pe `verificare-porti.html`, 20 de clipuri alese pe dovadă din cele 58 de
exporturi ale piloturilor. **Verbatim, netraduse și neinterpretate** — notele lui sunt datele, iar
citirea mea a lor e mai jos, marcată ca atare.

Corpus: exporturile re-randate după reparația de caption din 31 august (`render_v3_letterbox` +
`subtitles` după `overlay`).

## Verdicte

```
moist|pilot2c8a/003a5c53c51d = ok
moist|pilot2c8a/e2576b0a0e3b = ok
moist|pilot2c8a/2a59b1e41880 = bad  // personajul din browser e pus bine cand vorbeste, doar ca e taiat pe jumatate nu e focusat pe el
moist|pilot2c8a/38aa005c7c1f = bad  // subtitrarea ar merge jos si iar e stricata imaginea din browser
jensen|pilot6b38/46921099031e = bad  // stricat: secunda 6-9 secunda 23-25 secunda 35-39 secunda 53 si 56
jensen|pilot6b38/ad094a7ba1bb = bad  // are momente cand e putin stricat la fel ca primul
jensen|pilot6b38/df54e4eeb408 = bad  // e bun mare parte doar ca in unele momente se taie persoana pe jumate
jensen|pilot6b38/5bd49b8abd8e = bad  // detectia pe fete e buna, doar in momentul in care intra 16:9 cam 2 secunde la fiecare videoclip nu e tranzitia buna
vlog|pilotee0e/a9f653576eb7 = bad
vlog|pilotee0e/10b22f789e7b = bad
vlog|pilotee0e/2b875022bb35 = bad
vlog|pilotee0e/94843d95608c = bad
ghost|pilotf81b/b23c14c41495 = ok  // se mai vede subtitrarea
ghost|pilotf81b/7fe211b7e3bf = ok  // se mai vede subtitrarea
ghost|pilotf81b/6053a598cf06 = bad  // se mai vede subtitrarea sila sec 45 sare fata
ghost|pilotf81b/f2c0424c76bc = ok  // se mai vede subtitrarea
caption|pilot2c8a/003a5c53c51d = ok
caption|pilotee0e/10b22f789e7b = ok
caption|pilot6b38/df54e4eeb408 = ok
caption|pilotee0e/2b875022bb35 = ok
```

**11 bad, 9 ok din 20.**

## Ce închide, și în ce direcție

### R3a — PICAT

Gate-ul cerea „Moist: creatorul, nu fața din browser". Verdictul lui e că **încadrarea urmărește
personajul din browser** (`2a59b1e41880`: „personajul din browser e pus bine cand vorbeste") și că
`38aa005c7c1f` e „iar stricată imaginea din browser". 2 ok / 2 bad.

Asta confirmă direct ce spuneau R3a și R3b în cod și ce nimeni nu demonstrase: **`stable_track`
găsește un cluster geometric stabil, nu o persoană.** Cifra de 464 din 1.285 de eșantioane
„compatibile cu ancora" nu însemna niciodată „464 sunt creatorul", iar acum există dovada umană că
uneori nu e.

### R3b — PICAT, 8 din 8

Jensen 4/4 bad, vlog 4/4 bad. Două mecanisme numite, și amândouă sunt despre GEOMETRIE, nu despre
alegerea subiectului:

- **„se taie persoana pe jumate"** — apare la `jensen/df54e4eeb408` și la `moist/2a59b1e41880`, deci
  peste două surse diferite. Fereastra de crop taie fața, nu o ratează.
- **„detectia pe fete e buna, doar in momentul in care intra 16:9 cam 2 secunde la fiecare videoclip
  nu e tranzitia buna"** (`jensen/5bd49b8abd8e`). **Cea mai utilă propoziție din tot setul:**
  separă explicit detecția de tranziție și pune defectul pe intrarea în `fit`, cu o durată — ~2s,
  la fiecare clip.

`jensen/46921099031e` dă timpi exacți: **6–9s, 23–25s, 35–39s, 53s, 56s.**

### R6, caption peste față — TRECUT, 4 din 4

Toate patru `ok`. Semnalul `over_face`, care era mort până azi și acum marchează 38 de clipuri, e
**adevărat geometric și fără consecință vizuală pe eșantionul ăsta**. Nu îl șterge — dar ca
`REVISABLE` în preflight e prea strict, și e primul lucru pe care o măsurătoare umană l-a contrazis.

### R6, captions duble — NECONCLUDENT, și trebuie reîntrebat

Toate patru notele spun **„se mai vede subtitrarea"**, dar trei clipuri sunt marcate `ok` și unul
`bad`. Verdictul și nota trag în direcții opuse: dacă „se mai vede" înseamnă că subtitrarea SURSEI e
încă vizibilă, atunci defectul cu care s-a deschis planul e prezent pe 4 din 4 și gate-ul a picat;
dacă înseamnă că a NOASTRĂ se vede bine, gate-ul a trecut.

**Nu se poate deduce din date.** Rămâne deschis până când omul spune care dintre cele două.

## Diagnosticul, derivat din timpii pe care i-a dat

`jensen/46921099031e` a venit cu timpi exacți — **6–9s, 23–25s, 35–39s, 53s, 56s** — și clipul are
exact **patru schimbări de compoziție**. Toate patru cad în ferestrele raportate:

| tranziție | fel | raportată |
|---|---|---|
| 8,82s | `crop → fit` | da (6–9) |
| 22,65s | `fit → crop` | da (23–25) |
| 38,40s | `crop → fit` | da (35–39) |
| 52,97s | `fit → crop` | da (53) |

**Patru din patru, zero ratate.** Al cincilea moment raportat, 56s, e singurul care nu e o schimbare
de compoziție: e un salt de ancoră de 998px într-un `crop → crop`.

**Saltul de ancoră NU explică restul, și asta contează.** Clipul are salturi de 960px la 13,50s,
844px la 48,66s și 50,23s, și **1138px la 57,73s — cel mai mare din clip** — și niciunul nu e
raportat. Deci ipoteza „se strică unde sare încadrarea" e falsificată de propriile date; ce rămâne e
**tranziția între compoziții**, exact ce a spus el în cuvinte pe alt clip: „în momentul în care intră
16:9 cam 2 secunde la fiecare videoclip nu e tranziția bună".

### Cât de răspândit e, pe cele 58 de exporturi

```
clipuri cu compoziție cunoscută            58 din 58
clipuri cu cel puțin o schimbare crop<->fit 27  (47%)
total schimbări                             84   media 1,4 pe clip
durata totală                             2743s  =>  o schimbare la 32,7s
la ~2s de tranziție proastă fiecare          6% din durata livrată
```

### De ce vlog a ieșit 4 din 4 fără nicio notă

Fiindcă acolo defectul e continuu. **Primele trei clipuri din tot corpusul după numărul de schimbări
sunt exact trei dintre cele patru pe care le-a marcat el:**

| clip | durată | schimbări | una la |
|---|---:|---:|---:|
| `pilotee0e/10b22f789e7b` | 88,5s | **10** | 9s |
| `pilotee0e/94843d95608c` | 76,7s | **8** | 10s |
| `pilotee0e/2b875022bb35` | 81,1s | **6** | 14s |
| `pilotee0e/a9f653576eb7` | 39,0s | 1 | 39s |

O tranziție proastă la fiecare 9 secunde nu se notează pe momente, se notează pe clip. Al patrulea
are o singură schimbare și e tot `bad` — deci pentru el mai există și altceva, **nemăsurat**.

### Ce se vede în cadre, și cifra care îl explică

Cadre extrase din `46921099031e` în jurul joncțiunii de la 8,82s:

- **8,70s (`crop`)** — diagrama de proteină e tăiată într-o **fâșie verticală**: se vede o coloană
  din mijlocul unui grafic lat, mărită. Ca imagine e nelizibilă ca întreg.
- **9,00s (`fit`)** — aceeași diagramă apare deodată **mică, în mijloc**, ocupând vreo treime din
  înălțime, cu două benzi blurate mari sus și jos.

Deci același obiect își schimbă instantaneu mărimea aparentă. Măsurat pe toate joncțiunile din cele
58 de exporturi, comparând zoom-ul efectiv al fiecărei părți (`1080/lățimea ferestrei` pentru `crop`,
`1080/lățimea sursei` pentru `fit`):

```
joncțiuni crop<->fit         84
salt de scară median       3,58x
minim / maxim        3,16x / 13,52x
sub 1,5x (blânde)             0
peste 3x                     84
```

**Toate cele 84. Niciuna sub 3x.**

Și minimul nu e o coincidență, e o identitate: pentru o sursă 16:9, `crop` ia o fereastră de
`1080·(1080/1920) = 607,5`px și o mărește la 1080 (zoom 1,78x), iar `fit` ia toți cei 1920 și îi
strânge la 1080 (zoom 0,5625x). Raportul e `1,78 / 0,5625 = 3,16` **prin construcție**. Nu există
reglaj care să-l coboare fără a schimba ce înseamnă `fit`.

**Asta mută defectul din categoria „tranziție de înmuiat" în „tăietură pe care regula n-ar trebui să
o facă".** Un cut între două compoziții e un salt de zoom de cel puțin 3,16x pe același subiect, iar
R4 tratează schimbarea de tratament drept motiv OBLIGATORIU de tăietură — taie chiar și fără graniță
de cuvânt și chiar peste `min_shot_s`. Nimic din lanț nu întreabă cât de mare e saltul pe care îl
produce.

## Ce urmează din asta

Defectul e în **randare, la joncțiunea dintre compoziții** — nu în detecția de fețe, pe care el o
declară explicit bună, și nu în alegerea momentului. Asta îl scoate din R3a/R3b (care aleg subiectul
și regimul) și îl pune lângă R1: e o proprietate a felului în care `dynamic_render` exprimă o
schimbare de compoziție, unde `crop` se reconfigurează și `scale` îl urmează în același `sendcmd`.

S-a măsurat: e un **salt de scală**, median 3,58x, minim 3,16x, pe toate cele 84 de joncțiuni.
Vezi secțiunea de mai sus. Ce NU s-a măsurat e dacă o tranziție animată l-ar face acceptabil sau
dacă tăietura însăși trebuie evitată — prima e muncă de randare, a doua e o regulă în R4, și alegerea
dintre ele nu se poate face din datele astea.

## Ce NU demonstrează

- Nimic despre selecție. Astea sunt verdicte de RANDARE, pe clipuri alese pentru că poartă un
  defect anume — nu un eșantion din care se pot calcula rate.
- Nimic despre celelalte 38 de exporturi ale piloturilor.
- `vlog` are 4 bad fără nicio notă, deci **nu se știe ce e stricat acolo.** E de reîntrebat.
