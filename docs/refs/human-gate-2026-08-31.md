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

### R6, captions duble — PICAT, 4 din 4, confirmat de om

Toate patru notele spun **„se mai vede subtitrarea"**, dar trei clipuri erau marcate `ok` și unul
`bad` — verdictul și nota trăgeau în direcții opuse, și diferența decidea poarta. Întrebat direct,
omul a răspuns: **e subtitrarea SURSEI.** Sub stratul nostru se mai vede cel ars în videoclipul
original.

Deci **defectul cu care s-a deschis tot planul e încă prezent, pe 4 din 4 clipuri privite**, pe
corpusul RE-RANDAT de pe 31 august. Poarta R6 („zero captions duble pe cele 15 go ghost") a picat.

Ce validează asta, și e prima confirmare umană pe care o primește vreunul dintre instrumentele
astea:

- **`source_captions` avea dreptate.** Verdictul `present` pe `pilotf81b` nu era un fals pozitiv al
  unui detector cu `calibrated: false` — omul vede exact ce a raportat el.
- **Cele 37 de respingeri ale preflight-ului sunt confirmate.** Erau singura cifră din R7 despre care
  Codex spunea că rămâne „suspiciune de duplicare, nu duplicare demonstrată". Pe eșantionul ăsta e
  demonstrată.
- **Cerința celor două straturi era corectă.** `own_layer` — adăugat fiindcă un verdict despre
  proxy-ul proiectului nu e o propoziție despre un export — dă exact răspunsul care s-a adeverit:
  sursa are text ars ȘI exportul poartă `.ass`-ul lui.

Ce NU e închis: nimeni nu a stins încă al doilea strat. Detectorul e `calibrated: false` pe patru
surse, iar handover-ul cere explicit să nu se materializeze dezactivarea captions-urilor cât timp e
așa. Confirmarea asta e un argument pentru calibrare, nu un substitut pentru ea.

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

### Al patrulea vlog, cel neexplicat: 17 tăieturi invizibile

`pilotee0e/a9f653576eb7` are **o singură** schimbare de compoziție, deci diagnosticul de mai sus nu
îl acoperea. Rulat prin funcția livrată a lui R1 (`dynamic_geometry.merge_equivalent_shots`):
**20 de shot-uri devin 3.** 39 de secunde tăiate în 20 de bucăți, dintre care **17 tăieturi nu
schimbă absolut nimic** — de la 4,64s încolo sunt 18 shot-uri `fit` consecutive, iar un `fit` livrează
cadrul întreg indiferent de ancoră.

Pe tot corpusul: **exact 116 tăieturi invizibile, pe 23 din 58 de clipuri** — cifra de bază a lui R0,
neschimbată. R1 a reparat plannerul pe 29 august și **reparația n-a ajuns niciodată într-un export**,
fiindcă re-randarea din 31 august a rejucat planurile stocate în loc să re-planifice.

Suprapunerea cu verdictele umane nu e întâmplătoare: din cele 11 clipuri marcate `bad`, **cel puțin
șase poartă tăieturi invizibile** — `a9f653576eb7` 17, `46921099031e` 10, `ad094a7ba1bb` 6,
`10b22f789e7b` 6, `2b875022bb35` 6, `38aa005c7c1f` 5. Deci prin verdictele lui trec DOUĂ defecte
independente, iar al doilea e deja reparat în planner și așteaptă doar o re-planificare.

### Fereastra `fit` mai îngustă: MĂSURATĂ ȘI RESPINSĂ

Sugestia era a mea: dacă `fit` ar folosi o fereastră 4:5 în loc de tot cadrul, saltul ar coborî de la
3,16x la ~1,4x. Măsurat pe 50 de cadre `fit` din 27 de clipuri, luând cutia de conținut non-negru din
banda sursei:

```
pilot2c8a  median 1,00   min 1,00      pilot6b38  median 1,00   min 0,71
pilotee0e  median 1,00   min 0,96      pilotf81b  median 1,00   min 1,00

cadre care încap într-o fereastră de 80% din lățime:   2/50
cadre care încap într-o fereastră de 70% din lățime:   0/50
o fereastră 4:5 păstrează 45% din lățimea sursei
```

**48 din 50 folosesc toată lățimea.** O fereastră 4:5 ar tăia conținut pe practic fiecare shot `fit`
din corpus — exact lucrul pentru care `fit` există. Ideea e moartă, și e bine că a murit pe o
măsurătoare de zece minute și nu pe un batch.

Rămâne deci o singură pârghie pentru cele 54 de joncțiuni: **animarea tranziției**. Și trebuie spus
cinstit că nu a fost respinsă de nimeni — am prezentat-o ca opțiune ne-recomandată fără să fi măsurat
nimic despre ea.

## Ce urmează din asta

Defectul e în **randare, la joncțiunea dintre compoziții** — nu în detecția de fețe, pe care el o
declară explicit bună, și nu în alegerea momentului. Asta îl scoate din R3a/R3b (care aleg subiectul
și regimul) și îl pune lângă R1: e o proprietate a felului în care `dynamic_render` exprimă o
schimbare de compoziție, unde `crop` se reconfigurează și `scale` îl urmează în același `sendcmd`.

S-a măsurat: e un **salt de scală**, median 3,58x, minim 3,16x, pe toate cele 84 de joncțiuni.
Vezi secțiunea de mai sus.

**Direcția aleasă (31 august): nu se mai taie acolo** — implementat ca
`absorb_brief_fit_islands`.

**Cum s-a luat decizia, fiindcă asta contează pentru cine o recitește:** i-am pus omului trei
opțiuni și am marcat-o pe asta „recomandat". A ales-o. Deci este alegerea MEA validată de el, nu una
independentă — și în special **animarea tranziției nu a fost respinsă de nimeni**, doar
ne-recomandată de mine, fără nicio măsurătoare în spate. Prima versiune a acestui document scria că
a respins-o; era o supra-interpretare.

Ce rămâne adevărat fără interpretare: minimul de 3,16x e o identitate a geometriei 16:9, deci „mai
măsoară pe alte surse" nu putea schimba concluzia.

## Tranzițiile de joncțiune — VERDICT, 4 septembrie 2026: hard cut, 4 din 4

Cele patru joncțiuni cronometrate au fost randate în trei tratamente — hard cut (ce se livrează),
rampă de 0,30s și rampă de 0,60s — prin `render_dynamic_clip` însuși, nu printr-un filtergraph
construit alături. Verdictul omului, unanim:

```
 8.82|hard-cut = best
22.65|hard-cut = best
38.40|hard-cut = best
52.97|hard-cut = best
```

Motivul lui, verbatim: **„în momentul în care se focusează/defocusează pe om, hard cut oferă o
imagine mai bună din punct de vedere al fluidizării."**

**Deci rampa NU se aplică.** `ease_s` rămâne 0,0 pentru fiecare apelant, mecanismul rămâne în cod
nefolosit, iar comportamentul livrat nu se schimbă. Costul întregii investigații a fost plătit ÎNAINTE
de a fi aplicată pe corpus, ceea ce era chiar scopul.

### Ce NU stabilește acest verdict, și e ușor de citit greșit

**Nu stabilește că joncțiunea e în regulă.** Omul a comparat trei prezentări și a ales-o pe cea mai
bună dintre ele; comparația nu conținea nicio variantă pe care el să o fi numit *bună*. Plângerea
originală — „în momentul în care intră 16:9 cam 2 secunde nu e tranziția bună" — rămâne exact acolo
unde era, pe aceleași 57 de joncțiuni lungi.

Ce s-a închis e o singură întrebare: **animarea nu e răspunsul.** Iar cealaltă pârghie, o fereastră
`fit` mai îngustă, a fost măsurată și respinsă separat (48 din 50 de cadre folosesc toată lățimea).
Deci ambele idei pe care le aveam sunt moarte, iar problema rămâne deschisă **fără o soluție
propusă** — ceea ce e o stare mai onestă decât una cu o reparație pe care nimeni n-a validat-o.

Prima versiune a acestui document scrisese despre o decizie anterioară că omul „a respins" o opțiune
pe care eu o marcasem ne-recomandată. Nota asta există ca să nu se repete: verdictul de aici e
„dintre astea trei, asta", nu „asta e bună".

### Un defect al rampei, găsit printr-un cadru și nu prin cod

Merită păstrat fiindcă e a doua oară în același batch. `_position_exprs` întoarce `0, 0` fix pentru
un shot `fit` — corect doar când fereastra E tot canvasul, fiindcă atunci originea și poziția
centrată sunt același punct. Încetează să fie același punct în clipa în care fereastra e mai mică,
adică exact ce face o rampă: **fixată la `0, 0`, o fereastră de la mijlocul rampei stă în colțul
stânga-sus al canvasului, care e padding transparent**, deci compozitul arăta fundalul blurat și
nimic altceva.

Scriptul `sendcmd` arăta perfect rezonabil. Un cadru extras de la mijlocul rampei e ce a găsit-o.

Prima oară fusese rampa care se prăbușea fiindcă `_size` limitează înălțimea la SURSĂ (1080) în loc de
canvas (3412) — găsită tipărind cele două scripturi unul lângă altul. În ambele cazuri, citirea
funcției nu ar fi prins nimic.

## Ce NU demonstrează

- Nimic despre selecție. Astea sunt verdicte de RANDARE, pe clipuri alese pentru că poartă un
  defect anume — nu un eșantion din care se pot calcula rate.
- Nimic despre celelalte 38 de exporturi ale piloturilor.
- `vlog` are 4 bad fără nicio notă, deci **nu se știe ce e stricat acolo.** E de reîntrebat.
