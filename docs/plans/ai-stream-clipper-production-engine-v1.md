# AI Stream Clipper — motor de selecție și montaj content-aware, plan de producție v1

**Data:** 2026-08-28  
**Statut:** plan aprobat pentru implementare, codul nu este încă livrat  
**Sursă pentru reasoning:** [`ai-stream-clipper-reasoning-v2.md`](ai-stream-clipper-reasoning-v2.md)  
**Sursă pentru starea curentă:** [`../handover/areas/clipper/CURRENT.md`](../handover/areas/clipper/CURRENT.md)

## 1. Obiectivul real

Motorul este gata de folosit direct în aplicație numai dacă răspunde bine la două întrebări
independente:

1. **Reasoning quality:** a găsit și a ordonat momente care merită exportate?
2. **Render quality:** a transformat fiecare moment într-un clip vertical pe care l-am publica fără
   să-l reparăm manual?

O selecție bună randată prost este un export prost. O randare frumoasă a unui moment slab este tot
un export prost. De aceea acest plan nu permite ca o metrică agregată să compenseze cealaltă axă.

Nu există o modificare de cod care poate garanta prin ea însăși „clipuri excelente”. Planul face
altceva, verificabil: împiedică activarea motorului până când calitatea este demonstrată pe surse
diverse, prin artefacte reproductibile și review uman orb.

## 2. Baseline-ul care nu trebuie uitat

Auditul `render_v3_letterbox` a inspectat toate cele 58 de exporturi din cele patru proiecte pilot.
Geometria nouă funcționează: 58/58 fișiere complete, decalaj maxim de durată 0,030s, fără întindere,
iar benzile `fit` sunt blurate, nu negre.

Ce rămâne defect:

| metrică | baseline v3 | interpretare |
|---|---:|---|
| durată totală | 45m43s | corpusul randat |
| shot-uri | 1.341 | 29,3/minut, aproximativ unul la 2,04s |
| cel mai scurt shot | 0,605s | montaj agresiv pe orice tip de conținut |
| tăieturi `fit → fit` fără schimbare vizuală | 116 | dedupe-ul compara dreptunghiul intermediar, nu imaginea livrată |
| clipuri care încep odată cu primul cuvânt | 58/58 | zero lead-in |
| clipuri cu ≤50ms după ultimul cuvânt | 22/58 | final fără aer; uneori propoziție tăiată |
| clipuri go ghost cu două sisteme de captions | 15/15 | sursa avea deja text ars |
| clipuri Moist cu UI/overlays din sursă | 14/14 | `stable_track` nu garantează creatorul |
| defecte tehnice declarate la review-ul v2 | 45/58 | precizia selecției este contaminată de render |

Aceste numere sunt baseline-ul pentru plan. O implementare nu este acceptată fiindcă „pare mai
bună”; trebuie să miște aceste cifre în direcția stabilită și să nu strice geometria deja reparată.

## 3. Deciziile de arhitectură

### 3.1 Profil de bază plus regim per secvență

Montajul nu se alege doar din eticheta proiectului. Decizia finală este:

```text
profilul tipului de conținut
  + încrederea clasificării
  + ce există în secvența curentă
  + calitatea semnalelor disponibile
  = shot-ul care se randează
```

Profilul stabilește ritmul și vocabularul de camere. Regimul secvenței stabilește ce trebuie văzut
acum: vorbitor, conversație, acțiune, material demonstrativ, reacție sau cadru de siguranță.

### 3.2 Clasificarea nesigură devine conservatoare

Etichetele existente sunt `gaming`, `podcast`, `interview`, `irl`, `commentary`, `talking_head`,
`tutorial`, `sports`, `low_dialogue`, `unknown`. Ele se mapează la profile de editare, nu se creează
un al doilea clasificator.

Dacă lipsește încrederea sau este sub pragul calibrat, se folosește profilul `conservative`, cu
shot-uri lungi și cadru sigur. O clasificare slabă nu are voie să activeze un montaj mai agresiv.

### 3.3 Reasoning-ul nu învață defectele rendererului

Verdictul uman despre un moment și verdictul despre randarea lui se păstrează separat. Un `reject`
pentru browser vizibil, captions duble sau crop greșit nu devine exemplu negativ pentru ranker-ul
care a ales momentul. Feedback-ul de render nu intră în `training_rows()` pentru selecție.

### 3.4 Fără active-speaker inventat

Interviul nu comută între fețe până când există un semnal măsurat care leagă vocea de subiect.
Fără acel semnal, se păstrează un cadru sigur cu ambii vorbitori sau un crop stabil pe vorbitorul
deja vizibil. Nu se ghicește vorbitorul din mărimea feței sau din alternanță.

### 3.5 Un singur corector bounded

Preflight-ul poate face maximum o corecție automată de boundary, compoziție sau captions, apoi
reverifică. Dacă mai eșuează, clipul rămâne pentru review manual; nu intră într-o buclă de
„reparare” care mută rezultatul până trece testul.

## 4. Profilele inițiale de montaj

Valorile următoare sunt **guardrail-uri alese**, nu rezultate demonstrate. Se calibrează pe corpus,
iar planul interzice prezentarea lor drept adevăruri măsurate înainte de gate-ul uman.

| profil | tipuri mapate | ritm inițial | reguli principale |
|---|---|---|---|
| `talking_head` | talking_head, commentary | 5–10 tăieturi/min | ține subiectul; reframe numai la idee sau emoție clară |
| `conversation` | podcast, interview | 5–12/min | cadru stabil; vorbitor activ numai cu semnal sigur; `fit` pe slide |
| `action` | gaming, sports | 12–28/min în acțiune, 5–12/min în pauze | evenimentele controlează ritmul; creatorul și acțiunea au roluri distincte |
| `exploration` | irl, low_dialogue | 6–15/min | urmează scenele sursei; `fit` pentru spații și obiecte; nu vânează orice față |
| `instructional` | tutorial | 4–10/min | stabilitate cât timp informația trebuie citită; niciun pas tăiat |
| `conservative` | unknown sau clasificare slabă | 3–8/min | hold lung, `fit` sigur, fără alternare forțată |

Regimurile per secvență sunt o listă închisă:

- `speaker`: există un subiect urmărit cu încredere;
- `conversation`: două sau mai multe persoane relevante sunt vizibile;
- `action`: există acțiune măsurată în regiunea potrivită;
- `visual_evidence`: diagramă, screen share, obiect sau B-roll care trebuie văzut integral;
- `reaction`: creator stabil plus conținut extern;
- `safe`: semnalele sunt contradictorii sau insuficiente.

`safe` nu este o eroare. Este comportamentul corect când alternativa ar fi un crop inventat.

## 5. Contractul fiecărui shot

Fiecare shot persistat trebuie să aibă:

```json
{
  "t0": 0.0,
  "t1": 6.4,
  "edit_profile": "conversation",
  "regime": "speaker",
  "composition": "crop",
  "camera": "face_medium",
  "reason": "tracked_subject",
  "confidence": 0.91,
  "rect": {"x": 0, "y": 0, "w": 1080, "h": 1920}
}
```

`reason` este dintr-o listă închisă, nu proză liberă. Planul trebuie să explice de ce a tăiat, nu
doar unde. Sidecar-ul mai păstrează `edit_profile_source`, încrederea clasificării,
`render_version`, metricile preflight și orice fallback.

## 6. Batch-uri de implementare

### Batch R0 — evaluatorul repetabil și contractul de trace

**Scop:** transformă auditul manual într-un gate pe care orice agent îl poate rerula.

**Fișiere:** nou `server/services/clipper/edit_quality.py`, nou
`scripts/audit_clipper_exports.py`, `storage.py`, test nou `test_clipper_edit_quality.py`.

**Modificări:**

- metrici pure pentru shot-uri/minut, durată minimă, tăieturi echivalente după compoziție,
  lead-in/out, distribuția regimurilor și captions duplicate declarate;
- raport per clip, per proiect și macro pe surse;
- sidecar-ul și raportul declară exact versiunea rendererului și input fingerprint-ul;
- lipsa datelor este `unavailable`, niciodată zero.

**Teste:** corpus sintetic cu crop/fit/crop, lipsă sidecar, plan vechi, durată zero, două artefacte
din rulări diferite.

**Gate:** scriptul reproduce baseline-ul structural: 1.341 shot-uri, aproximativ 29,3/min și 116
tranziții `fit → fit`. Diferențele sunt investigate, nu corectate în raport ca să iasă cifra.

### Batch R1 — echivalența vizuală după compoziția finală

**Scop:** o tăietură există numai dacă imaginea livrată se schimbă.

**Fișiere:** `dynamic_geometry.py` (cheia și merge-ul), `dynamic_edit.py` (planner-ul care îl
apelează), `clipper_render_plan.py` (numai pragul de fallback static, vezi mai jos), testele dynamic.

**Premisa, corectată la implementare.** Prima versiune a acestui plan spunea că dedupe-ul rulează
înainte de compoziția finală. Nu rulează: `composition` este atribuit fiecărui shot în aceeași buclă
care îl construiește, iar merge-ul rulează după. Defectul era **cheia**, nu ordinea —
`_merge_dead_cuts` compara dreptunghiul planificat, iar două shot-uri `fit` au dreptunghiuri diferite
deși livrează același cadru întreg.

**Modificări:**

- cheia vizuală se ia din ce emite rendererul: timeline-ul de dimensiuni și expresiile de poziție;
- două shot-uri `fit` adiacente sunt echivalente indiferent de crop-urile intermediare;
- un shot care se mișcă nu poate fi absorbit — la tăietură mișcarea ar reporni; un shake identic se
  unește, fiindcă expresia folosește timpul absolut și continuă neîntreruptă peste joncțiune;
- shot-urile echivalente se unesc păstrând întreaga cronologie și **metadata primului shot**. Planul
  cerea „motivul dominant", dar shot-urile nu au încă `reason` sau `confidence`: alegerea între două
  seturi de energii ar fi o regulă inventată deghizată în măsurătoare. Consolidarea motivului aparține
  batch-ului care introduce contractul `reason`;
- planul reține `shot_count_before_merge`, fiindcă `clipper_render_plan` respinge un plan cu mai puțin
  de două shot-uri și îl randează static. Fără asta, un clip a cărui singură vină era o tăietură
  invizibilă și-ar schimba rendererul, crop-ul și captions-urile;
- întâi se face dedupe exact; pragurile perceptuale nu se introduc până nu sunt măsurate;
- test obligatoriu `crop → fit → fit → crop`, inclusiv sendcmd-ul rezultat.

**Gate:** zero tăieturi exact echivalente pe cele 58 de planuri; aceeași durată, aceleași captions,
zero regresii de geometrie și zero cadre întinse. Gate-ul se rulează pe o proiecție **versionată** a
celor 58 de planuri (`server/tests/fixtures/pilot_shot_plans.json`), nu pe `data/clipper`: corpusul
real este privat, mutabil și pe cale să se schimbe, iar o re-randare corectă l-ar face să contrazică
exact cifra pe care o verifică. `scripts/build_shot_merge_fixture.py` raportează aceleași cifre de pe
corpusul real și regenerează proiecția.

**Ce NU intră aici:** auditul R0 continuă să raporteze 116 pe cele 58 de exporturi existente, fiindcă
citește sidecar-urile randate, iar R1 schimbă planner-ul. Cele 116 devin 0 abia după re-randarea
piloturilor.

### Batch R2 — resolverul de profile și controlul din aplicație

**Scop:** fiecare clip primește o gramatică potrivită tipului său, cu fallback conservator.

**Fișiere, lista reală.** Nou: `edit_profiles.py`, `workers/clipper_scoring.py` (split la 500 de
linii), `src/types/clipper-reasoning.ts`, `src/types/clipper-captions.ts` și `src/types/clipper-editing.ts`
(același motiv), `src/components/clipper/edit-mode-field.tsx`. Atinse: `clipper_render_plan.py`,
`clipper_render_jobs.py` (sidecar), setările Clipper și `config.py`, `segment_type.py`,
`clipper_build.py`, `clipper_finalize.py`, `serialize.py`, `models.py` și `database.py` (două coloane
noi pe clip), `src/types/clipper.ts`, `source-form.tsx`, `reasoning-panel.tsx`, plus `routers/clipper.py` și
`tests/test_clipper_api.py` — testul de round-trip peste HTTP a arătat că `patch_settings` normaliza
dicționarul PARȚIAL primit, deci un PATCH pe orice altă cheie reseta tăcut `edit_mode` și
`reasoning_mode` la valorile rig-ului.

Prima versiune a listei omitea tot lanțul de propagare. Fără el încrederea nu ajunge niciodată la
clip, iar resolverul nu poate distinge o clasificare măsurată de una moștenită.

**Contract setare:** `edit_mode = legacy_dynamic | content_aware_shadow | content_aware`.

- `legacy_dynamic` păstrează rendererul actual pentru rollback;
- `content_aware_shadow` rezolvă și înregistrează PROFILUL, dar livrează planul legacy. În R2 nu se
  calculează un plan nou: profilul stabilește gramatica, iar plannerul care o aplică apare în
  batch-urile următoare;
- `content_aware` livrează planul nou numai după gate-ul final;
- browserul pornește pe `Server default` și nu suprascrie configurația rig-ului;
- verdictul local din `segment_types` propagă la candidat și încrederea lui; override-ul manual are
  proveniență proprie, iar lipsa încrederii rămâne `unknown`, nu este inventată din scorul sursei;
- profilul rezolvat și motivul sunt vizibile în sidecar și în panoul de reasoning/render.

**Teste:** toate cele zece content types, confidence lipsă, unknown, setare invalidă, round-trip
UI/API/worker, proiect vechi fără cheie.

**Gate:** în shadow, planul legacy livrat rămâne identic — demonstrat rulând `_decide_render` în
ambele moduri și cerând egalitate pe plan, crop, captions, trim, fps, watermark și fingerprint, cu
singura diferență în câmpul diagnostic `edit_profile`. `applied` rămâne fals pentru orice mod care
poate ajunge în producție, inclusiv un rig configurat greșit pe `content_aware`: routerul și workerul
folosesc același resolver de mod disponibil.

### Batch R3a — prezența și compoziția ancorate pe creator

**Scop:** separă „există o față undeva” de „creatorul pe care trebuie să-l urmărim este prezent”.

**Măsurat înainte de implementare**, pe `analysis/faces.json`: `stable_track` găsește o ancoră pe
**unul** din cele patru piloturi. Pe acela — Moist, `pilot2c8a` — doar **464 din cele 1.285 de
eșantioane cu față, adică 36%, sunt COMPATIBILE CU ANCORA** — nu „sunt creatorul": nimeni nu a
etichetat cutiile, iar geometria e tot ce știm. Ancora e la (34, 141), lată de 25px, deci toleranța
efectivă e podeaua de 40px, nu lățimea feței; un overlay în
colțul din stânga sus. Pe celelalte trei piloturi nu există ancoră și nimic nu se filtrează.
`scripts/measure_creator_presence.py` reproduce cifrele.

**Ce NU spune măsurătoarea, și de ce.** Prima versiune a scriptului raporta și „821 din 1.999 de
eșantioane de prezență se schimbă". Cifra era lipsită de sens: `faces.json` are pe piloturi un pas
median de **6,7 secunde**, la care `ENTER_S` de 3,0s se rotunjește la un eșantion, adică histerezis
zero, în timp ce producția aplică douăsprezece eșantioane de histerezis pe track-ul de 0,25s. Este
exact capcana pe care docstring-ul din `dynamic_subject` o descrie deja. Compatibilitatea cu ancora
este o proprietate a cutiilor și nu depinde de rata de eșantionare; efectul asupra prezenței se poate
măsura doar pe track-ul dens, la randare.

**Fișiere:** `dynamic_subject.py`, `clipper_render_plan.py`, `clipper_render_jobs.py` (sidecar),
`scripts/measure_creator_presence.py`, testele dynamic.

**Modificări:**

- când există `stable_track`, prezența creatorului se calculează numai din detectări compatibile cu
  ancora; fețele din materialul reacționat nu mai țin artificial modul `crop` activ;
- eșantioanele nu se elimină niciodată, doar cutiile lor: timeline-ul e indexat pozițional, iar
  comprimarea lui ar deplasa fiecare timp de după prima eliminare;
- fără ancoră, track-ul rezultat este identic cu cel brut — cazul care deja funcționează;
- histerezisul devine RETROSPECTIV: după confirmarea unei absențe, tot run-ul e marcat absent,
  inclusiv primele trei secunde. Doar dispariția se antedatează; revenirea are deja regula de dovadă
  unanimă din `span_has_subject`;
- compoziția propusă se calculează peste shot-urile EXISTENTE și se scrie în sidecar ca `creator_view`,
  lângă cea livrată. **Înregistrată, niciodată aplicată:** `legacy_dynamic` rămâne înghețat și
  exportul shadow rămâne ce a înghețat R2.

**Gate automat:** zero cutii incompatibile cu ancora contribuie la prezența creatorului; fără ancoră
track-ul e identic cu intrarea; run-urile confirmate sunt marcate de la începutul lor; planul și
fingerprint-ul livrate rămân identice între `legacy_dynamic` și `content_aware_shadow`; sidecar-ul
păstrează separat propunerea, ancora, numărul de eșantioane compatibile și motivul.

**Gate vizual: rămâne DESCHIS.** Nu există substitut automat — un face box sau un crop valid geometric
nu demonstrează că persoana corectă e în cadru. Formularea corectă până se uită un om este
„implementare terminată, gate vizual pending".

### Batch R3b — regimurile și topologia shot-urilor

**Scop:** regimul secvenței devine unitatea de decizie, fără să reintroducă tăieturi invizibile.

**Fișiere, lista reală.** Nou: `dynamic_regimes.py`, `series.py`,
`tests/test_clipper_regimes.py`, `tests/test_clipper_regime_signals.py`. Atinse: `dynamic_subject.py`
(prezența off-anchor), `dynamic_window.py` (pasul real al seriei de mișcare), `dynamic_edit.py`
(folosește scalarea comună), `clipper_render_plan.py` și `clipper_render_jobs.py` (sidecar).
`dynamic_cuts.py` NU este atins: R3b nu taie nimic.

**R3b este o propunere shadow.** Segmentarea regimurilor și topologia de tratament se calculează și se
înregistrează; planul livrat rămâne exact ce a înghețat R2. Materializarea lor în planul livrat
aparține căii care construiește complet planul `content_aware`, înainte ca modul să devină selectabil
— altfel se încalcă înghețul stabilit în R2.

**Modificări:**

- lista închisă `speaker | conversation | action | visual_evidence | reaction | safe`, care există
  deja în `edit_profiles.REGIMES`;
- fiecare segment are `regime`, `reason` și `confidence`/`evidence`;
- o secvență fără creator poate deveni `visual_evidence`/`fit`, nu „game camera” forțată;
- fără active-speaker verificabil, `conversation` folosește cadru comun sau hold, nu două crop-uri
  ghicite;
- regimul e decis înainte de tratamentul vizual; nu se votează majoritar peste un shot mixt.

**Ce a fost măsurat, nu presupus.** Regimurile se decid PE EȘANTION, nu pe shot: un verdict ponderat
peste un shot este chiar votul majoritar pe care planul îl interzice, iar coprezența — singurul lucru
care distinge `reaction` de un talking head — este un fapt la nivel de eșantion, pe care două ponderi
de 50% dintr-un shot îl declară adevărat pentru două jumătăți care nu se suprapun niciodată.

**Ce refuză să ghicească**, fiecare descoperit prin măsurare: seria de mișcare e pe alt ceas (cadre
întregi, deci un proxy de 10 FPS eșantionează la 0,2s, nu 0,25; indexul 0 e o santinelă, iar valoarea
`j` acoperă intervalul DINAINTE), acoperirea și variabilitatea sunt axe independente, lipsa
timestamp-urilor de cuvinte nu e tăcere, un track mai scurt decât clipul lasă `target_unknown`, iar
evidența se mediază doar peste eșantioanele măsurate și e `None` acolo unde nu s-a măsurat nimic.

**Ce nu are voie să pretindă.** `stable_track` găsește un cluster geometric stabil, nu o persoană.
Cheile vizuale sunt `crop_anchor`, `crop_subject` și `fit_full`; `crop_creator` poate exista abia după
o verificare reală de identitate sau după gate-ul vizual uman. Tot ce persistă sidecar-ul spune
`target`, iar `target_basis` spune ce fel de țintă a fost.

**Contractul care evită regresia R1.** Nu orice graniță de regim are voie să forțeze o tăietură
fizică — asta ar reintroduce exact tăieturile invizibile pe care le-a scos R1. Ordinea corectă:
construiești `regime_segments` contigue și fără goluri; calculezi tratamentul vizual pentru fiecare;
**unești segmentele adiacente când cheia vizuală finală este identică**; păstrezi proveniența
regimurilor separat, chiar dacă rezultă un singur shot vizual.

**Teste de regresie pe cazurile măsurate:**

- Jensen: diagramă → `fit`, vorbitor → `crop`, cadru cu doi oameni → sigur;
- vlog: perdea/cameră/obiect fără om → `fit`, persoană urmărită → `crop`;
- Moist: fața din video-ul reacționat nu este creator; lipsa webcamului nu mută crop-ul pe browser;
- go ghost: privirea în jos nu produce `fit` dacă subiectul rămâne detectabil în secvență.

**Gate:** zero cazuri cunoscute în care browserul este confundat cu creatorul; cele două comutări
false `fit` dispar; cadrele Jensen și vlog folosite în audit trec verificarea vizuală.

### Batch R4 — ritmul content-aware, fără alternare forțată — INSTRUMENTAT 30 aug 2026

**Scop:** camera se schimbă pentru că informația vizuală o cere, nu pentru că a trecut 1,8s.

**Fișiere, corectate la implementare:** nou `dynamic_rhythm.py`, nou `dynamic_rhythm_pace.py`, nou
`clipper_shadow_views.py` (ambele split-uri la 500 de linii), `edit_profiles.py` (`band_for`),
`clipper_render_plan.py`, `clipper_render_jobs.py`, teste noi `test_clipper_rhythm.py`,
`test_clipper_rhythm_pace.py`, `test_clipper_rhythm_export.py`.

**`dynamic_cuts.py` și `dynamic_edit.py` NU sunt atinse, și asta este deliberat.** Planul le lista,
dar a le modifica ar schimba planul livrat — pe care R2 l-a înghețat și de care depind gate-urile
vizuale încă deschise pentru R3a și R3b. Gramatica nouă există complet, pură și testată; ea devine
livrabilă abia când `content_aware` intră în `SELECTABLE`, iar `delivers_profile()` face comutarea
singur. Până atunci propunerea se scrie în sidecar ca `rhythm_view` și nu mișcă niciun cadru.

**Regula, și este tot batch-ul: o tăietură are nevoie de un MOTIV și de un LOC.**

- Motivul e o schimbare declarată în ce trebuie văzut: `treatment_change` (de la R3b),
  `source_scene_cut` (sursa a tăiat ea însăși), `action_beat` (un onset într-un interval pe care R3b
  l-a măsurat ca acțiune). Listă închisă.
- Locul e o graniță unde tăietura nu cade în mijlocul unui cuvânt — `_boundaries`, reutilizat, nu
  reimplementat.
- **O pauză fără nimic în spate este exact tăietura pe care batch-ul o elimină.**

**Asimetria dintre cele două feluri de motiv.** `treatment_change` e OBLIGATORIU: cadrul a devenit
greșit, deci taie chiar și fără graniță (`placement: unsnapped`) și chiar peste `min_shot_s`, cu
violarea înregistrată. A ține un crop pe o ancoră goală ca să protejezi o lungime minimă schimbă un
defect vizibil pe o metrică invizibilă. Restul sunt oportunități, iar o oportunitate care nu poate fi
luată curat nu se ia.

**Patru corecții din review, toate găsite de Codex citind codul, nu descrierea lui:**

- **Două schimbări obligatorii nu pot fi satisfăcute de aceeași tăietură.** La 5,0s și 5,2s ambele
  se agățau de pauza de la 5,1s, a doua era absorbită ca duplicat, iar tratamentul care exista doar
  între ele nu se vedea niciodată — fără tăietură, fără `held`, fără violare. O tăietură satisface
  o schimbare doar dacă stă la MOMENTUL ei; altfel a doua rămâne `unsnapped` sau se raportează ca
  `required_collision`. Fiecare tăietură păstrează `requests: [[motiv, moment]]`, nu un singur
  `asked_at`.
- **Coada runt nu e o violare, e un refuz.** O tăietură la 9,9s într-un clip de 10s produce un flash
  de 100ms. A o raporta și a o lăsa în `cuts` prezenta drept montaj valid o cerință imposibil de
  materializat. Se scoate din `cuts` și intră în `required_conflicts`; R5 decide dacă extinde
  fereastra sau mută boundary-ul.
- **Acoperirea parțială nu e o partiționare.** `action_measured` era adevărat și cu
  `coverage=partial`, deci toate secundele nemăsurate intrau automat în `quiet` și un minut pe care
  nimeni nu l-a măsurat ieșea `below`. Partiționarea cere acum `complete` ȘI `variable`; beat-urile
  rămân acceptate în segmentele `action` individuale, fiindcă acolo măsurătoarea există.
- **`indeterminate` ascundea un `above` demonstrabil.** Trei tăieturi în patru secunde sunt 45/min
  față de un plafon de 28, iar durata scurtă nu face asta ambiguu. Doar podeaua are nevoie de spațiu,
  deci `above` se verifică primul.

**A doua rundă, două „conflicte fabricate de plasare".** Ambele declarau imposibilă o cerință
obligatorie care se putea materializa:

- **Snap-ul nu mai trece dincolo de următoarea schimbare obligatorie.** Mutarea unei tăieturi peste
  ea păstrează ordinea numerică și tot pierde tratamentul: shot-ul de dinainte arată încadrarea de
  DINAINTEA acestei schimbări, deci intervalul dintre cele două nu apare deloc pe ecran. Găsit pe
  chiar fixture-ul din testele batch-ului — 5,000s și 5,050s cu pauză la 5,300s.
- **O schimbare obligatorie nu mai face snap în zona cozii.** Una la 9,2s dintr-un clip de 10s se
  muta la 9,5s, era scoasă de walk-back-ul cozii și raportată ca imposibilă, deși propriul ei moment
  lasă un shot perfect legal de 0,8s.

**A treia rundă, simetria:** snap-ul e mărginit de schimbările obligatorii **în ambele sensuri**.
Înapoi, tăietura care introduce schimbarea curentă s-ar întâmpla înainte ca precedenta să fi început;
ordinea tăieturilor nu prinde asta, fiindcă o tăietură dinaintea schimbării precedente dar de după
TĂIETURA precedentă e perfect în ordine. Marginea cozii e inclusivă — `duration - min_shot_s` e
ultimul loc legal — iar conflictele ies sortate temporal, fiindcă walk-back-ul le adăuga invers.

**A patra rundă, de precizie.** Marginile filtrau timpul BRUT al graniței în timp ce tăietura se
plasa la cel rotunjit: o pauză la 5,0499996 trecea de un plafon de 5,05 și ateriza exact pe el, pe
chiar schimbarea pe care nu avea voie s-o traverseze, unde schimbarea aceea se contopea în ea și
intervalul dintre tratamente dispărea din nou. Granițele se rotunjesc acum o singură dată, înainte de
orice test. Și egalitatea de moment compara `existing["t"]` — unde a ajuns tăietura după snap, care
poate fi departe de ce a cerut cineva; se compară acum cu `requests`, deci două motive împart o
tăietură doar dacă au fost CERUTE în același moment.

`required_collision` nu mai e revendicat drept „de neatins": cu marginile puse, o schimbare
ulterioară ar trebui să găsească întotdeauna momentul propriu liber, dar asta e un argument, nu o
demonstrație. Ramura rămâne ca **gardă de integritate** — alternativa la o constantă nefolosită e un
tratament care dispare în tăcere.

**A treia rundă (P2):** o tăietură scoasă la coadă păstrează TOATE motivele pe care le răspundea, nu
doar primul — o cerință care nu mai apare nicăieri în raport e mai rea decât una raportată ca
imposibilă.

**A cincea, de atribuire:** o tăietură la granița `action → speaker` nu e o tăietură `quiet`, e o
**tranziție**. Se numără separat, creditată niciunei benzi — altfel montajul liniștit pare mai agitat
dintr-un motiv care nu are legătură cu materialul liniștit.

**Guardrail-urile sunt raportate, nu impuse.** Nimic nu adaugă o tăietură ca să atingă o bandă și
nimic nu scoate una ca să rămână în ea — o propunere umplută până la bandă ar face banda
nefalsificabilă. `action` e singurul profil judecat pe două benzi, împărțind clipul în secundele pe
care R3b le-a numit `action` și restul; fără măsurătoarea de mișcare ambele partiții ies
`unavailable`, niciodată contopite. `indeterminate` este un verdict real: sub `60 / lo` secunde
propria podea a benzii nu așteaptă încă nicio tăietură.

**Ce refuză să ghicească.** `UNMEASURED` numește, per profil, ce cere §4 și nimic din repo nu
măsoară: `talking_head` cere reframe „la o idee sau emoție clară", iar cel mai apropiat lucru care
există e un regex de cuvinte-cheie — o listă care conține „bro" și „lol" nu este o emoție, iar
promovarea ei la motiv de tăiere ar fi exact semnalul inventat pe care §3.4 îl interzice pentru
vorbitorul activ. De aceea `talking_head` și `conversation` ies `below` banda lor. **Golul e
măsurătoarea care lipsește, nu un ritm care are nevoie de umplutură.**

**Profilul nu se schimbă în interiorul clipului**, fiindcă `edit_profiles.resolve` răspunde o
singură dată per clip din tipul lui de conținut. Regula planului e satisfăcută pentru că schimbarea
nu există, nu pentru că e suprimată — și niciun motiv din lista închisă nu derivă din profil.

**Teste:** talking-head liniștit (aceleași granițe, `_cut_times` livrat taie de opt ori, R4 de zero
ori — contrastul rulează funcția livrată, nu îl descrie), slide de 8s, burst de gaming, scenă IRL,
graniță de regim cu aceeași cheie vizuală, beat în afara acțiunii măsurate, semnale absente.

**Gate:** rămâne deschis. Cifrele pe corpus se pot obține doar re-randând piloturile, iar „auditul
uman nu mai descrie montajul drept agitat" cere același om ca gate-urile R3a/R3b. Review-ul extern
(șase runde) a închis batch-ul **ca instrumentare shadow, nu ca motor activ** — cele două nu se
confundă: finalizarea lui R4 nu este validare pentru producție.

### Batch R5 — completion check și boundary repair determinist — INSTRUMENTAT 30 aug 2026

**Scop:** momentul ales devine o fereastră completă, nu o propoziție tăiată la scor maxim.

**Fișiere, corectate la implementare:** nou `boundary_completion.py`, nou
`scripts/audit_clipper_boundaries.py`, `candidate_terms.py` (`SENTENCE_EDGE_S`), `candidates.py`
(literalul promovat), `clipper_build.py` (înregistrarea), test nou
`test_clipper_boundary_completion.py`.

**`candidate_boundaries.py` și `story_evidence.py` NU sunt atinse.** Planul le lista, dar ele fac
deja ce cerea lista de modificări: `refine_boundaries` MUTĂ ambele margini — snap pe propoziție,
lead-in, payoff, reacție, răspuns, trim de coadă, drop de orfan, pad de release — iar
`story_evidence.remeasure` recalculează deja context/payoff/reaction coverage după fiecare mutare. Ce
lipsea nu era o mutare în plus, ci un **verdict**: nimic nu putea spune cu voce tare „clipul ăsta se
oprește în mijlocul unui cuvânt".

**Gaura pe care o închide, aceeași ca la fiecare batch de la R0 încoace.**
`extract_features` calculează `ends_on_sentence = 1.0 if text.endswith(".!?…")`. Pe un transcript
FĂRĂ punctuație asta e 0.0 pentru fiecare clip tăiat vreodată — iar 0.0 acolo nu înseamnă „se termină
la mijlocul propoziției", înseamnă că nimeni nu a putut ști. Clipper-ul transcrie cu
`keep_punctuation=True`, dar `transcriber._clean_text` scoate punctuația implicit și un transcript mai
vechi poate să nu o aibă. Acum verificările de propoziție ies `unavailable`, iar `eligible` e `None`:
board-ul nu are voie să piardă un moment din cauza felului în care a fost transcris. O tăietură în
mijlocul unui cuvânt rămâne măsurabilă oricum.

**Liste închise:** nouă defecte, dintre care patru blocante — `start_inside_word`, `end_inside_word`,
`end_mid_sentence`, `orphan_tail`. `start_mid_sentence` NU e blocant: un hook deschide legitim la
mijlocul unei propoziții, iar gate-ul cere un OM să spună dacă începutul e acceptabil.
`clipped_release` nu e blocant: se repară prin padding, ceea ce `_keep_release` face deja, nu prin
refuzarea momentului.

**O singură reparație, bounded (§3.5):** extinderea finalului până la următoarea graniță de
propoziție, mărginită de durata maximă, de mediu și de prima fereastră care începe DUPĂ aceasta —
singura vecină cu care o reparație ar putea intra în coliziune nouă. Câmpul de candidați nu e un
timeline și ferestrele se suprapun intenționat, deci o suprapunere care există deja nu e o margine;
crearea uneia care nu exista, da. Ce rămâne după reparație se **măsoară pe fereastra reparată**, nu se
presupune: o extindere care ajunge la o graniță de propoziție poate ateriza tot pe un orfan.

**Nimic nu se aplică.** `eligible` intră în `candidates.json` și nu decide nimic — planul însuși
condiționează asta de trecerea corpusului, fiindcă o regulă care scoate momente în tăcere trebuie
măsurată înainte să fie crezută, nu după.

**Constantele nu sunt încă recalibrate, și scriptul spune asta.** `TAIL_PAD_S = 0.40` e moștenit de la
o singură sursă (median 0,16s, p90 0,40s). Auditul tipărește distribuția reală a cozii pe corpus,
adică exact numerele din care s-ar re-deriva.

**Corecții din review (Codex, două runde):**

- **Un defect MĂSURAT nu mai e înghițit de o măsurătoare indisponibilă.** Un cuvânt trunchiat nu are
  nevoie nici de punctuație, nici de o limbă; `eligible=None` e corect doar când nimic cunoscut nu
  respinge fereastra și verdictul depinde de semnalul care lipsește. Ordinea e acum: blocant măsurat
  → `False`; altfel necunoscut → `None`; altfel `True`.
- **Deschiderea se verifică STRUCTURAL, nu cu o toleranță.** O comparație de timp cu începutul
  propoziției numea „mid-sentence" orice fereastră care se deschidea în tăcerea dinaintea vorbirii —
  adică exact ce produce `refine_boundaries` de fiecare dată când adaugă lead-in sau paddează un
  început. Întrebarea e dacă primul cuvânt dinăuntru ÎNCEPE o propoziție.
- **Două axe, nu un verdict comprimat.** `blocking` e despre MOMENT (merită păstrat?), `technical` e
  despre FIȘIER (`clipped_release`, `dead_tail` — un pad la randare le repară). Dacă un defect tehnic
  are voie pe board e întrebarea preflight-ului R7, nu a acestui batch.
- **„Nimic nu e greșit" și „ceva e greșit și mutarea asta nu-l atinge" erau același string.** Acum
  `nothing_to_repair` vs `defect_is_not_an_unfinished_end`.
- **Vecinul care începe exact la final** nu mai e omis (`>=`, nu `>`), iar limita e recunoscută ca
  fiind conservatoare, nu corectă: câmpul nu e un timeline, iar candidatul următor e adesea altă
  variantă a aceluiași moment. Refuzurile se numără (`would_overlap_the_next_window`), ca prețul
  limitei să fie o cifră, nu o politică invizibilă.
- **Auditul valida schema prea slab.** `view.get("defects") or []` pe un record care nu a purtat
  niciodată cheia se citea ca o fereastră curată, iar un corpus de goluri se agrega într-o trecere.
  Acum fiecare cheie promisă e verificată înainte de orice numărătoare.

**Gate:** măsurabil de acum, prin `--recompute`, care încarcă transcriptul din DB și rulează ACEEAȘI
funcție canonică peste ferestrele stocate — fără re-score, fără să atingă vreun scor sau board.
**Prima măsurătoare, pe tot corpusul — 6.762 de candidați din 13 proiecte cu candidați, toți cu
verdict: 261 de ferestre se termină în interiorul unui cuvânt și ZERO încep așa.** Gate-ul cere zero.

*(Prima cifră raportată aici a fost 171, și era greșită. O obținusem însumând liniile unui raport pe
care îl trecusem printr-un `tail` — adică agregasem o vedere trunchiată și publicasem suma ca cifră
de corpus. Exact clasa de eroare împotriva căreia e scris tot planul. Codex a prins-o recalculând
independent; 261 e cifra verificată, din JSON-ul complet.)*
Asimetria 261/0 e chiar demonstrația cauzei: `refine_boundaries` aplică `_snap` pe început și
niciodată pe finalul final. `_reaction_end` întoarce `min(w1, limit)`, deci când reacția lovește
plafonul `REACTION_MAX_S` la mijlocul unui cuvânt tăietura cade acolo; `_fit` snapează doar la
depășirea maximului, iar `_keep_release` nu ajută fiindcă `end - inside_end > TAIL_PAD_S` e fals
acolo. Cele patru exemple verificate manual poartă toate `reaction_kept`.

Restul, ca bază de comparație pentru o rulare viitoare, din JSON-ul complet: `end_mid_sentence`
3.681, `clipped_release` 1.855, `start_mid_sentence` 573, `start_on_continuation` 432,
`end_inside_word` 261, `orphan_tail` 176, `required_context_outside` 76, `dead_tail` 55. Reparații
refuzate: `nothing_to_repair` 2.054, `would_overlap_the_next_window` 1.283,
`defect_is_not_an_unfinished_end` 1.006, `would_exceed_max_duration` 298. Coada: median 0,40s,
p90 0,40s pe 6.742 de ferestre măsurate.

**`slice4h00test` NU e un fir de bug actual.** Artefactul lui e din 14 august, iar `_keep_release` a
intrat pe 15 în `4a136de` — de acolo vin cele 880 de cozi zero ale lui. Pe ferestrele lui vechi,
implementarea de azi ar adăuga padding la 748 din 880; 132 n-au spațiu. Proiectul demonstrează lipsa
de proveniență/versionare a artefactelor, nu că funcția curentă e ocolită.

**Reparată în R5a**, după review: proprietarul defectului e boundary refinement, nu rendererul —
ferestrele candidate sunt greșite ÎNAINTE de scoring.

### Batch R5a — finalul aterizează în afara unui cuvânt — 30 aug 2026

**Fișiere:** `candidate_boundaries.py` (`_fit`), nou `scripts/measure_boundary_snap.py`, test nou
`test_clipper_boundary_snap.py`.

**O singură modificare:** `_fit` snapează acum și finalul, ultimul lucru pe care îl face, mărginit de
maxim și de mediu, cu pull-back refuzat când ar coborî sub minim. Docstring-ul funcției promitea
„staying off words" de la început; era adevărat doar pentru început.

**Măsurat pe corpus, înainte și după (`scripts/measure_boundary_snap.py`):**

| | |
|---|---:|
| ferestre trunchiate, înainte | **261** |
| ferestre trunchiate, după | **0** |
| refuzate pentru minim / maxim / mediu | 0 / 0 / 0 |
| împinse înainte / trase înapoi | 260 / 1 |
| deplasare mediană | 0,10s |
| deplasare p90 | 0,66s |
| peste 0,15s | 120 din 261 |
| peste 1s | 13 |
| peste 3s | 3 |

**Deplasarea NU e mică**, contrar a ce presupusesem („sub 0,15s"): 120 din 261 o depășesc. Trei
cazuri depășesc 3s, pe transcripte care conțin tokenuri de 5,72s, 5,14s și 3,54s.

**Nu am pus gardă pe durata cuvântului, și motivul e măsurat:** p99 al duratelor de token marchează
19 din cele 261 de mutări, p99.9 marchează exact cele trei extreme — deci pragul s-ar alege după ce
vezi ce răspuns dă, ceea ce e calibrare pe gate. Nici audio-ul nu tranșează: toate trei extremele cad
în intervale clasificate drept vorbire, cu RMS-ul activ, deci afirmația mea inițială că „adaugă
secunde de material probabil tăcut" **era prea tare — nimeni nu a arătat asta**. Nici probabilitatea
Whisper nu separă: 0,292 pentru un token de 5,14s, dar 0,886 pentru unul de 3,54s și 0,997 pentru
unul de 1,56s. O regulă statistică poate spune că timestamp-ul e ciudat; nu poate spune unde se
termină cuvântul. Dacă un review demonstrează defectul, soluția aparține alinierii transcriptului sau
unui detector audio de graniță, nu unei limite p99 în `_fit`.

Scriptul numește acum cele mai mari mutări per proiect, cu tokenul, durata lui, percentila în
distribuția transcriptului însuși și probabilitatea Whisper.

**Ce NU e măsurat:** ce fac cele 261 de mutări scorurilor de graniță, dedupe-ului, shortlist-ului și
board-ului. Nu se poate ști fără un re-score pe o **clonă** — nu pe proiectele-baseline, și nu doar
pe cele patru piloturi: cele trei mutări extreme sunt în `39c89ae2e16e` și `43a509687a33`, deci
clonele acelor două surse trebuie incluse, altfel gate-ul nu testează riscul tocmai descoperit. Baseline-ul
R0 nu se pierde: rămâne dovada despre cele 58 de fișiere vechi, iar rezultatele noi se ștampilează cu
versiunea nouă de boundary și nu se amestecă în aceeași comparație. Review-ul orb S8 vine după.

`--recompute` măsoară REGULA, nu pipeline-ul: un verde acolo lasă integrarea end-to-end
nedemonstrată, iar raportul spune pe fiecare rând `recorded` sau `recomputed`.

### Batch R6 — captions și source hygiene — DETECTORUL EXISTĂ, 30 aug 2026

**Prima parte livrată:** `source_captions.py` + `scripts/detect_source_captions.py` + teste.
Răspunde la `present | absent | unknown`, **4 din 4 împotriva etichetelor umane** de pe piloturi.
Nimic nu e aplicat încă — detectorul nu stinge al doilea strat de captions.

**Două abordări au eșuat înainte**, ambele euristici de luminozitate pe benzi orizontale, iar a doua
a pus un NEGATIV peste pozitiv. Sunt scrise integral în
[`../clipper-caption-detection.md`](../clipper-caption-detection.md), ca următorul agent să nu le
rerulze. Ce funcționează e un detector de text adevărat: greutățile CRAFT ale lui `easyocr` sunt deja
în cache local, deci merge offline, 5 secunde pe sursă.

**Discriminatorul, măsurat nu dedus:** trei proprietăți împreună — o singură bandă, persistentă, și
**LATĂ**. Ultima e cea pe care nimic altceva nu o dă: watermark-ul lui `pilot6b38` e în 13 din 14
cadre, deci MAI persistent decât adevăratul pozitiv, și are 0,079 din lățimea cadrului față de 0,45.
Fiecare negativ pică pe altă axă, deci ambii discriminatori fac muncă.

**`present` e răspunsul scump**, nu `absent` — o versiune anterioară a acestei secțiuni avea direcția
inversată. `present` înseamnă că sursa are deja captions, deci **dezactivează** stratul ClipForge;
`absent` îl **păstrează**; `unknown` nu schimbă nimic. Un `present` greșit livrează un clip fără niciun
fel de captions, de aceea e singura stare care cere ambele praguri — și de aceea se poate obține din
dovezile unei singure benzi, în timp ce `absent` cere ca toate benzile să fie clar negative.

**Două corecții din review:** judecarea doar a benzii cu cele mai multe cadre lăsa un watermark
(14/14, lățime 0,08) să mascheze pista reală de subtitrare (9/14, lățime 0,45); și un detector care
aruncă pe fiecare cadru ieșea `absent`, fiindcă benzi goale sunt indistinctibile de o sursă fără text.
Numitorul e acum ce s-a **analizat**, nu ce s-a eșantionat.

**Ce NU e demonstrat:** pragurile au fost alese cu răspunsul la vedere, pe patru surse. Nu e o
calibrare, iar `calibrated: false` călătorește cu fiecare verdict.

**A doua parte livrată, 30 aug 2026:** `caption_placement.py` (+ `caption_placement_vocab.py`) și
`caption_choice.py`. Prima spune CE acoperă caption-ul ars, per shot, în pixeli de output; a doua
spune DE CE stă acolo — poziția propusă, alternativele respinse, motivul. Ambele înregistrează și nu
mișcă niciun cadru.

**Trei axe, ținute separat, și asta e tot batch-ul:** `conflicts` (ce s-a măsurat), `unavailable`
(ce nu a furnizat nimeni), `refused` (ce a furnizat cineva greșit). Împreunate, un `y_pct` NaN
raporta „nu a setat nimeni niciun caption", iar un shot a cărui dovadă era numărul 3 raporta toate
cele trei semnale drept simplu lipsă — cu un `worst` calculat peste el.

**`share` e o REUNIUNE, nu un maxim,** peste semnale și peste cutii: o față peste 39,6% din caption
și text peste alte 39,6% înseamnă 79,2% ilizibil, iar max-de-cutie-apoi-max-de-semnal numea mai rău
un shot cu o singură față la 60,4%. Și e un PLAFON INFERIOR când nu s-a măsurat tot: `share_complete`
călătorește alături, iar cu nimic măsurat `share` e `None`, nu 0,0.

**Ce NU se măsoară, și de aceea nu se afirmă:** banda de letterbox e scoasă din `share` fiindcă nu
ocluzionează sursa — dar asta nu e o afirmație că textul de acolo e lizibil. `render_v3_letterbox`
umple banda cu o copie BLURATĂ a cadrului, nu cu negru, iar §R6 cere contrast măsurat înainte ca
ceva să stea acolo. Punctul rămâne deschis.

**Constatarea pe care `caption_choice` o cară fără s-o repare:** un `safe_zone` cu NaN trece de
`_norm_rect` — orice comparație cu NaN e falsă — și apoi se suprapune peste caseta de caption la
FIECARE poziție din scanare. O singură intrare malformată șterge căutarea de bandă și MUTĂ
caption-ul livrat: măsurat 0,3092 → 0,4642 pe un rect de facecam plus un rect NaN. E comportament
viu în `captions` și acest batch nu mută captions livrate, deci numărătoarea călătorește în raport.

**Browser chrome — prima măsurătoare a pus întrebarea greșit, și concluzia ei era falsă.**

Am rulat easyocr cu recognizer pornit peste 20 de proxy-uri × 8 cadre uniforme, căutând URL-uri,
domenii și placeholder-e de căutare: 0 potriviri. Numitorul arăta că nu e un detector mort — 453 de
tokeni citiți, între 0 și 62 pe sursă, aproape toți numere de HUD și name tag-uri — și am scris de
aici că **corpusul nu conține niciun pozitiv**.

**Asta contrazice o dovadă pe care o aveam deja.** Review-ul uman v2 listează „browser UI" printre
defectele rendererului v3, alături de cele 116 tăieturi invizibile și de captions-urile duble. Deci
pozitivul EXISTĂ; opt instantanee uniforme dintr-o sursă de ore nu l-au atins. Un eșantion care ratează
lucrul căutat nu e o dovadă că lucrul lipsește — exact confuzia pe care restul acestui plan o combate,
făcută de mine, în paragraful care o enunță.

**Ce se măsoară în locul ei:** aceleași detectoare peste EXPORTURILE randate, un cadru pe secundă, nu
peste proxy la opt momente arbitrare. Acolo a văzut omul defectul, deci acolo se pune întrebarea. Se
numără două familii separat — un URL sau un domeniu, care e dovadă în sine, și o etichetă de control
(`search`, `share`, `save`, `subscribe`), care e un cuvânt obișnuit și e dovadă doar alături de
altele.

**Ce rămâne adevărat din prima măsurătoare, și e o avertizare:** recognizer-ul citește prost exact
conținutul care contează. Două surse au dat 0 tokeni, iar patru au dat tokeni fără niciunul peste
confidence 0,5 — printre ele `pilotf81b`, singura sursă despre care ȘTIM că are captions arse. Deci
recall-ul unui warning bazat pe recunoaștere de text e mic și nedemonstrat, iar `not_detected` nu
poate însemna niciodată „curat".

**Rulate pe corpusul real, nu pe fixture-uri** (`scripts/audit_caption_placement.py`, 101 sidecare):

```
sidecars found                        101
  refused                               0
of those, with a caption position      99
  without one (unavailable, not a fail)  2
  today's rule would not produce it      8
    ...and it sits on a keep-out         1
of those, `fit` + known geometry       27
  caption lands on the letterbox       27
```

**27 din 27.** Fiecare clip cu shot `fit` și geometrie cunoscută are caption-ul pe banda de
letterbox. Linia §R6 despre contrast nu e un caz-limită de acoperit cândva; e starea fiecărui export
letterboxat deja livrat.

**8 din 99 poziții pe care regula de azi nu le-ar produce.** Nu corupție — exporturile sunt
anterioare scanării de bandă care a înlocuit cele șase nudge-uri de ±4%. Auditul verifică poziția pe
care a CERUT-O stilul clipului, nu oricare dintre cele patru presete: verificarea laxă declara șase
clipuri din `2d3375ee3420` explicate fiindcă stau la 0,51, valoarea `center`, sub un stil care spune
`bottom`. Unul singur dintre cele opt (`0c9685df852b/205a6ec12b00`, y=0,75) stă peste 7,8% dintr-un
keep-out de față, pe care regula actuală îl evită.

**CONTRASTUL E ARITMETIC, nu un eșantion de cadre** (`caption_contrast.py`,
`scripts/audit_caption_contrast.py`). Un glif cu contur se separă de ORICE fundal prin una dintre
cele două culori ale lui — conturul pe fundal deschis, umplutura pe fundal întunecat — deci există o
podea, iar niciun cadru nu poate fi sub ea. Și podeaua are FORMĂ ÎNCHISĂ: e rădăcina pătrată a
contrastului dintre cele două culori proprii ale glifului, `sqrt(21) = 4,58` pentru alb în negru, la
luminanță 0,179. Prima versiune mătura 256 de griuri, cu un comentariu care susținea că forma închisă
ține doar când culorile sunt extremele; ține pentru orice pereche, iar măturarea era aproximarea — și
greșea în direcția care flatează, fiindcă un pixel de fundal e 8 biți pe CANAL, dar luminanța unui
pixel colorat e o sumă ponderată a trei canale și cade oriunde între.

**Și descoperirea nu e pe letterbox, e în paletă.** Toate umpluturile trec (4,39–4,58). Culoarea de
HIGHLIGHT, singurul cuvânt pe care îl pictează animația karaoke, nu:

```
Classic White / Boxed White  4,58     Karaoke Yellow  4,07
Bold Impact                  3,87     Clean Minimal   3,44
Viral Gradient               2,72     Neon Pop        2,33
```

`Neon Pop` și `Viral Gradient` nu pot GARANTA 3,0:1 pentru cuvântul evidențiat. **Ambele sunt
presete livrabile azi.**

**Fraza asta are o citire precisă și una laxă, iar cea laxă e falsă.** O podea de 2,33 înseamnă că
EXISTĂ o luminanță de fundal la care separarea coboară acolo — nu că textul nu atinge niciodată 3:1.
Pe majoritatea fundalurilor reale îl atinge lejer. Forma onestă a oricărui verdict de aici e
„separarea minimă pe care o poate garanta paleta, presupunând fundal uniform, umplutură opacă și
contur vizibil", niciodată „contrastul randat trece".

Și fiecare număr de acolo presupune că sunt DOUĂ culori pe ecran: un `outline_width` zero lasă o
umplutură goală, al cărei cel mai rău fundal e propria ei culoare, la 1,0. Lipsa sau grosimea zero
sunt refuzuri. La fel o culoare cu canal alfa — ASS scrie `00` pentru opac, CSS scrie `FF`, iar
codebase-ul le are pe amândouă — deci se refuză, nu se presupune. Repararea înseamnă schimbarea unei culori,
adică schimbarea a ce se livrează, deci nu aparține unui batch în umbră — e o decizie pentru om.

Ce NU e: o podea de LUMINANȚĂ, nu o dovadă de lizibilitate (WCAG ignoră nuanța); presupune fundal
uniform sub fiecare glif, ceea ce blur-ul produce și un `crop` peste sursă detaliată nu; iar pragurile
sunt WCAG 2.1 AA, scrise pentru pagini web, deci `calibrated: false`.

**MAPPERUL EXISTĂ, iar semnalul de față are pentru prima dată cifre** (`evidence_map.py`).
`caption_placement` a spus dintotdeauna că APELANTUL mapează dovezile, și nimeni nu o făcea, deci
toate trei semnalele de ocluziune erau `unavailable` de când a fost scris.

**Lanțul e citit din renderer, nu presupus:** proxy → sursă cu scări separate pe X și Y; apoi
`y += canvas_offset`, fiindcă `dynamic_render` face PAD ÎNAINTE DE CROP și comentariul lui explică de
ce — `scale` își fixează dimensiunea la configurare, deci letterbox-ul din față e ce face fiecare
crop 9:16, iar un mapper care taie înainte de padding descrie un renderer abandonat; apoi fereastra
de crop a shot-ului; apoi o singură scalare la 1080×1920.

**Refuză în patru locuri:** un shot cu timeline de dimensiune multi-punct (dreptunghiul e fereastra
livrată doar cât timp nu se mișcă); un shot care nu poate fi citit deloc, refuz SEPARAT de precedentul;
dimensiuni proxy lipsă (`dynamic_edit` cade pe `or src_w`, adică scară 1 unde realitatea e 5,3); și o
cutie care nu e patru numere finite. O cutie care ratează cropul e `off_frame` — nici refuz, nici zero.

**Cifrele, pe cele 101 sidecare:**

```
shot-uri                                2126
  fără niciun eșantion în fereastra lor   964
  cu fețe mapate în output               1162
  cu MAI MULT de un eșantion               161
cutii de față care ratează cropul         459
clipuri cu caption peste o față detectată  26
clipuri cu cazul cel mai rău STABILIT       0
```

**964 e titlul, nu 26.** Detectorul eșantionează la ~2 secunde iar un shot are tipic 1–4, deci aproape
jumătate din shot-uri nu conțin niciun eșantion. Alea sunt `unavailable` — `None`, niciodată listă
goală, fiindcă o listă goală spune „ne-am uitat și nu era nicio față".

**Deci 26 e un plafon inferior pe toate axele.** Cele 964 de shot-uri neeșantionate pot conține
fiecare o față peste caption, iar semnalele de UI și de text-sursă nu au nicio detecție per shot —
deci `share_complete` e fals pentru fiecare shot din corpus și ZERO clipuri au cazul cel mai rău
stabilit. Raportul tipărește asta lângă cifră, fiindcă altfel „26 de clipuri" se citește „și celelalte
73 sunt curate".

Câteva dintre cele 26 au `worst = 1,0`: banda de caption complet acoperită de o față.

**Ce a mai rămas din listă:** warning-urile de browser chrome (blocate pe rata de fals-pozitiv, care
se măsoară acum), detecția per-shot pentru UI și pentru textul sursei — fără ele două din trei
semnale rămân `unavailable` și niciun caz nu poate fi stabilit — și cablarea propriu-zisă în sidecar.
`panels_to_keep_out` NU e un mapper generic: sare deliberat peste shot-urile de față.

### Batch R6 — lista originală

**Scop:** captions ajută clipul și nu dublează sau ascund informația.

**Fișiere:** `captions.py`, `dynamic_window.py`, `clipper_render_plan.py`, review-ul local, teste.

**Modificări:**

- detectează `source_captions = present | absent | unknown` pe eșantioane relevante;
- `present` dezactivează implicit al doilea strat, dar păstrează override manual;
- poziția captions evită fața creatorului, UI-ul și conținutul informativ al slide-urilor;
- pe `fit`, banda blurată poate găzdui captions numai dacă trece contrastul și nu intră peste sursă;
- browser chrome, controale și bare de căutare devin warnings explicite de publicare;
- lipsa detecției nu este interpretată drept „nu există captions”.

**Gate:** zero captions duble pe cele 15 go ghost; zero captions peste fețele și diagramele din
setul de regresie; text lizibil pe toate compozițiile.

### Batch R7 — preflight de publicare și corecția bounded

**Scop:** niciun clip tehnic defect nu intră automat pe board/export.

**Fișiere:** `review.py`, `review_vision.py`, `clipper_render_jobs.py`, `edit_quality.py`.

**Verificări înainte de publicare:**

- geometrie și durată;
- echivalența tăieturilor și ritmul profilului;
- subiect/creator prezent când profilul îl cere;
- cadru util în `fit` și lipsa browser chrome dominant;
- captions duplicate, overlap și contrast;
- boundary complet;
- provenance completă.

Vision rămâne opțional și failure-safe. Regulile deterministe decid ce pot demonstra; vision poate
ridica un warning sau propune singura corecție permisă, nu poate declara singur clipul bun.

**Gate:** maximum o corecție; rerun-ul produce rezultat stabil; un eșec al modelului nu blochează
jobul, dar nici nu șterge warning-ul.

### Batch R8 — reconstrucția secvenței livrate după trim

**Scop:** un montaj din care s-au tăiat secunde să poată fi măsurat, nu doar refuzat.

**De ce este un batch separat.** R0 a stabilit că `drop_spans` schimbă montajul, nu doar ceasul: un
shot care cade integral într-un interval eliminat nu există în videoclipul livrat, iar unul tăiat prin
mijloc devine două bucăți cu un salt între ele. Până există o regulă pentru ce ÎNSEAMNĂ o tăietură în
mijlocul unui shot, evaluatorul refuză jumătatea bazată pe shot-uri și marchează
`trimmed_edit_not_reconstructed`. Handover-ul a atribuit o vreme această reconstrucție lui R1; nu îi
aparține — R1 este despre echivalență, nu despre trim.

**Fișiere:** `edit_quality.py`, `dead_air.py`, testele lor.

**Modificări:**

- secvența livrată se construiește din intersecția shot-urilor cu intervalele păstrate;
- un shot rămas fără durată dispare; unul tăiat prin mijloc produce bucăți, iar saltul dintre ele este
  declarat, nu inventat ca tăietură normală;
- metricile bazate pe shot-uri redevin disponibile pe exporturile cu trim.

**Gate:** un export cu `trim_silence` produce aceleași metrici ca varianta netăiată acolo unde trimul
nu atinge nimic, iar acolo unde atinge, cifrele se explică prin intervalele eliminate.

### Batch S7 — închiderea infrastructurii reasoning v2

Acest batch continuă Batch 7 din planul reasoning, nu îl rescrie:

- envelope și fingerprint pentru toate artefactele;
- invalidare țintită și recovery per chunk neutilizabil;
- resolver determinist pentru grounding, apoi remăsurare pe ancorele cached;
- `anchor_id` stabil propagat la variante;
- dedupe nu mai citește o scară blendată ambiguă;
- aceeași identitate de rulare pentru selection și render trace.

**Gate:** o cerere de chunk neparsabilă nu șterge tăcut 57% dintr-un interviu; rerun-ul declară
partea nondeterministă; niciun cache incompatibil nu este reutilizat.

### Batch S8 — evaluarea selecției, separată de randare

**Scop:** demonstrează că `story_v2` alege mai bine decât legacy.

**Metodă:**

- minimum 10 surse și 150 de momente distincte, multi-gen și multi-limbă;
- aceeași randare neutră și tehnic validă pentru ambele board-uri;
- review orb pentru `worthwhile`, `self_contained`, hook, payoff, boundary și diversitate;
- macro-agregare per sursă; nu se lasă o sursă lungă să domine suma;
- review-ul tehnic este alt formular și alt event de feedback;
- ranker-ul learned rămâne oprit până are ambele clase și câștig LOPO pe proiecte nevăzute.

**Gate:** două evaluări consecutive peste legacy; nicio regresie mare ascunsă într-un tip de
conținut; intervalul de încredere și cazurile de dezacord sunt publicate în artefact, nu doar media.

### Batch P — activarea în aplicație

Activarea se face în ordinea următoare:

1. `content_aware_shadow` implicit, board legacy și export legacy;
2. renderer content-aware implicit, reasoning încă în shadow;
3. după gate-ul S8, `story_v2` selectabil explicit;
4. după încă o cohortă nouă reușită, `story_v2` devine server default;
5. `legacy` și `legacy_dynamic` rămân rollback pentru minimum o versiune stabilă.

UI-ul arată separat:

- reasoning mode efectiv;
- edit mode și profil efectiv;
- render version;
- warnings tehnice;
- dacă rezultatul este shadow, fallback sau producție.

Nu există fallback tăcut de la o valoare afișată la alta.

## 7. Gate-ul final de produs

Motorul poate fi numit production-ready numai dacă toate sunt adevărate:

### Randare

- 0 cadre întinse și 0 exporturi trunchiate;
- 0 tăieturi exact echivalente după compoziție;
- 0 captions duble în corpusul cunoscut;
- 0 cuvinte/foneme tăiate;
- defect rate tehnic uman ≤10% macro și fără sursă peste 20%;
- ritmul fiecărui profil se află în guardrail sau are warning justificat;
- creatorul nu este înlocuit de conținutul reacționat;
- fiecare sidecar are profil, regimuri, motive și render version.

### Reasoning și selecție

- minimum 10 surse și 150 de momente etichetate;
- `story_v2` depășește legacy în două evaluări consecutive;
- self-contained și boundary trec pragurile umane;
- shortlist-ul are coverage și diversitate, fără variante redundante;
- niciun chunk neutilizabil nu este raportat drept rulare completă;
- learned ranker este fie demonstrat pe proiecte nevăzute, fie rămâne explicit dormant.

### Fiabilitate

- suita Clipper completă verde; TikTok se raportează separat și nu este ascuns în numărătoare;
- retry, cancel, restart și cache invalidation testate;
- preview-ul și exportul folosesc aceeași compoziție;
- rollback-ul schimbă un singur mod declarat și este testat end-to-end;
- nicio activare nu rescrie proiectele sau feedback-ul vechi.

## 8. Matrice minimă de testare

| nivel | obligație |
|---|---|
| unit | profil resolver, regimuri, boundary completion, echivalență vizuală, captions policy |
| contract | UI/API/worker pentru `edit_mode` și `reasoning_mode`, proiecte vechi, valori invalide |
| integration | plan → sendcmd → MP4, crop/fit/crop, fallback, preflight și o corecție |
| regression | cele 58 de exporturi și cadrele nominalizate în audit |
| offline | shot rate, dead cuts, boundary metrics, Precision@K, NDCG și coverage |
| human | două review-uri separate: selecție și randare |
| reliability | provider absent, JSON invalid, restart, cancel, retry, cache incompatibil |

Un test care verifică doar că MP4-ul există sau că JSON-ul este valid nu este test de calitate.

## 9. Ce nu trebuie făcut

- Nu se adaugă profile prin copierea întregului planner de șase ori.
- Nu se scrie un al doilea content classifier; se rezolvă profile din verdictul existent.
- Nu se calibrează toate tipurile pe cele patru surse pilot.
- Nu se activează `story_v2` pentru că v2 a câștigat pe 58 de randări tehnic contaminate.
- Nu se folosește `overall` ca substitut pentru heuristic, judge, eligibility sau renderability.
- Nu se antrenează ranker-ul din feedback tehnic.
- Nu se introduce active-speaker prin euristici neverificate.
- Nu se ascunde un warning prin fallback.
- Nu se rescrie rendererul FFmpeg care tocmai a trecut gate-ul geometric; se schimbă plannerul și
  contractul shot-urilor, iar rendererul rămâne consumatorul planului.
- Nu se depășesc 500 de linii. `dynamic_edit.py` are deja 478 și `dynamic_cameras.py` 468: logica
  nouă intră în modulele cu responsabilități de mai sus, nu se îndeasă în ele.

## 10. Ordinea obligatorie

```text
R0 evaluator
 → R1 dedupe după geometria livrată
 → R2 profile în shadow
 → R3a subiectul ancorat pe creator
 → R3b regimuri și topologia shot-urilor
 → R4 ritm
 → R5 boundaries
 → R6 captions
 → R7 preflight
 → R8 reconstrucția montajului după trim
 → S7 reproducibilitate reasoning
 → S8 golden review
 → P activare graduală
```

R1–R7 pot îmbunătăți produsul fără să schimbe board-ul. S7–S8 pot demonstra selecția fără să fie
contaminate de renderer. Activarea finală depinde de ambele ramuri.

## 11. Instrucțiune pentru următorul agent

Acesta este documentul de pornire pentru următoarea implementare. Agentul trebuie să aleagă primul
batch neînchis, să citească numai fișierele acelui batch și să raporteze gate-ul înainte de a trece
mai departe. Dacă o măsurătoare contrazice planul, se păstrează măsurătoarea și se actualizează
planul; nu se ajustează testul ca să confirme ipoteza.
