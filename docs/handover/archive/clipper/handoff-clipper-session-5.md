# Handover — Clipper, sesiunea 5: încadrarea și instrumentul cu care a fost măsurată

**5–9 septembrie 2026.** Istoric. Starea curentă este în
[`areas/clipper/CURRENT.md`](../../areas/clipper/CURRENT.md).

Sesiunea a început cu un defect de încadrare și s-a terminat cu descoperirea că
instrumentul cu care măsuram încadrarea era greșit — de șase ori mai imprecis
decât se declara pe sine. Aproape tot ce e scris aici a fost mai întâi raportat
greșit și corectat după. Ordinea corecțiilor este miezul documentului: fiecare
verdict a fost răsturnat de un instrument mai bun, iar ultimul a fost răsturnat
de primul care s-a uitat la obiectul întreg.

Interlocutor extern: **Codex** (ChatGPT desktop). Aproape fiecare defect de mai
jos a fost găsit de el, nu de mine.

---

## 1. Defectul livrat: stratul suprimat

`plan_dynamic_edit` alegea o a doua regiune „vie" pe surse care nu au a doua
cameră. Pe `pilotf81b` asta însemna **48,1 secunde în care încadrarea livrată
nu conținea subiectul**.

Reparat în `services/clipper/layout_policy.py`: `SECOND_CAMERA` /
`NO_SECOND_CAMERA`, cu `SETTING = "source_has_a_second_camera"`, iar
`plan_dynamic_edit(..., no_second_camera=True)` scoate ramura `alive`.

**Măsurat pe cadre randate, nu pe plan:** 48,1 s → 0,0 s off-subject; fețele
găsite în exportul re-randat 60/71 → **71/71**; clipul de control a ieșit
identic pe octeți (`23d9ec2a87842a67`). Numele `TWO_REGION` / `ONE_REGION` a
fost schimbat fiindcă descria forma, nu cauza.

## 2. Backup-ul care pierdea generația pe care o înlocuia

`replan_and_rerender._preserve` copia `exports/` în `exports_pre_replan/` și,
dacă directorul exista, **continua**. Comentariul argumenta că originalele sunt
în siguranță fiindcă `copytree` refuză o destinație existentă — adevărat, și
irelevant:

```
run 1   exports/ = A  ->  exports_pre_replan/ = A, exports/ devine B
run 2   backup-ul există, deci nu se păstrează nimic; exports/ devine C, B e pierdut
```

`rerender_pilots` avea bug-ul în oglindă: refuza, deci nu putea păstra niciodată
a doua generație. O singură implementare: `services/clipper/export_generations.py`,
cu serial (`exports_pre_replan_02`) și un `_generation.json` scris **în interiorul
copiei**, ca inventarul să nu poată devia de la ce descrie.

## 3. Regiunile pe faze

O încadrare constantă pe tot clipul nu poate ține și o încheietură în stânga jos
și un ceas în centru. Definiția de fază e a lui Codex: **o porțiune pe care
PRIORITATEA VIZUALĂ e aceeași**, nu un construct al planificatorului.
`b23c14c41495` are patru: `speech`, `watch-worn`, `removal`, `screen`.

Stiva: `caption_region` (regiunea), `build_phase_regions` (construcția),
`verify_phase_regions` (verificarea), `select_fresh_lot` (alegerea loturilor),
`watch_annotations` + `watch_annotations_fresh` (adnotarea).

Reguli care au costat ceva să fie stabilite:

- o regiune construită din niște cutii și verificată pe aceleași cutii nu
  demonstrează nimic — de aici loturile ținute deoparte;
- disjuncția se dovedește pe **indexul cadrului decodat**, nu pe timpii ceruți;
- `dynamic_plan["subject"]["face"]` e o medie pe tot clipul și nu are autoritate
  asupra niciunei faze;
- fuziunea a două regiuni se raportează cu costul ei față de **fiecare**, nu
  împărțit la cea mare — prima versiune numea „fuzionabilă la 4,4%" o fuziune
  care micșora faza de vorbire cu 37%.

## 4. Cele cinci defecte ale instrumentului, în ordinea în care au apărut

Acesta e materialul care nu trebuie repetat.

### 4.1 Planșele cu două cadre alăturate

Prima adnotare a fost citită de pe planșe `--scale 2.0 --cols 2`. Fiecare tile
are propria riglă 0→1. **Fiecare cadru din tile-ul drept a ieșit deplasat la
dreapta cu 0,26–0,30 din cadru:**

```
f2275 face  citit 0.68-0.85   real 0.375-0.545
f2365 watch citit 0.51-0.72   real 0.235-0.435
f2401 screen citit 0.735-0.86 real 0.475-0.665
```

Și **fiecare** cadru avea marginea de sus citită prea jos, cu până la 0,125 (180
px sursă), fiindcă vârful bandanei negre pe fundal închis e cea mai grea muchie
din imagine la 2x.

Cele trei regiuni de ceas erau construite din numerele astea. Prima rulare de
hold-out a raportat „regiunea watch-worn taie fața pe 9 din 9 cadre nevăzute, cu
până la 115 px" ca fapt despre regiune. Era un fapt despre **două treceri de
citire**. Regula: nu compara o măsurătoare cu una luată cu alt instrument.

**Un cadru pe planșă nu e o preferință, e reparația.**

### 4.2 Indexul și timpul cer laturi opuse ale lui `read()`

Cele două proprietăți ale decodorului sunt în dezacord cu un cadru **în același
moment**:

```
set(POS_FRAMES, 2243)  ->  POS_FRAMES 2243, POS_MSEC 224200
read()                 ->  POS_FRAMES 2244, POS_MSEC 224300
```

Timpul de prezentare al lui f2243 e `index/fps` = 224,3 s. Deci **indexul se
citește ÎNAINTE** (după, numește cadrul următor) **și timpul DUPĂ** (înainte,
numește cadrul precedent). Patru locuri le citeau pe amândouă înainte:
`source_caption_observation.observe` și cele trei scripturi care îl oglindesc.

Cost: fiecare etichetă de planșă, fiecare `t_decoded` și fiecare atribuire de
fază erau cu 0,1 s mai devreme — uniform, 132 din 132 de eșantioane. Un singur
cadru schimbă faza odată corectat, și e cel care conta: **f2425 era cadru de
construcție al fazei `screen` și nu e în clip** (242,5 s față de un final la
242,42). Regiunea era construită parțial dintr-o imagine pe care privitorul nu o
vede. `build_phase_regions._in_clip` refuză acum orice cadru din afara clipului.

Granițele **nu** au fost mutate cu 100 ms automat — Codex: „unele sunt alegeri
editoriale, iar baza fiecăreia trebuie identificată". Re-observate pe cadre cu
etichete corectate, toate trei cad pe numerele pe care le aveau: gestul la
224,20 (f2240 în repaus, f2242 deja ridicat), scoaterea la 230,90 (f2309),
ecranul la 239,40 (f2394).

### 4.3 Mărirea proxy-ului nu adaugă detaliu

Toată adnotarea fusese citită de pe proxy la `--scale 3.0`. Codex, într-o
propoziție: **„mărirea aceluiași proxy nu adaugă detaliu."** Proxy-ul e 480×270;
mărit la 1440 interpolează. **Sursa e 2560×1440 — de 5,33x rezoluția liniară —
și era pe disc tot timpul.**

`scripts/source_crop.py` citește sursa: adresarea rămâne a proxy-ului (cadrul n
la n/10 s → cadrul sursă `round(n/10*30)`, verificat, index diferit = refuz),
grila e etichetată în fracțiuni din **cadrul întreg** (un decupaj citit față de o
riglă proprie e capcana de la §4.1), nu mărește niciodată, scrie PNG fiindcă
întrebarea e unde se termină o muchie moale iar un codec cu pierderi inventează
exact gradientul ăla.

### 4.4 Toleranța de ±0,01 n-a fost niciodată atinsă

Lotul de calibrare — zece margini remăsurate pe sursă, fiecare ca **interval**,
nu ca punct:

```
f2251 face.top    0.155  [0.162, 0.178]   +10..+33 px   held          -> held
f2279 face.top    0.130  [0.142, 0.158]   +17..+40 px   indeterminate -> held
f2357 hand.top    0.235  [0.185, 0.200]   -72..-50 px   held          -> held
f2362 hand.top    0.030  [0.072, 0.080]   +61..+72 px   CLIPPED       -> indeterminate
f2367 hand.top    0.045  [0.072, 0.082]   +39..+53 px   CLIPPED       -> indeterminate
f2400 face.right  0.790  [0.730, 0.750]  -154..-102 px  CLIPPED       -> unmeasured
f2403 face.right  0.775  [0.730, 0.750]  -115..-64 px   CLIPPED       -> unmeasured
f2405 face.right  0.755  [0.725, 0.745]   -77..-26 px   indeterminate -> unmeasured
f2411 watch.top   0.085  [0.110, 0.130]   +36..+65 px   indeterminate -> held
f2414 watch.top   0.075  [0.110, 0.130]   +50..+79 px   indeterminate -> held
```

Deplasările ajung la 154 px sursă față de o toleranță declarată de ±25,6 px
orizontal și ±14,4 vertical — **de șase ori** — și merg **în ambele direcții**.
`f2357` e controlul care contează: cutia a fost desenată cu 0,045 **sub** vârful
mâinii pe care trebuia să o conțină. Dacă eroarea ar fi avut un semn, ar fi fost
o deplasare de scăzut; nu are, e zgomot mai lat decât toleranța.

Verdictul vine acum din faptul că muchia regiunii cade sau nu **înăuntrul
intervalului măsurat**. În regula asta nu apare nicio toleranță. Codex a
avertizat explicit împotriva reparației leneșe: „nu înlocui global ±0,01 cu
±0,05 doar fiindcă a doua valoare acoperă discrepanțele constatate."

### 4.5 Un interval citit dintr-o porțiune de contur nu delimitează obiectul

Codex a deschis decupajul salvat de mine pentru f2400: intervalul `[0.730,
0.750]` era justificat prin conturul vizibil la **înălțimea 0,20–0,35**, iar
capul se bombează mai la dreapta pe la 0,43. „Intervalul unei porțiuni de contur
nu delimitează automat marginea dreaptă a întregului cap." Cele trei rânduri
`face.right` poartă acum `whole_contour: False` și verdict `unmeasured`.

## 5. Traiectoria unei singure margini

`f2400 face.right`, în ordine:

| citire | verdict |
|---|---|
| adnotarea proxy inițială (0,790) | tăiat |
| decupaj îngust pe muchie, re-citit ([0,730, 0,750]) | ținut |
| după ce Codex a contestat justificarea | nemăsurat |
| **obiectul întreg, la rezoluția sursei** | **tăiat** |

Prima citire era aproximativ corectă. „Corecția" mea a înrăutățit-o, retragerea
a făcut-o vagă, și abia a patra s-a uitat la obiectul întreg. Aceasta e cea mai
scumpă lecție a sesiunii: **un decupaj strâns pe o muchie ascunde exact conturul
care decide unde e extrema.**

## 6. Disciplina de corectare, așa cum a stabilit-o Codex

Am refuzat la un moment dat să corectez cutii despre care știam că sunt greșite,
fiindcă toate corecțiile mergeau într-o direcție după ce văzusem verdictele.
Codex a respins și asta: **„Păstrarea unei adnotări despre care știi că este
greșită nu protejează evaluarea. Protecția este păstrarea versiunii vechi,
remăsurarea fără crop și verdict afișate, justificarea noii margini și
raportarea separată a rezultatului recalculat."**

De aceea `watch_annotations.py` poartă valorile înlocuite și un motiv pe cutie,
nu doar istoricul git: întrebarea următorului cititor e „cutia asta a fost mereu
așa, sau a fost mutată după un verdict".

## 7. Alegerea loturilor

`select_fresh_lot.py` alege cadrele **înainte** să se uite cineva la ele, după o
regulă convenită în avans: patru intervale egale pe fază, trei cadre la
`floor(n/4)`, `floor(n/2)`, `floor(3n/4)` din lista eligibilă sortată; sub 3
eligibile se iau toate și **se raportează deficitul**, niciodată completat din
alt sfert. Eligibil ≠ „nu e în tabele": ferestrele de tranziție de ±0,5 s și
cadrele citite pentru re-derivarea granițelor sunt excluse și numite.

Rezultat: **34 din 36**, cu `screen` q0 [239,40, 240,155) având un singur cadru
eligibil, f2400 — cifra și cadrul pe care Codex le prezisese independent.

## 8. Ce a livrat totuși sesiunea

- reparația stratului suprimat, măsurată pe cadre randate;
- `export_generations` + 19 inventare pe disc;
- reparația `POS_MSEC` cu test care pică pe ordinea veche;
- refuzul cadrelor din afara clipului;
- patru instrumente noi (`replay_annotations`, `source_crop`, `edge_calibration`,
  `coverage_bounds`) și cele cinci defecte de mai sus, documentate acolo unde
  se folosesc;
- 1913 teste trec.

Ce **nu** a livrat: o regiune verificată. Candidatul curent e construit din
adnotări proxy despre care se știe acum că sunt greșite.
