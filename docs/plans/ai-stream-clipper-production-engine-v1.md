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

### Batch R4 — ritmul content-aware, fără alternare forțată

**Scop:** camera se schimbă pentru că informația vizuală o cere, nu pentru că a trecut 1,8s.

**Fișiere:** `dynamic_cuts.py`, `dynamic_edit.py`, `edit_profiles.py`, teste.

**Modificări:**

- elimină regula globală „shot-urile adiacente trebuie să difere” și alternarea obligatorie după
  două shot-uri;
- un cut candidat trebuie să aibă atât o graniță temporală acceptabilă, cât și un motiv vizual sau
  narativ declarat;
- `talking_head` și `conversation` pot ține cadrul peste mai multe propoziții;
- `action` poate accelera numai în intervale cu acțiune măsurată;
- `visual_evidence` rămâne stabil cât timp textul/diagrama trebuie citită;
- schimbarea de profil în interiorul clipului nu produce singură o tăietură;
- guardrail-urile din §4 sunt raportate, nu ascunse prin clamp.

**Teste:** același talking-head liniștit, aceeași scenă de interviu, burst de gaming, slide de 8s,
cut de scenă IRL, semnale absente.

**Gate:** niciun profil non-action nu depășește guardrail-ul fără warning; minimum shot respectat;
auditul uman nu mai descrie montajul drept agitat; numărul de tăieturi scade fără să apară cadre
moarte lungi.

### Batch R5 — completion check și boundary repair determinist

**Scop:** momentul ales devine o fereastră completă, nu o propoziție tăiată la scor maxim.

**Fișiere:** nou `boundary_completion.py`, `candidate_boundaries.py`, `story_evidence.py`,
`clipper_finalize.py`, teste.

**Modificări:**

- verifică începutul și finalul pe cuvinte, punctuație, pauze și dovezile story;
- adaugă lead-in și tail controlate, fără să taie foneme: valorile se calibrează, nu se hardcodează
  din PRP-ul vechi;
- dacă finalul este incomplet, extinde o singură dată până la următoarea graniță sigură, bounded de
  durata maximă și de overlap;
- după schimbare, recalculează context/payoff/reaction coverage și toate scorurile dependente;
- dacă nu poate obține o fereastră completă fără să strice momentul, marchează `ineligible` cu motiv;
- `eligibility` începe să influențeze board-ul numai după ce acest validator trece corpusul;
- un LLM poate propune o completare, dar verificarea și timestamp-ul final sunt deterministe.

**Gate:** zero cuvinte trunchiate; ≥95% începuturi și finaluri acceptate la review uman; cele 22 de
finaluri cu ≤50ms scad fără a introduce tăceri lungi; context coverage rămâne peste gate-ul existent.

### Batch R6 — captions și source hygiene

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
