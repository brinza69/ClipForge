# Plan de consolidare — AI Stream Clipper Reasoning v2

- **Status:** plan consolidat, verificat în cod și în artefactele locale. Nicio schimbare de cod încă.
- **Data auditului inițial:** 2026-08-21
- **Data consolidării:** 2026-08-21, după review-ul din
  [`ai-stream-clipper-reasoning-v2-review.md`](ai-stream-clipper-reasoning-v2-review.md)
- **Scop:** motorul de selecție, delimitare, evaluare și învățare al AI Stream Clipper
- **În afara scopului:** TikTok, redesign general al aplicației și schimbarea motorului de randare

Ce s-a schimbat față de prima versiune este listat integral în §13.

## 1. Verdict executiv

Motorul nu trebuie rescris și nici înlocuit cu un model mai mare. Structura actuală este
recuperabilă. Prioritatea este să existe o singură reprezentare a dovezii narative, apoi să fie
reparate scările de scor, coverage-ul, shortlist-ul, observabilitatea și feedback-ul.

Constatările sunt împărțite pe tot parcursul documentului în trei categorii care nu au voie să fie
amestecate:

- **Confirmat în cod** — locul exact există și a fost citit. Nu necesită altă dovadă.
- **Impact măsurat** — efectul a fost cuantificat pe artefacte reale, cu sursa numită.
- **Ipoteză de validat** — plauzibil, dar nemăsurat. Nu se prezintă niciodată ca efect demonstrat.

Șase concluzii structurale:

1. Candidații din afara shortlist-ului de 80 nu primesc scor zero. Ei își păstrează scorul euristic,
   în timp ce candidații evaluați primesc o altă scară. Efectul real este compararea unor scoruri
   incompatibile, nu penalizarea celor neevaluați.
2. Nu este sigur să facem deduplicare finală înainte de judge. Soluția este gruparea variantelor
   aceluiași moment, evaluarea reprezentanților și alegerea variantei finale după evaluare.
3. Reasoning-ul story nu este doar dezactivat implicit: setările normale ale proiectului elimină
   cheile prin care ar trebui activat. Funcția nu poate fi pornită din fluxul normal al aplicației.
4. Motorul reason-ează despre un payoff semantic, dar delimitarea și scoring-ul folosesc un alt
   payoff, dedus mecanic. Acesta este defectul central: alegerea, tăierea și scorarea nu lucrează cu
   aceeași dovadă.
5. Împărțirea transcriptului după numărul de caractere reduce recall-ul pe stream-uri lungi și rare
   în dialog.
6. Feedback-ul de export automat devine etichetă pozitivă de antrenare fără nicio decizie umană.
   Ranker-ul poate învăța din propriile decizii.

## 2. Fluxul actual și locurile unde se pierde informația

```text
transcript + signals
  → semantic windows + candidați euristici
  → atoms / promises / threads / episodes
  → ancore story generate de LLM
  → variante story
  → boundary refinement
  → feature extraction + scor euristic / ranker
  → shortlist de maximum 80
  → LLM judge
  → eliminare exportate + dedupe + diversity
  → layout / captions / headline
  → persistare + render + review + feedback
```

Punctele de pierdere:

- ancora story își pierde `payoff_strength`, `confidence` și o parte din proveniență când devine
  variantă;
- refinement-ul schimbă intervalul fără să recalculeze metricile story;
- chunk-urile urmăresc caractere, nu timpul și acoperirea sursei;
- shortlist-ul este dominat de scorul euristic pe care judge-ul ar trebui tocmai să-l corecteze;
- judge-ul primește textul clipului fără marcaje pentru context, payoff, reacție și provenance;
- scorul final suprascrie sensuri diferite într-un singur câmp `overall`;
- feedback-ul nu separă acțiunea automată de verdictul explicit al utilizatorului;
- nu există un artefact final compact care să explice de ce un moment a câștigat sau a fost eliminat.

## 3. Constatări

### 3.1 Confirmat în cod

| Prioritate | Problemă | Locul exact | Consecință |
|---|---|---|---|
| P0 | Metrici story stale după refinement | `candidate_boundaries.py:341` — `out = dict(cand)` copiază `story` și suprascrie doar start/end; metricile se produc în `candidate_proposals.py:120`, înainte | context debt, hook latency și validarea pot descrie alt clip |
| P0 | Două noțiuni de payoff | `candidates.py:381` — `extract_features` derivă payoff-ul cu `_payoff_time()`; `story.payoff_t` nu este citit niciodată acolo | clipul poate fi tăiat și scorat în jurul altui eveniment |
| P0 | Scoruri incompatibile după judge | `llm_judge.py:190` — `subset = sorted(...)[:80]`; bucla de re-blend din `apply_ranking` iterează `subset`, nu `cands` | candidați evaluați și neevaluați concurează pe scări diferite |
| P0 | Feedback automat tratat ca succes | `clipper_render_jobs.py:303` înregistrează `"exported"` necondiționat; `feedback.py:124` — `if "exported" in decisive: return LABEL_EXPORTED` | ranker-ul învață din auto-export, nu din gustul utilizatorului |
| P1 | Story engine inaccesibil din aplicație | `_default_settings()` (`routers/clipper.py:65`) nu conține `llm_select` sau `reasoning_version`; `_normalise_settings` păstrează doar cheile existente în defaults | ambele chei sunt aruncate silențios la create și patch; activarea per proiect este imposibilă |
| P1 | Recall slab pe surse lungi | `CHUNK_CHARS = 120_000`, `chunk_lines()` nu are noțiune de timp, cotă fixă `per_chunk=10` | ore întregi pot primi aceeași cotă ca zeci de minute |
| P1 | Shortlist nereprezentativ | top 80 după scor euristic înainte de judge | momentele story neobișnuite pot să nu fie evaluate |
| P1 | Packet de judge fără marcaje | `judge_prompt` trimite id, archetype, start și textul clipului, fără să numească unde sunt contextul, payoff-ul, reacția și proveniența | judge-ul trebuie să deducă structura din text; nu poate verifica ce i se cere să verifice |
| P1 | Grounding doar structural | schema validează timpuri și tipuri, nu susținerea în transcript | o explicație plauzibilă poate fi acceptată ca fapt |
| P1 | Fallback LLM prea tăcut | `llm_select.py:340` — `if answer and answer.strip(): return answer`, fără validare JSON | un JSON invalid oprește fallback-ul către următorul provider |
| P1 | Cache fără identitate completă | `_anchor_stamp` = prompt + reasoning + engines + duration | artefacte vechi pot fi refolosite după schimbări semnificative |
| P1 | `segment_types` fără stamp deloc | `clipper_build.py:78` citește artefactul orbește, fără nicio verificare de validitate | tipul de conținut per stretch supraviețuiește oricărei schimbări de configurație |
| P2 | Content type nu rămâne per-candidat | scoring folosește tipul local (`clipper_build.py:279`), persistarea folosește `content_type=profile` (`clipper_build.py:590`) | explicația și layout-ul pot folosi alt profil decât scoring-ul |
| P2 | Review consultativ | `review.py` produce `Finding` și `verdict`; `clipper_render_jobs.py:167` doar le loghează, `REVISE` nu declanșează nicio corecție | probleme reparabile ajung la utilizator |
| P2 | Ranker evaluat pe datele de train | `ranker.py:186`, cu comentariu explicit | activarea poate părea sigură fără generalizare reală |
| P2 | Baseline-ul ranker-ului nu este baseline pur | `_baseline_rank` cade pe `rank_position`, atribuit după judge + dedupe + diversity | comparația learned versus heuristic este metodologic incorectă |

### 3.2 Impact măsurat

Măsurat pe `data/clipper/slice4h00test/analysis/candidates.json` (920 candidați, artefact din
14 august 2026), după judge:

- 80 judecate, 840 nejudecate;
- 60 din cele 80 au primit `llm_score = 0`, deci `overall` a scăzut la aproximativ 0.3× euristic;
- maximul dintre nejudecați: **60.7**;
- consecință: un candidat pe care judge-ul l-a respins explicit pierde în fața unuia pe care nu l-a
  văzut niciodată;
- **12 din primii 20 de pe board nu au trecut deloc prin judge**;
- **1 singur candidat story din 38 a ajuns la judge**.

Comentariul din `apply_ranking` documentează exact acest defect. A fost reparat *în interiorul*
subset-ului și lăsat intact între subset și restul pool-ului.

Măsurat pe același artefact, pentru packet-ul de judge: mediana este de **423 de caractere** și doar
**2 din 80** ating limita `MAX_CLIP_CHARS = 900`. Trunchierea nu este defectul. Defectul este
absența marcajelor, descrisă în §3.1.

### Setul de antrenare al ranker-ului era degenerat

Măsurat pe `data/db/clipforge.db` în ziua în care a fost adăugată coloana `origin`:

- **69** de evenimente în `clip_feedback`: 61 `exported`, 5 `previewed`, 3 editări;
- **zero** evenimente `approved` sau `rejected`. Niciunul;
- `training_rows()` întorcea **43 de rânduri, toate cu eticheta 1.0**;
- `MIN_TRAINING_EXAMPLES = 40`;
- o parte însemnată dintre cele 61 de exporturi au fost făcute de `auto_export` și de un script
  batch, deci de mașină, nu de un om;
- după introducerea `origin` și eliminarea originilor necunoscute: **zero rânduri valide**.

**Formularea corectă contează.** Setul depășea pragul **numeric** de antrenare, nu poarta de
promovare. Nu există `data/clipper/ranker.json`, deci ranker-ul nu fusese niciodată activat, iar
`should_use_learned` ar fi refuzat oricum: cu o singură clasă, `ndcg_at_5` și `baseline_ndcg_at_5`
ies ambele 0.0, iar o egalitate păstrează euristica. Verificat prin simulare pe 43 de rânduri
sintetice cu etichetă unică.

Pericolul real este deci cu un strat mai adânc decât pare: un model degenerat s-ar fi antrenat și
salvat, iar singurul lucru care oprea promovarea era o **egalitate accidentală**, nu o verificare a
diversității etichetelor. Vezi Batch 8.

### 3.3 Ipoteză de validat

Următoarele sunt plauzibile și nemăsurate. Nu se prezintă ca efecte demonstrate și nu justifică
singure o schimbare de ponderi:

- contextul lipsă poate fi compensat de energia audio în unele profile de scoring;
- preflight-ul vizual aduce un beneficiu real față de costul lui;
- feature-urile noi propuse pentru ranker generalizează pe surse nevăzute;
- limita globală de 600.000 de caractere din `transcript_lines` taie coada transcriptului. Nu a fost
  atinsă în corpusul inspectat; rămâne risc latent, nu defect demonstrat.

## 4. Dovezi din artefactele locale, și limitele lor

Auditul a folosit două rulări story existente.

### Sursa scurtă `2d3375ee3420`

- 69 de candidați, dintre care 15 story și opt payoff-uri story distincte;
- toate cele 69 de variante au trecut prin judge, totalul fiind sub limită;
- **opt** variante story aveau context cerut în afara intervalului final;
- toate cele 15 pierdeau `confidence` și `payoff_strength` până în artefactul candidat;
- diferența dintre payoff-ul semantic și cel mecanic avea mediană 7,14 s și maxim 26,5 s.

### Sursa de patru ore `slice4h00test`

- 920 de candidați, 38 story și 20 de payoff-uri story distincte;
- numai 80 au fost evaluați de judge, iar dintre cei 38 story a intrat unul singur;
- **20** de variante story aveau context cerut în afara intervalului final;
- diferența dintre payoff-ul semantic și cel mecanic avea mediană 4,96 s și maxim 61,62 s;
- promptul de atoms a fost împărțit în doar două chunk-uri: ~3h21m și ~38m;
- fiecare chunk a atins limita sa de ancore, semn că limita influențează rezultatul;
- toate payoff-urile story au apărut după aproximativ 2h57m. Aceasta nu dovedește că începutul
  conținea clipuri bune, dar dovedește că strategia nu oferă coverage și recall controlabile.

**Pragul folosit pentru „context în afara intervalului final":** un fapt de context contează ca
fiind în afară dacă `t < start` sau `t > end`, cu toleranță zero. Cu o toleranță de 50 ms, a doua
sursă dă 19 în loc de 20. Cifra canonică este cea cu toleranță zero.

### Limita de proveniență a acestor două rulări

Rulările nu pot fi tratate ca baseline al fluxului normal de producție:

- artefactele `candidates.json` sunt din **14 august 2026**, iar rândurile de proiect din DB au fost
  actualizate pe **18 august 2026**;
- `clipper_settings` din DB conține pentru cele două proiecte doar 5, respectiv 4 chei, printre care
  `llm_select` și `reasoning_version`. `_normalise_settings` întoarce întotdeauna setul complet de
  defaults, deci aceste rânduri **nu au trecut prin API** — au fost scrise direct;
- artefactele nu păstrează un snapshot al setărilor, al modelului sau al metodei de lansare.

Sunt probe experimentale valide pentru existența defectelor, dar nu garantează că restul
configurației corespunde cu ce ar primi un utilizator real. Aceasta este justificarea directă pentru
`reasoning_run.json` din Batch 0.

## 5. Arhitectura țintă

```text
coverage planner bazat pe timp + caractere
  → atoms / promises / threads versionate
  → ancore cu dovezi verificabile în transcript
  → StoryEvidence canonic
  → variante de boundary care păstrează aceeași identitate de moment
  → refinement
  → remeasure + validity pe intervalul final
  → grupuri stabile de momente
  → shortlist stratificat pe buget, de momente, nu de variante aproape identice
  → judge pe pool, aplicat atomic
  → board format din momentele selectate explicit de judge
  → alegerea variantei + diversity final
  → preflight limitat + persistarea selection trace
  → feedback explicit, separat de automatizări
```

### 5.1 Contractul `StoryEvidence`

Toate componentele trebuie să consume aceeași dovadă:

```json
{
  "moment_id": "sha256 stabil din sursa si ancora",
  "archetype": "reaction|payoff|callback|reveal|...",
  "hook": {"t": 10.2, "evidence": "...", "atom_ids": ["..."], "grounded": true},
  "required_context": [{"t": 6.4, "fact": "...", "atom_ids": ["..."], "grounded": true}],
  "payoff": {"t": 31.8, "evidence": "...", "atom_ids": ["..."], "strength": 0.82, "grounded": true},
  "reaction": {"start": 32.0, "end": 35.1, "evidence": "..."},
  "boundary_coverage": {"context": true, "payoff": true, "reaction": true},
  "metrics": {"hook_latency": 3.8, "context_debt": 0.0},
  "validity": "valid|uncertain|invalid",
  "provenance": {"chunk_id": "...", "prompt_version": "...", "model": "..."}
}
```

Reguli obligatorii:

- timpul se raportează mereu la sursă, nu la textul trunchiat al promptului;
- payoff-ul semantic grounded este sursa principală; detectorul mecanic este fallback și trebuie
  etichetat astfel;
- orice schimbare de boundary declanșează recalcularea `boundary_coverage`, `hook_latency`,
  `context_debt` și a poziției payoff-ului;
- `invalid` nu poate fi compensat de energie audio; `uncertain` poate rămâne în pool, dar fără
  verdict fals de certitudine;
- variantele aceluiași moment păstrează același `moment_id`.

### 5.2 Grounding operațional — determinist, fără al doilea model

O dovadă este `grounded` dacă și numai dacă toate condițiile de mai jos sunt adevărate:

1. modelul a returnat `atom_ids`, un timestamp și un citat exact;
2. toți `atom_ids` există în artefactul `atoms`;
3. timestamp-ul se suprapune cu intervalul atomilor numiți;
4. citatul, normalizat, apare în textul acelor atomi.

`context coverage` înseamnă că intervalele tuturor dovezilor necesare se află în boundary-ul final.
Interpretarea semantică rămâne separată și poate fi `uncertain`.

**Normalizarea citatului.** Transcriptul Clipper **păstrează punctuația și majusculele**:
`clipper_pipeline.py:194` transcrie cu `keep_punctuation=True`, deci `_clean_text` este ocolit. O
potrivire pe șir brut ar eșua din cauza punctuației, a majusculelor și a diacriticelor, nu din cauza
grounding-ului.

`segmentation.norm_token` normalizează **un singur token**, nu un citat întreg — semnătura este
`norm_token(word: str)` și `.strip()` acționează doar la capete. Implementarea corectă este:

1. split în tokeni pe whitespace;
2. `norm_token` aplicat fiecărui token;
3. eliminarea tokenilor goi;
4. reunirea tokenilor cu un singur spațiu;
5. căutarea ca secvență contiguă în atomii ordonați temporal.

Aceeași normalizare se aplică ambelor părți. Trebuie testate: punctuație, majuscule, diacritice,
whitespace, citate care traversează doi atomi și citatul care devine gol după normalizare. **Un citat
normalizat gol eșuează explicit** — nu se potrivește cu nimic prin definiție.

**Eșecul potrivirii marchează `grounded: false`, nu elimină ancora.** O ancoră ne-grounded rămâne în
pool cu `validity: uncertain`. Eliminarea ei ar transforma prima versiune a regulii de potrivire
într-un filtru de recall necalibrat.

### 5.3 Regula de selecție după judge

Aceasta înlocuiește complet blendarea actuală. Se aplică **numai când un judge a rulat**; drumul
legacy, fără LLM, rămâne neschimbat.

- shortlist-ul de maximum 80 este un pool orientat spre recall;
- judge-ul selectează `clip_count + rezerve`;
- board-ul final conține **doar** momentele selectate explicit de judge;
- candidații din afara pool-ului primesc `not_evaluated` și nu concurează pe altă scară;
- candidații din pool pe care judge-ul nu i-a selectat primesc `not_selected_in_judged_pool`;
- dacă după dedupe și diversity rămân prea puține momente, se rulează un al doilea pool, format din
  următorii candidați după scor euristic, excluzându-i pe cei deja judecați;
- **maximum două runde de pool per proiect**, adică maximum 160 de moment groups evaluate și cost
  predictibil. Fără această limită, o sursă de 920 de candidați poate declanșa 12 runde;
- **backfill-ul după plafon**, când nici după a doua rundă nu sunt destule momente:
  - se ia **numai** din momentele cu status `not_evaluated`;
  - **exclude** toate momentele deja judecate și respinse — un moment pe care judge-ul l-a văzut și
    l-a refuzat nu se întoarce pe board printr-o ușă din spate;
  - momentele selectate de judge rămân primele;
  - fiecare intrare adăugată astfel este marcată `heuristic_backfill_after_round_limit` în
    `selection_trace.json`.

  Alternativa ar fi fallback euristic atomic pentru tot board-ul, dar acela ar arunca selecții judge
  valide. Backfill-ul explicit și separat este de preferat;
- dacă judge-ul eșuează sau răspunde invalid ori incomplet, rezultatul lui este ignorat **atomic** și
  întreg câmpul revine la ordinea euristică.

Scorurile brute judge și heuristic nu se compară niciodată direct.

**Consecință care trebuie declarată, nu ascunsă:** sub această regulă shortlist-ul devine singurul
punct de eșec pentru recall. Un moment bun aflat în afara pool-ului nu mai poate ajunge pe board pe
niciun drum. De aceea regula se activează implicit abia după ce shortlist-ul stratificat din Batch 5
există — vezi §6.

### 5.4 Bugetul shortlist-ului

`40/20/20` a fost o valoare inițială, nu una calibrată. Strategia este pe buget:

- dacă există maximum 80 de moment groups, intră toate;
- pentru surse mai mari se rezervă o parte pentru top euristic;
- o parte pentru story cu evidence grounded;
- restul se completează greedy pentru coverage temporal, threads și archetypes;
- proporțiile rămân configurabile și se calibrează ulterior pe corpus multi-gen.

Cota story nu este direct proporțională cu numărul de candidați story: un generator zgomotos ar
domina pool-ul.

## 6. Ordinea batch-urilor și de ce s-a schimbat

Ordinea urmează cost/beneficiu, nu ordinea în care au fost descoperite problemele.

| # | Batch | De ce aici |
|---:|---|---|
| 0 | Trace și observabilitate | ieftin, nu schimbă comportament, și fără el nu se poate demonstra nicio îmbunătățire |
| 1 | Contract de activare | fără el nimic nu poate fi testat prin fluxul normal |
| 2 | Regula de selecție + `origin` la feedback | cele două defecte cu impact demonstrat, ambele mici |
| 3 | `StoryEvidence` + remeasure | defectul central, dar mai scump |
| 4 | Coverage temporal + răspunsuri LLM validate | |
| 5 | Moment groups și shortlist stratificat | condiție pentru ca regula din Batch 2 să devină implicită |
| 6 | Model de scor explicit + split `scoring.py` | |
| 7 | Cache, provenance, reproducibilitate | |
| 8 | Ranker v2 | are nevoie de etichetele curate produse de Batch 2 |
| 9 | Preflight multimodal limitat | |
| 10 | Golden set complet, shadow rollout, activare | gate-ul care depinde de etichetare umană |

**Dependența critică:** Batch 2 livrează regula din §5.3 **numai în `story_v2_shadow`**. Devine
implicită abia după Batch 5. Motivul este măsurat: astăzi, un candidat story în afara celor 80 poate
încă ajunge pe board pe scorul euristic, iar 1 din 38 au fost judecați. Dacă regula „board = doar ce
a selectat judge-ul" devine implicită înainte de shortlist-ul stratificat, candidații story trec de
la subreprezentați la complet excluși. Reparația ar produce o regresie de recall.

## 7. Mod de lucru obligatoriu

1. Se salvează baseline-ul înainte de schimbare: artefacte, metrici, seed/config și versiuni.
2. Se scrie mai întâi un test care reproduce defectul și care eșuează pe comportamentul vechi din
   motivul așteptat.
3. Se modifică o singură categorie de comportament per batch.
4. Se rulează testele unitare vizate, apoi suita Clipper completă.
5. Se rulează evaluarea offline pe același corpus și se compară cu baseline-ul.
6. Orice artefact nou primește versiune, fingerprint de input și motiv de invalidare.
7. Legacy rămâne disponibil până când shadow mode trece gate-ul de calitate.
8. Un provider LLM indisponibil nu blochează proiectul; fallback-ul este explicit în artefact și UI.
9. Reviewer-ul uman evaluează fără să vadă ordinea sau sursa modelului.
10. Fiecare batch actualizează testele și documentația; fiecare fișier Clipper nou intră și în
    `docs/clipper-map.md` în același commit.

## 8. Batch-urile

### Batch 0 — trace și observabilitate

**Scop:** să putem demonstra o îmbunătățire, nu doar o schimbare. Partea ieftină, fără etichetare
umană.

**Modificări:**

- artefact `reasoning_run.json`: versiuni, chunk-uri, provider attempts, fallback-uri, număr de
  ancore, erori și **snapshot-ul setărilor efective plus metoda de lansare** — direct justificat de
  limita de proveniență din §4;
- artefact `selection_trace.json`: fiecare `moment_id`, variante, scoruri, status judge, motiv de
  eliminare, câștigător și eventualele runde de pool;
- `scripts/evaluate_clipper_reasoning.py`, raport repetabil legacy versus v2.

**Ce NU poate cere Batch 0:**

- **`moment_id` stabil.** Acesta apare abia în Batch 5. Până atunci trace-ul folosește identificatori
  de variantă. În Batch 5 schema trace-ului se versionează și primește `moment_id`.
- **Trace identic la rerun.** LLM-ul este nedeterminist, iar cache-ul cu identitate completă vine
  abia în Batch 7. Cerința „rerun-ul dă exact același trace" ar transforma nedeterminismul modelului
  într-un eșec de gate.

**Teste:** serializare, fingerprint stabil, artefacte valide după fallback, structura trace-ului
identică la rerun.

**Gate:** artefactele se produc pe ambele surse existente; **structura** și fingerprint-urile sunt
identice la rerun, iar diferențele de răspuns ale modelului sunt **declarate** prin hash de
model/request/response, nu tratate automat ca eșec. Fără cerință de etichetare umană aici.

**Gate trecut, 2026-08-21.** Satisfăcut pe **clone**, nu pe proiectele reale: re-scorarea rescrie
rândurile `clips`, iar ambele surse au board și feedback. `scripts/clone_clipper_project.py` face o
clonă cu hardlink-uri (14 MB de artefacte costă zero) și refuză să șteargă un proiect care nu e clonă.

Baseline-ul este în `docs/refs/reasoning-baseline-2026-08-21.json`. Ce a arătat, din artefactele
scrise de rulare și nu din reconstrucție manuală:

| | `gate2d3375` | `gateslice4h` |
|---|---|---|
| candidați | 66 | **901** |
| judecați | 66 (100%) | **80 (9%)** |
| story judecați | 12/12 | **1/19** |
| chunk-uri | 1, acoperire 99% | 2: **3h21m** și **38m** |
| fallback-uri | 1 | 2 |

Cifrele reproduc independent constatarea centrală a auditului — 9% din câmp evaluat, un singur
candidat story ajuns la judge, împărțire în două chunk-uri profund dezechilibrate — de data asta ca
artefact, nu ca reconstrucție. `launched_by: "script"` și `settings_snapshot` cu 5 chei arată direct
că rularea nu a venit prin API, adică exact lacuna descrisă în §4.

**Stabilitate la rerun**, cerută explicit de gate și verificată pe trei rulări ale sursei scurte și
două ale celei lungi:

| amprentă | stabilă între rulări |
|---|---|
| `input_fingerprint` | da |
| `structure_fingerprint` | da — aceiași 66 și 901 de identificatori de variantă |
| `chunk_plan_fingerprint` | da când se chunk-uiește; `null` când ancorele vin din cache |

Rerun-ul a găsit un defect pe care niciun test nu îl prindea: pe o rulare care refolosea ancorele din
cache nu se chunk-uia nimic, iar amprenta unei liste goale era **identică pe două proiecte fără
legătură** — o amprentă care se potrivește între inputuri diferite nu măsoară nimic. Este acum
`null`, iar `stages` spune de ce (`anchors: cached`).

**Ce variază între rulări și ce nu.** Numărul de candidați story oscilează (38 în auditul inițial,
27 și 19 pe rulările de gate) pentru că modelul este nedeterminist. Proporția judecată nu oscilează:
9% de fiecare dată. Aceasta este distincția pe care artefactele o fac vizibilă — nedeterminismul
modelului este zgomot, plafonul shortlist-ului este structură.

### Batch 1 — contract unic de activare

**Fișiere:** `server/config.py`, `server/routers/clipper.py`, `server/workers/clipper_build.py`,
tipurile și panoul advanced din frontend.

**Modificări:**

**Contractul tranzitoriu.** Semantica `story_v1` **nu se elimină** — proiectele existente o
folosesc, și fără ea se rupe baseline-ul necesar comparației. `reasoning_mode` are patru valori:

- `legacy`
- `story_v1`
- `story_v2_shadow`
- `story_v2`

Cheile vechi se citesc ca fallback, iar `reasoning_mode` explicit are prioritate:

| ce e în settings | se citește ca |
|---|---|
| `llm_select: false` | `legacy` |
| `llm_select: true` + `reasoning_version: "story_v1"` | `story_v1` |
| `reasoning_mode` prezent | valoarea lui, indiferent de cheile vechi |

**Modificări:**

- includerea `reasoning_mode` în `_default_settings()`, astfel încât `_normalise_settings` să nu îl
  mai arunce;
- **`story_v2` rămâne indisponibil până după trecerea gate-ului din Batch 5.** O cerere care îl
  setează din UI sau API este **respinsă explicit**, cu eroare de validare — nu redirecționată
  silențios către shadow. O redirecționare tăcută ar face ca setarea afișată utilizatorului să difere
  de cea care rulează, exact defectul pe care acest batch îl repară;
- în shadow mode, v2 scrie artefactele și metricile, dar lista utilizatorului rămâne ordonată de
  legacy;
- UI afișează `complete`, `partial`, `fallback` sau `failed_non_blocking`, providerul și motivul.

**Teste:** create/patch/round-trip settings, maparea celor două chei vechi, prioritatea
`reasoning_mode`, respingerea explicită a lui `story_v2`, worker config, parity legacy.

**Gate:** setarea ajunge identic din UI/API la worker; comportamentul proiectelor existente rămâne
neschimbat; lipsa LLM păstrează rezultatul legacy și nu blochează job-ul.

### Batch 2 — regula de selecție și originea feedback-ului

**Fișiere:** `llm_judge.py`, `clipper_build.py`, `feedback.py`, `clipper_render_jobs.py`, routerele
de clips.

Cele două jumătăți ale acestui batch au regimuri diferite de activare.

**Jumătatea în shadow — regula de selecție:**

- implementarea regulii din §5.3, **activă doar în `story_v2_shadow`**;
- statusuri explicite `selected`, `not_selected_in_judged_pool`, `not_evaluated`;
- eliminarea blendării între scara judge și scara euristică;
- fallback atomic la răspuns invalid sau incomplet;
- backfill-ul după plafonul de runde, conform §5.3.

**Jumătatea care intră direct în producție — originea feedback-ului:**

- fiecare eveniment de feedback primește `origin=manual|auto|system`;
- auto-export devine neutru pentru training; ultimul verdict explicit al utilizatorului are
  prioritate.

Fixul de `origin` **nu se ține în shadow.** Nu schimbă nimic din ce vede utilizatorul, iar fiecare zi
în care rămâne nelivrat adaugă etichete false în setul de antrenare. Nu are motiv să aștepte gate-ul
din Batch 5.

**Teste:** răspuns parțial, duplicate IDs, provider failure, a doua rundă de pool, plafonul de două
runde, auto-export urmat de reject, export manual urmat de reject, verdict repetat.

**Gate:** niciun scor din altă scară nu concurează direct; niciun eveniment `auto` nu produce o
etichetă pozitivă; ordinea vizibilă utilizatorului rămâne cea legacy.

**Gate trecut, 2026-08-22.** Regula din §5.3 este implementată în
`services/clipper/selection.py` și rulează în `story_v2_shadow`, care a devenit selectabil în aceeași
zi — poarta lui era Batch 5, iar acum face exact ce spune numele: calculează ordinea v2 și lasă
ordinea legacy să ajungă la utilizator.

Comparat pe `gateslice4h`, pe același câmp de 946 de candidați, rulat în shadow:

| | legacy | v2 |
|---|---|---|
| câștigători | 10 | 10 |
| **diferă de legacy** | — | **9 din 10** |
| backfill | 0 | 0 |

Bilanțul verdictelor pe tot câmpul: `selected=118, not_selected_in_judged_pool=247,
not_evaluated=581`.

### Baseline-ul legacy este imuabil, și de ce a trebuit să fie

Prima versiune scria blendul verdictului în `overall` pentru fiecare tăietură a unui moment judecat —
285 de candidați. Dar `overall` este exact ce citește board-ul **legacy**, iar shadow promite că
acea tablă nu se mișcă. Efectul era măsurabil și înșelător: cele două tablouri păreau să conveargă de
la 7 diferențe la **1**, nu pentru că v2 s-ar fi apropiat de legacy, ci pentru că **legacy fusese
mutat**.

Acum `heuristic_score` este înghețat înainte de orice verdict, propagarea scrie într-un
`selection_score` separat, iar regula clasează pe acela. Rezultatul: `overall` se mai mișcă pentru
**80** de candidați în loc de 285, iar diferența reală dintre tablouri este **9 din 10**.

**Cei 80 rămân, și trebuie spus.** Sunt pool-ul pe care `apply_ranking` îl blendează, comportament
care exista înainte de 2b și care rulează identic în `story_v1`. Shadow reține **regula**, nu
verdictul. Inerția completă cere ca `overall` să devină alias și scorurile să fie câmpuri explicite,
ceea ce este Batch 6.

### Ce s-a descoperit abia la rulare, în ordine

**Bilanțul se măsura pe supraviețuitori.** Raporta `not_selected_in_judged_pool=0` când erau 60,
pentru că se număra după filtrarea alternativelor. `selection.mark` marchează acum tot câmpul înainte
ca ceva să fie scris sau eliminat.

**Dedupe retrograda candidații judecați înainte ca regula să apuce să acționeze.** `deduplicate` își
alege liderii după `overall`, în care se blendează verdictul, deci un candidat judecat pierdea
propriul grup în favoarea unui frate nejudecat și era marcat `is_alternative`. Din 119 selectați,
**trei** supraviețuiau. Regula primește acum tot câmpul dedupat.

**Activarea era inversată.** `judged and not shadow` aplica regula în `story_v1` și `llm_nominate` și
o sărea în shadow. Un utilizator pe `story_v1` ar fi avut board-ul rescris de o regulă necomparată cu
nimic.

**Câștigătorii recuperați nu ajungeau pe tablă.** Flagul `is_alternative` era pus pe non-câștigători
dar niciodată **curățat** de pe câștigători, iar `rank_position` rămânea 0 — deci ordinea v2 nu
ajungea în DB, în API sau în auto-export.

**Momentele respinse reintrau printr-un frate.** Se propaga doar verdictul selectat, deci celelalte
tăieturi rămâneau `not_evaluated` — exact grămada din care ia backfill-ul.

**Calea cu scoruri producea board gol.** `apply_scores` seta `llm_score` și niciodată `llm_rank`.

**Rundele nu erau comparabile.** Fiecare rundă clasează propriul pool de la 1, deci un #1 din runda a
doua și unul din prima sunt două afirmații diferite. Ordonarea este acum `(judge_round, llm_rank)`.

### Batch 3 — dovadă canonică și recalculare după boundary

**Fișiere:** nou `server/services/clipper/story_evidence.py`, apoi `story.py`,
`candidate_proposals.py`, `candidate_boundaries.py`, `candidates.py`, `scoring.py`.

**Modificări:**

- introducerea `StoryEvidence` fără a duce `story.py` peste limita de 500 de linii;
- păstrarea `confidence`, `payoff_strength`, grounding și provenance în variante;
- funcții pure `ground_anchor`, `measure_story_candidate`, `validate_story_span`;
- recalculare după fiecare boundary final;
- payoff-ul semantic grounded în boundary și features; detectorul audio/text rămâne fallback
  etichetat;
- status separat `valid`, `uncertain`, `invalid`;
- grounding conform §5.2, inclusiv regula „eșecul potrivirii nu elimină ancora".

**Teste:** context înainte/după start, payoff după end, reaction tăiat, hook mutat, semantic versus
mechanical payoff, idempotent remeasure, citat care nu se potrivește, atom_id inexistent.

**Gate:** zero metrici story stale în corpus; 100% dintre candidații selectați au payoff inclus sau
status `uncertain`; minimum 95% context coverage grounded la variantele balanced.

**Gate trecut, 2026-08-21**, măsurat pe cele două clone după re-scorare:

| | `gate2d3375` | `gateslice4h` | gate |
|---|---|---|---|
| metrici stale | 0 | 0 | 0 ✓ |
| payoff inclus în fereastră | 13/13 | 28/28 | 100% ✓ |
| context coverage, variante balanced | 8/8 | 15/15 | ≥95% ✓ |
| `validity` | 11 valid, 2 uncertain | 6 valid, 22 uncertain | 0 invalid |
| sursa payoff-ului | 13 semantic / 54 mecanic | 28 semantic / 882 mecanic | — |

**Ce a găsit corpusul și niciun test nu prindea.** Prima măsurare a dat 7/8 la context coverage pe
sursa scurtă. Cauza: `start_on_sentence` mutase o variantă balanced de la 32.0 la 32.5, lăsând
singurul ei fapt de context la o jumătate de secundă în afara clipului — snap-ul de propoziție anula
exact `latest_complete_start`, principiul pentru care există `story.py`. Boundary-ul respectă acum un
`_context_floor`, iar cifra a urcat la 8/8 și 15/15.

### Ce verifică grounding-ul de fapt, și ce nu

Condiția 2 din §5.2 — „atomii numiți trebuie să existe" — **este astăzi inaplicabilă**, și e important
să fie scris aici ca să nu fie raportată ca îndeplinită. `atoms.to_lines` randează fiecare linie ca
`[secunde] text` și **nu conține niciun id de atom**, deci modelul nu poate returna `atom_ids`: nu a
văzut niciodată unul. Măsurat pe corpus, localizarea este **100% prin timestamp, 0% prin `atom_ids`**.

Ce se verifică efectiv, și este o verificare reală:

1. citatul există și supraviețuiește normalizării per token;
2. este căutat în atomul care conține timestamp-ul revendicat, plus unul de fiecare parte;
3. apare acolo ca secvență contiguă de **tokeni întregi**.

`matched_by` înregistrează care localizator a fost folosit, tocmai ca cele două rate să nu fie
raportate ca una singură.

**Ratele stricte, măsurate:** payoff **33/41 = 80%**, context **20/32 = 62%**; sursa lungă 20/28 și
15/27. **Aceste rate sunt per candidat, calculate înainte de deduplicarea canonică**, deci numără
aceeași afirmație o dată pentru fiecare variantă care o împarte. Cifra pe afirmații distincte este în
§14.3 și este singura comparabilă între rulări. Concluzia care stătea aici — „modelul parafrazează
mai mult pe un transcript lung" — **nu se susține**: măsurătoarea nu separă parafraza de o legătură
canonică lipsă, iar duplicarea putea produce singură diferența dintre surse. `validity` majoritar
`uncertain` pe sursa lungă rămâne raportare corectă.

**Două defecte ale primei versiuni, găsite la review și reparate înainte de a fi raportate ca
rezultat:** potrivirea era `substring` pe textul concatenat, deci `"a bla"` trecea contra
`"a blast furnace"`; iar fereastra temporală era ±20 de secunde, adică patru până la opt atomi pe
acest corpus. Ambele sunt acum secvență de tokeni și fereastră structurală de ±1 atom. Efectul pe
corpus a fost mic (33 în loc de 34 payoff-uri), dar amândouă puteau produce un „grounded" fals.

### Batch 4 — coverage temporal și răspunsuri LLM validate

**Fișiere:** `llm_select.py`, `atoms.py`, `promises.py`, configurarea Clipper.

**Modificări:**

- chunk planner limitat simultan de timp și caractere, inițial maximum 45 de minute și 40.000 de
  caractere;
- overlap temporal inițial de 90 de secunde și deduplicare prin `moment_id`;
- nicio trunchiere globală tăcută; fiecare interval apare într-un chunk sau într-un motiv explicit
  de skip;
- cotă adaptivă după durată și densitate, cu minim de coverage per interval;
- `_ask_json`: validează JSON și schema înainte de acceptare, apoi încearcă următorul provider;
- timeout, retry budget și anulare configurabile, cu progres per chunk;
- prompturile cer `atom_ids`, citate exacte și timestamp-uri.

**Teste:** sursă de patru ore cu dialog rar, eveniment la intersecția chunk-urilor, JSON invalid de
la primul provider, timeout, dedupe overlap.

**Gate:** acoperire temporală fără goluri; nicio pierdere de coadă; fallback-ul produce rezultat
valid; `slice4h00test` are cel puțin șase intervale temporale, nu două chunk-uri dezechilibrate.

**Gate trecut, 2026-08-22.** Măsurat pe `gateslice4h`, aceeași sursă de patru ore:

| | înainte | după |
|---|---|---|
| chunk-uri de ancore | 2 | **6** |
| cel mai lat chunk | 3h21m | **45m** |
| acoperire temporală | nemăsurabilă | **14398,2s din 14400s, zero goluri** |
| ancore | 15 | **43** |
| cel mai timpuriu payoff | 2,95h | **0,11h** |

Planul de chunk-uri, cu overlap vizibil la cusături:

```text
#0  0.00h -> 0.75h   45.0 min   24017 car   10 ancore
#1  0.73h -> 1.47h   44.8 min   31620 car    9 ancore
#2  1.45h -> 2.19h   44.9 min   23637 car    1 ancora
#3  2.17h -> 2.92h   44.9 min   27748 car   10 ancore
#4  2.89h -> 3.64h   44.8 min   27413 car   10 ancore
#5  3.61h -> 4.00h   23.3 min   12594 car    6 ancore
```

**Ce dovedește asta.** §4 spunea: „toate payoff-urile story au apărut după aproximativ 2h57m. Aceasta
nu dovedește că începutul conținea clipuri bune, dar dovedește că strategia nu oferă coverage și
recall controlabile." Acum se știe care dintre cele două era: payoff-urile se distribuie **11 / 7 /
11 / 14** pe cele patru ore, iar cel mai timpuriu e la 0,11h. Începutul stream-ului nu era gol — era
**necitit**. Prima jumătate a sursei producea zero ancore pentru că nimic nu ajungea la model cu o
cotă pe care s-o poată folosi, nu pentru că nu ar fi avut ce.

### Batch 5 — moment groups și shortlist stratificat

**Fișiere:** nou `server/services/clipper/candidate_groups.py`, `llm_judge.py`, `dedupe.py`,
`clipper_build.py`.

**Modificări:**

- grupare stabilă înainte de judge, după payoff, overlap, thread și similaritate;
- maximum una-două variante reprezentative per moment în packet;
- shortlist pe buget conform §5.4;
- packet cu start/end, opening și **marcaje explicite pentru context, payoff, reacție, metrici și
  provenance** — nu doar text brut;
- grupurile folosesc ID stabil, iar diversity primește durata reală a proiectului;
- după acest batch, regula din §5.3 poate deveni implicită în `story_v2`.

**Teste:** 200 de variante ale 40 de momente, buget pe stream lung, buget pe stream scurt sub 80 de
grupuri, IDs stabile la rerun, durată fără candidat la coada sursei.

**Gate:** fiecare categorie de buget este reprezentată; niciun moment din pool nu rămâne fără status;
pe corpus, numărul de momente story ajunse la judge crește față de baseline.

**Gate trecut, 2026-08-22.** Măsurat pe `gateslice4h`:

| | înainte | după |
|---|---|---|
| candidați | 909 | 943 |
| grupuri de momente | — | **295** |
| pool-ul judge-ului | 80 variante | **80 momente** |
| **variante story care poartă verdict** | **1 din 75** | **47 din 61** |
| candidați care poartă un verdict | 80 | **173** |

Categoriile bugetului, toate trei active: `heuristic=40, story=17, coverage=23`.

**Ce s-a schimbat de fapt.** Vechiul `sorted(cands, -overall)[:80]` avea două defecte într-o singură
linie. Clasa **variante**, deci cele două-patru tăieturi ale unei singure ancore puteau ocupa patru
din cele optzeci de locuri certându-se între ele. Și clasa după scorul **euristic**, adică exact
lucrul pe care judge-ul există ca să-l corecteze.

**Verdictul aparține momentului, nu tăieturii care l-a reprezentat.** Fără propagare, jumătatea pe
care o citește board-ul rămânea nelegată: judge-ul scorează un reprezentant per moment, dar dedupe și
diversity aleg câștigători din **tot** câmpul, deci un câștigător care e altă tăietură a unui moment
judecat nu purta niciun verdict. `propagate_verdicts` îi dă verdictul momentului, blendat cu aceeași
pondere împotriva **propriului** scor euristic — a-i copia `overall`-ul reprezentantului ar pune
numărul unei tăieturi pe altă tăietură, adică exact amestecul de scări pe care planul îl elimină.

### Ce NU repară acest batch, și de ce

**7 din cei 10 câștigători rămân `not_evaluated`.** Toți șapte sunt candidați legacy, din momente care
nu au intrat niciodată în pool: 80 de momente judecate din 295. Un câștigător dintr-un moment
nejudecat nu are ce moșteni, fiindcă momentul lui nu a fost întrebat niciodată. Ei câștigă pe scorul
euristic **neblendat**, în timp ce candidații judecați au fost blendați în jos — §3.2, litera ei.

Aceasta este exact regula din §5.3, jumătatea 2b din Batch 2, și nu poate fi rezolvată de shortlist:
oricât de bun ar fi pool-ul, atâta timp cât board-ul se alege din tot câmpul, un moment nejudecat
poate câștiga. Batch 5 a livrat însă condiția de care 2b depindea — shortlist stratificat plus
propagarea verdictului la nivel de moment — deci 2b este acum deblocat.

**Un defect găsit de un test, nu de corpus.** Faptele story ale unui grup se citeau de pe liderul
după scor. Un moment poate conține și o fereastră legacy, și una story — se suprapun, deci gruparea
le pune împreună — iar când tăietura legacy scorează mai bine, momentul devenea invizibil pentru cota
story. Ceea ce se întâmplă des: euristica e tocmai ce preferă tăietura zgomotoasă și curată. Acum
faptele și reprezentantul vin de la cel mai bun membru **story**, iar scorul grupului rămâne cel mai
bun al momentului, oricare tăietură l-a obținut.

### Batch 6 — model de scor explicit

**Fișiere:** `scoring.py`, nou `scoring_profiles.py`, `candidates.py`, `llm_judge.py`,
`clipper_build.py`.

**Modificări:**

- câmpuri distincte `heuristic_score`, `learned_score`, `judge_score`, `selection_score`; `overall`
  devine alias de compatibilitate la marginea API;
- eligibility separat de quality: lipsa payoff-ului sau a contextului grounded poate invalida;
  informația necunoscută rămâne `uncertain`;
- constantele de hook și context sunt unice și importate, nu duplicate hardcoded;
- content type local rămâne pe candidat până la layout și persistare;
- **split-ul `scoring.py`**, care are deja 505 linii, peste limita de 500 din CLAUDE.md: profilele și
  ponderile se mută în `scoring_profiles.py`.

**Condiție pentru split, din regula 11 a CLAUDE.md:** comentariile care însoțesc ponderile conțin
măsurători care au costat sesiuni întregi. Ele se mută **verbatim**, fără reformatare, iar
`scripts/score_contribution.py` se rulează înainte și după, pe același proiect, pentru a demonstra
că ordonarea nu s-a schimbat. Un split care schimbă un singur scor nu este un split.

**Teste:** monotonie, comparabilitate, invalid versus energetic, profil local, parity legacy,
egalitate bit-cu-bit a contribuțiilor înainte și după split.

**Gate:** toate variantele finaliste au score trace complet; schimbarea judge-ului nu modifică scorul
euristic; niciun fișier peste 500 de linii.

**Gate trecut, 2026-08-22.** Măsurat pe `gateslice4h`, 946 de candidați:

| condiție | rezultat |
|---|---|
| score trace complet | `overall`, `heuristic`, `eligibility` la **946/946**; `judge` și `selection` unde se aplică |
| judge-ul nu atinge euristica | **exact 80** de candidați au `overall != heuristic_score`, și aceia sunt pool-ul judecat |
| fișiere peste 500 de linii | niciunul dintre cele atinse |

`learned` lipsește la toți, corect: ranker-ul e dormant de la Batch 2a încoace.

**Un câmp numit trebuie să fie sursa reală, nu o decorație.** Prima versiune seta `judge_score` doar
în bucla candidaților clasați: **14 din 365** de candidați judecați aveau unul, iar **77 din 91**
dintre cei *selectați* nu aveau. Un câmp prezent pe 4% din rândurile pe care le descrie nu e sursa
nimic — e o decorație care se citește ca un contract. Se scrie acum oriunde se scrie un verdict:
în bucla clasaților, în cea a respinșilor, pe calea cu scoruri și în propagare. Acum **0 din 365**.

**`ineligible` nu s-a declanșat niciodată pe acest corpus.** Zero candidați din 946. Mecanismul e
testat unitar, dar nu a fost niciodată exercitat de date reale, fiindcă Batch 3 raportează payoff
inclus la 64/64 — deci `validate_story_span` nu întoarce `invalid` nicăieri aici. Nu este dovadă că
funcționează pe o sursă unde ar conta.

**Splitul `scoring.py`, cu dovada cerută.** 505 → 355, cu rândurile de ponderi în
`scoring_profiles.py` (173). Mutare **verbatim**: aceleași octeți, aceleași comentarii, fiindcă
tocmai comentariile sunt evidența a ce s-a măsurat pe ce sursă. `scripts/score_contribution.py` a
fost rulat pe profilul gaming înainte și după și raportează **contribuții identice** pentru fiecare
sub-scor — singura dovadă că splitul unui tabel de ponderi e sigur.

`routers/clipper.py`, singurul fișier pe care l-am dus eu peste limită (665 → 747), s-a spart în
`clipper_settings.py` (contractul de settings, fără rute) și `clipper_runs.py` (analyze, cancel,
retry, artifacts, presets, ranker — montat lângă celelalte, URL-uri neschimbate). Acum 410.

**P2-ul din §3.1 e închis.** Persistarea folosea `content_type=profile`, adică tipul de proiect, în
timp ce scoring-ul folosea tipul local al stretch-ului. Pe sursa de patru ore diferența e reală:
779 gaming, 88 sports, 79 irl. Explicația și layout-ul foloseau alt profil decât cel pe care s-a
scorat clipul.

### Batch 7 — cache, provenance și reproducibilitate

**Fișiere:** `clipper_build.py`, serviciile story, `storage.py`.

**Modificări:**

- envelope comun pentru atoms, promises, threads, episodes, anchors, judge **și `segment_types`**,
  care astăzi nu are stamp deloc (`clipper_build.py:78`);
- fingerprint din sursă/transcript, prompt, model, temperatură, chunk config, versiunea serviciului
  și artefactele upstream;
- invalidare țintită: schimbarea judge-ului nu regenerează transcriptul, dar schimbarea
  transcriptului invalidează tot reasoning-ul dependent;
- cache pentru judge, cu seed și temperatură explicite unde providerul permite.

**Teste:** matrice de invalidare, cache hit/miss, artefact vechi fără envelope, rerun identic,
`segment_types` invalidat la schimbarea configurației.

**Gate:** două rulări cu aceeași identitate produc același selection trace sau declară explicit
partea nondeterministă; nicio reutilizare cu fingerprint incompatibil.

**Status parțial, S7a livrat 3 septembrie 2026.** Envelope-ul comun și matricea de invalidare sunt
implementate pentru atoms, promises, threads, episodes, anchors și `segment_types`; episodes au
devenit artefact persistent în loc să fie reconstruite în interiorul detectorului. Identitatea
include sursa înregistrată la ingest, transcriptul exact, upstream-urile efectiv citite, versiunile,
promptul, modelele rezolvate per engine, temperatura, contextul, timeout-ul și chunk config. JSON-ul
este strict și canonic, iar artefactele pre-S7 sunt miss deliberat. `judge` este numai rezervat în
schema comună.

**Status parțial, S7b livrat 3 septembrie 2026.** Promises și anchors păstrează acum un checkpoint
per chunk în interiorul envelope-ului comun. Un chunk `usable` este refolosit, unul `unusable` sau
`pending` este singurul reapelat, iar starea se scrie după fiecare cerere. Rândul persistă
providerul și modelul rezolvat, amprentele promptului și răspunsului, istoricul încercărilor,
numărul de obiecte normalizate și `seed: null` plus `nondeterministic: true`. O listă goală este
cacheabilă; un răspuns structural valid care nu produce niciun obiect valid rămâne retryable. Un
checkpoint nested malformat se refuză integral, nu se filtrează peste numitor. Cache-ul judge,
`anchor_id`, scara dedupe și identitatea comună selection/render rămân deschise, deci Batch 7 nu
este declarat închis.

**Status parțial, S7c livrat 3 septembrie 2026.** Judge-ul păstrează verdictul brut al fiecărei
runde în envelope-ul comun, legat de promptul exact și de contractul provider/model. Numai o rundă
utilizabilă și aplicabilă pool-ului curent este refolosită; una neparsabilă, goală sau fără niciun
id aplicabil este reapelată. Proveniența declară lipsa seedului și nondeterminismul, iar rerun-ul
reaplică local același răspuns fără apel extern. Starea malformată este refuzată integral, scrierea
checkpointului nu poate anula un verdict valid, iar un fallback de parsare recuperat este raportat
ca fallback. După S7c rămân deschise `anchor_id`, scara dedupe și identitatea comună
selection/render.

### Batch 8 — ranker v2

**Fișiere:** `ranker.py`, routerele de clips.

`origin` la feedback s-a livrat deja în Batch 2, deci acest batch pornește de la etichete curate.

**Poziția față de comentariul existent.** `ranker.py:186` spune astăzi, explicit, că metricile sunt
pe train și nu pe holdout, pentru că un split pe 40–200 de rânduri este mai ales zgomot. Comentariul
**nu se șterge și nu se înlocuiește** până când datele nu arată că evaluarea nouă este mai relevantă.
Se adaugă lângă el rezultatul noii evaluări și data la care a fost obținut; decizia se ia atunci, pe
dovadă, nu acum, pe intenție.

**Modificări:**

- persistarea baseline-ului euristic real, dinainte de judge și dedupe;
- feature set extins cu context debt, hook latency, payoff semantic, grounding, story confidence,
  content type și calitate vizuală;
- **leave-one-project-out** ca evaluare inițială, cu interval de încredere față de baseline. Pragul
  de cinci proiecte și 100 de etichete este insuficient pentru un holdout stabil, deci nu se
  folosește un split simplu;
- learned ranker rămâne oprit până când există câștig demonstrat pe proiecte nevăzute.

**Punctul de plecare este zero, nu 43.** După Batch 2a, `training_rows()` întoarce 0 rânduri pe acest
rig, pentru că niciunul dintre cele 69 de evenimente istorice nu are o origine cunoscută (§3.2). Asta
nu schimbă standardul statistic; înseamnă doar că ranker-ul rămâne dormant mai mult timp, ceea ce
este comportamentul corect când singura alternativă era un set cu o singură clasă.

**Gate-ul trebuie să ceară explicit ce lipsea:**

- ambele clase prezente în setul de antrenare — nu doar un număr de rânduri;
- distribuția pozitiv/negativ raportată în artefactul modelului;
- câștig față de baseline la LOPO, cu interval de încredere care nu include zero.

Prima condiție este cea care lipsea. `should_use_learned` verifică numărul de rânduri și compară
două NDCG-uri, dar nu se uită niciodată la etichete: pe un set cu o singură clasă ambele metrici ies
0.0 și promovarea este oprită de o egalitate, nu de o regulă. O egalitate accidentală nu este o
poartă.

**Teste:** extragerea baseline-ului corect, LOPO fără scurgere între folduri, stabilitate pe
bootstrap, proiecte separate între train și validation, **set cu o singură clasă respins explicit**.

### Batch 9 — preflight multimodal limitat

**Fișiere:** `review.py`, `review_vision.py`, `clipper_render_jobs.py`.

Beneficiul real al acestui batch este, deocamdată, **ipoteză de validat** (§3.3). Se măsoară înainte
de a fi extins.

**Modificări:**

- keyframe precheck ieftin pentru pool-ul final: față/crop, frame inutil, text tăiat;
- review-ul local poate aplica **o singură** corecție bounded de boundary sau layout, apoi
  reverifică;
- vision rămâne opțional și failure-safe;
- rezultatul intră în selection trace și feedback, nu doar în log.

**Teste:** lipsă model vision, timeout, corecție validă, corecție peste limite, a doua corecție
interzisă.

**Gate:** nicio buclă infinită; maximum o corecție automată; exportul continuă în mod degradat cu
avertisment explicit.

### Batch 10 — golden set, shadow rollout și activare

Aici se plătește costul de etichetare umană, o singură dată, când tot restul e stabil.

**Modificări:**

- definirea setului golden din proiectele existente din `data/clipper/MANIFEST.md`;
- etichetare manuală pentru `worthwhile`, `self_contained`, `hook`, `payoff`, boundary și utilitatea
  vizuală. Setul crește incremental și nu blochează batch-urile anterioare;
- rulare `story_v2_shadow` pe întreg corpusul;
- comparație automată cu legacy și review orb al diferențelor;
- pilot pe proiecte noi, fără a schimba implicit proiectele existente;
- activare implicită numai după două evaluări consecutive peste gate;
- păstrarea unui switch rapid la legacy pentru minimum o versiune stabilă.

**Gate final:** minimum 10 surse și 150 de momente etichetate; zero regresii de crash sau blocare;
parity pentru legacy; toate testele Clipper verzi; coverage complet; metrici golden peste baseline;
defect rate uman mai mic fără scădere relevantă a diversității.

## 9. Matricea minimă de testare

| Nivel | Ce trebuie verificat |
|---|---|
| Unit | grounding, remeasure, chunks, overlap, group IDs, regula de selecție, feedback precedence |
| Contract | settings UI/API/worker, schema LLM, artefact envelopes, migrare compatibilă |
| Integration | pipeline complet cu LLM valid, JSON invalid, timeout, provider absent, a doua rundă de pool |
| Regression | legacy produce aceeași ordine și aceleași exporturi, exceptând trace-ul nou |
| Offline eval | Recall@K, Precision@K, NDCG@K, pairwise accuracy, context coverage, payoff/boundary error |
| Human eval | clipul se înțelege singur, hook rapid, payoff real, final satisfăcător, alegere vizuală bună |
| Reliability | restart, retry, cache invalidation, anulare, rerun fără duplicate |

Niciun test bazat doar pe „JSON valid" nu este test de calitate semantică. Golden set-ul și
review-ul orb sunt obligatorii pentru schimbări de prompt, shortlist sau scoring.

## 10. Fișiere de adăugat

| Fișier nou | Batch | Rol |
|---|---:|---|
| `scripts/evaluate_clipper_reasoning.py` | 0 | raport repetabil legacy versus v2 |
| `server/services/clipper/story_evidence.py` | 3 | contractul canonic și recalcularea |
| `server/services/clipper/candidate_groups.py` | 5 | moment IDs, clustering, shortlist pe buget |
| `server/services/clipper/scoring_profiles.py` | 6 | profilele și ponderile, mutate verbatim |
| `server/tests/test_clipper_story_evidence.py` | 3 | regresii de boundary și grounding |
| `server/tests/test_clipper_candidate_groups.py` | 5 | coverage, buget, IDs stabile |
| `server/tests/test_clipper_feedback.py` | 2 | origin și prioritatea verdictelor |

Artefactele noi se înregistrează în `storage.py` și în `docs/clipper-map.md`. Nu se introduce migrare
DB în primele batch-uri: score trace și provenance stau în JSON-ul `reasoning`. O coloană nouă se
justifică doar dacă interogările reale arată că JSON-ul este insuficient; atunci modelul și migrarea
se schimbă împreună.

## 11. Strategie de rollback

- `legacy` rămâne intact și este fallback-ul fiecărui batch;
- shadow mode nu schimbă ordinea sau clipurile vizibile utilizatorului;
- fiecare artefact are versiune, astfel încât v2 nu suprascrie un cache legacy valid;
- activarea se face printr-un singur `reasoning_mode`, nu prin combinații de flags;
- dacă un gate eșuează, se revine la ultimul batch valid; nu se compensează prin alte ponderi sau
  prompturi în același commit;
- schimbările DB, dacă devin necesare, au migrare backward-compatible și downgrade testat.

## 12. Ce nu trebuie făcut

- nu se schimbă modelul LLM înainte de repararea contractelor de date;
- nu se măresc doar limitele de tokeni; problema este coverage-ul;
- nu se mărește `MAX_CLIP_CHARS` ca reparație pentru packet-ul de judge — trunchierea nu este
  defectul măsurat;
- nu se elimină candidați story prin dedupe euristic final înainte de judge;
- nu se amestecă scorurile parțial când judge-ul răspunde incomplet;
- nu se activează learned ranker pe metrici de train;
- nu se consideră auto-exportul feedback pozitiv;
- nu se prezintă ipotezele din §3.3 ca efecte demonstrate;
- nu se transformă indisponibilitatea LLM într-o eroare care blochează aplicația;
- nu se adaugă un graph general sau o rescriere completă fără dovadă că pașii de mai sus sunt
  insuficienți;
- nu se include TikTok în acest plan.

## 13. Ce s-a schimbat față de versiunea inițială

1. **Constatările sunt separate în trei categorii** — confirmat în cod, impact măsurat, ipoteză de
   validat (§3.1, §3.2, §3.3). Anterior erau într-o singură listă de „probleme confirmate".
2. **Fiecare constatare confirmată are acum locul exact în cod**, nu o descriere.
3. **Packet-ul de judge a fost reformulat.** Limita de 900 de caractere nu mai este prezentată ca
   defect: măsurat, mediana este 423 și doar 2 din 80 ating limita. Defectul este absența marcajelor
   pentru context, payoff, reacție și provenance. Adăugată și interdicția explicită din §12.
4. **Batch 0 a fost spart.** Trace-urile și `reasoning_run.json` sunt Batch 0 și nu cer etichetare
   umană. Golden set-ul a devenit Batch 10 și blochează doar activarea finală.
5. **Reordonare.** Regula de selecție și `origin` la feedback au urcat din Batch 5 și 7 în Batch 2,
   imediat după contractul de activare. Restul batch-urilor s-au renumerotat; tabelul din §6 dă
   corespondența și motivul.
6. **Regula pentru cei 840 nejudecați a fost înlocuită** cu varianta pool + selecție explicită
   (§5.3). Propunerea mea anterioară — judecații ocupă automat primele 80 de poziții, inclusiv cei
   respinși — a fost respinsă și retrasă. Era greșită: reintroducea aceeași inversiune, în sens opus.
7. **Adăugat plafonul de două runde de pool** și completarea marcată explicit în trace, pentru ca
   regula să nu poată intra într-o buclă costisitoare pe surse mari.
8. **Adăugată dependența Batch 2 → Batch 5** (§6): regula rulează în shadow până există shortlist
   stratificat, altfel candidații story trec de la subreprezentați la complet excluși.
9. **`segment_types` fără stamp** este acum numit explicit, în §3.1 și în Batch 7.
10. **Split-ul `scoring.py`** este planificat în Batch 6, cu `scoring_profiles.py` și cu condiția ca
    ponderile și comentariile lor să se mute verbatim, verificat cu `score_contribution.py`.
11. **20, nu 19**, pentru contextul în afara ferestrei finale, cu pragul definit: toleranță zero.
    Cifra din review-ul meu folosea o toleranță de 50 ms nedeclarată.
12. **Batch 8 recunoaște explicit comentariul din `ranker.py:186`** și nu îl înlocuiește până când
    datele nu demonstrează contrariul. Evaluarea inițială devine leave-one-project-out cu interval de
    încredere, în locul unui holdout pe cinci proiecte.
13. **Adăugată limita de proveniență a celor două rulări** (§4), cu dovada: artefacte din 14 august,
    rânduri actualizate pe 18 august, iar `clipper_settings` conține 5 și 4 chei, în timp ce
    `_normalise_settings` întoarce întotdeauna setul complet — deci nu au trecut prin API.
14. **Grounding-ul are definiție operațională deterministă** (§5.2), plus două precizări adăugate de
    mine: normalizarea prin `norm_token`, pentru că transcriptul este deja fără punctuație și cu
    litere mici, și regula că o potrivire eșuată marchează `grounded: false` fără să elimine ancora.
15. **Shortlist-ul a trecut de la cote fixe la buget** (§5.4).
16. **Adăugată declarația de consecință** din §5.3: sub noua regulă, shortlist-ul devine singurul
    punct de eșec pentru recall.

### Runda a doua de consolidare

După verdictul pe punctele A, B și C din §14:

17. **A acceptat, cu o condiție adăugată în Batch 1:** `story_v2` este **respins explicit** din
    UI/API până la trecerea gate-ului din Batch 5, nu redirecționat silențios către shadow. O
    redirecționare tăcută ar face ca setarea afișată să difere de cea care rulează — exact defectul
    pe care Batch 1 îl repară.
18. **Batch 2 a fost împărțit în două regimuri.** Fixul `origin=manual|auto|system` intră direct în
    producție; doar regula de selecție rămâne în shadow. Motivul: `origin` nu schimbă nimic vizibil,
    iar fiecare zi de întârziere adaugă etichete false în setul de antrenare.
19. **B acceptat.** Două runde înseamnă maximum 160 de moment groups și cost predictibil.
20. **Backfill-ul după plafon are acum reguli explicite** (§5.3): numai din `not_evaluated`, exclude
    momentele deja judecate și respinse, momentele selectate de judge rămân primele, fiecare intrare
    marcată `heuristic_backfill_after_round_limit`. Alternativa — fallback euristic atomic pentru tot
    board-ul — a fost respinsă pentru că ar arunca selecții judge valide.
21. **C, prima regulă confirmată.** Match eșuat → `grounded: false`, ancora rămâne cu
    `validity: uncertain`, nu se elimină înainte de măsurarea fals-negativelor.
22. **C, a doua regulă corectată — premisa mea era falsă.** Clipper-ul transcrie cu
    `keep_punctuation=True` (`clipper_pipeline.py:194`), deci `_clean_text` este ocolit, iar
    transcriptul **are** punctuație și majuscule. În plus `norm_token` normalizează un singur token,
    nu un citat întreg. §5.2 conține acum procedura corectă în cinci pași, plus cerința ca un citat
    normalizat gol să eșueze explicit.
23. **Gate-ul Batch 0 a fost relaxat pe două puncte** care nu erau realizabile la momentul lui:
    `moment_id` stabil apare abia în Batch 5, iar „rerun identic" nu poate fi cerut înainte de
    cache-ul din Batch 7, pentru că LLM-ul este nedeterminist. Gate-ul cere acum identitate de
    structură și fingerprint, cu diferențele de răspuns declarate prin hash.
24. **Batch 1 păstrează semantica `story_v1`.** `reasoning_mode` are patru valori, iar cheile vechi
    se citesc ca fallback, cu tabelul de mapare în Batch 1. Fără asta se rupe baseline-ul necesar
    comparației.
25. **Definition of Done reformulat:** „momentele selectate explicit de judge, plus backfill declarat
    după plafon", nu absolutul „doar momente selectate explicit", care ar fi intrat în contradicție
    cu regula de backfill.

## 14. Dezacorduri tehnice rămase

**Niciunul.** Punctele A, B și C au fost tranșate: A și B acceptate cu precizări, C acceptat pe
prima regulă și **corectat pe a doua, unde premisa mea era greșită** (vezi §13.22). Corecțiile sunt
integrate mai sus.

Rămân trei riscuri deschise, care nu sunt dezacorduri, ci lucruri care trebuie măsurate la
implementare:

1. **Plafonul de două runde de pool este ales, nu măsurat.** 160 de moment groups este o limită de
   cost, nu un optim. Numărul corect se poate stabili abia după Batch 5, când se vede câte momente
   distincte produce un pool pe o sursă lungă.
2. **Ordinea Batch 2 înaintea Batch 5 rămâne partea cea mai riscantă a reordonării.** Este sigură
   doar cât timp regula de selecție stă în shadow. Dacă la implementare apare presiunea de a o activa
   mai devreme, răspunsul este nu, iar motivul este măsurat: 1 candidat story din 38 a ajuns la
   judge. Constrângerea este acum aplicată și mecanic — `story_v2` este respins din API până la
   gate-ul din Batch 5.
3. **Regula de grounding produce rezultate negative încă neclasificate.** De aceea eșecul
   marchează, nu elimină.

   **Măsurat, 2026-08-22**, cu `scripts/measure_grounding.py`, pe 48 de afirmații distincte de pe
   `gateslice4h` și 10 de pe `gate2d3375`. Rândurile descriu **ce a găsit matcher-ul**, nu de ce:

   | | `gateslice4h` | `gate2d3375` |
   |---|---|---|
   | grounded strict, ca livrat | 27 (56%) | 10 (100%) |
   | potrivire exactă locală, fără legătură canonică | 11 (23%) | 0 |
   | potrivire relaxată, încă nevalidată | 5 (10%) | 0 |
   | **nicio potrivire locală în ±120s** | **5 (10%)** | **0** |

   **Ce NU spune tabelul.** Nu spune că modelul parafrazează, nu spune că a numit atomi greșiți — nu
   a numit niciunul, `matched_by` este `timestamp` pentru toate cele 111 intrări, iar `atom_ids` din
   artefact sunt completate de noi din fereastră. Și nu spune că cele 5 din urmă sunt inventate:
   „fără potrivire locală" este limita metodei, nu o concluzie despre model. Și nu spune că cele 21 de
   negative sunt **false** negative: asta presupune că citatul chiar e acolo și l-a ratat matcher-ul,
   ceea ce se stabilește abia după resolver-ul determinist. Până atunci sunt negative neclasificate,
   iar „marchează, nu elimina" rămâne decizia corectă tocmai fiindcă nu știm încă ce sunt.

   **Prima versiune a măsurătorii a fost greșită și a inversat concluzia.** Număra 111 afirmații
   unde sunt 48 distincte, fiindcă variantele aceluiași moment împart blocul story. Și căuta în
   TOT fluxul: testată cu 200 de citate inventate din vocabularul transcriptului, 8 treceau proba
   de subsecvență și **200 din 200** treceau bag-of-words. Un test pe care nimic nu-l poate pica
   raportează zero eșecuri, și exact așa a ieșit „modelul nu a inventat niciodată". Metoda de acum
   caută într-o fereastră de ±120s în jurul momentului revendicat, a renunțat la bag-of-words, și
   trece **0 din 200** de citate inventate.

   **Ordinea de reparare**, decisă la review: resolver determinist (citatul căutat în tokeni întregi,
   `matched_t` și driftul salvate, potrivirile multiple rămân `ambiguous`) → remăsurare pe aceleași
   ancore cached → A/B de prompt cu `atom_ids` doar pentru ce rămâne nerezolvat → prag lexical
   calibrat pe holdout. Nu se presupune că toate cele 11 vor deveni grounded.

## 14c. Pilot pe surse diverse, 2026-08-22

**Întrebarea:** motorul story generalizează, sau e modelat de singurul stream de Minecraft pe care
s-au măsurat batch-urile 0-6? Costul de etichetare din Batch 10 se plătește o singură dată și numai
dacă răspunsul e da.

Patru surse, fiecare clonată și rulată **o singură dată** în `story_v2_shadow`, aceeași configurație
(sha256 `224fce16d6c8f91c`), commit `b442f20`. Recitit cu `scripts/pilot_census.py`.

| sursă | tip | h | **descoperiri** | **/oră** | grounded | grupuri | sferturi |
|---|---|---|---|---|---|---|---|
| `go ghost` | talking-head editat, EN | 0,37 | 6 | **16,2** | 5 | 6 | 1/2/1/2 |
| Turul apartamentului | vlog IRL, **română** | 0,70 | 9 | **12,9** | 6 | 10 | 0/3/5/2 |
| Jensen Huang | interviu structurat | 1,05 | 7 | 6,7† | 5 | 11 | 1/0/5/5 |
| moistcr1tikal | **Just Chatting live 3h43m** | 3,72 | 28 | **7,5** | 16 | 33 | 9/4/7/13 |
| *gateslice4h* | *gaming live 4h — baseline* | *4,00* | *23* | *5,8* | *14* | *25* | *5/7/5/8* |

**Coloana care contează este „descoperiri", nu „grupuri", și prima versiune a acestei secțiuni
raporta grupuri.** Un grup este un grup de dedupe, iar dedupe atât sparge cât și unește față de
ancorele de dedesubt: pe interviu **7 payoff-uri distincte au devenit 11 grupuri**, pe stream-ul de
patru ore 28 au devenit 25. O densitate comparată pe grupuri compară comportamentul dedupe-ului la
fel de mult ca al conținutului.

**Și „descoperiri" este tot un proxy.** Identitatea canonică este **ancora**, iar niciun `anchor_id`
nu este propagat astăzi la variante — `payoff_t`, cuantizat ca în `moment_id`, este cel mai apropiat
număr onest. Două ancore care cad în aceeași cuantă se contopesc aici; una a cărei ancoră traversează
o margine de cuantă între variante se sparge. **Un `anchor_id` stabil este condiția ca oricare din
aceste cifre să fie definitivă.**

**Motorul nu este Minecraft-shaped, și concluzia nu depinde de care metrică se alege.** Comparația
cea mai apropiată de un experiment controlat este ultimul rând contra penultimului — două stream-uri
live de aproape patru ore, unul gaming și unul nu:

| metrică | non-gaming | gaming | diferență |
|---|---|---|---|
| descoperiri cuantizate/oră | 7,5 | 5,8 | +29% |
| payoff-uri brute/oră | 9,9 | 7,0 | +41% |
| grupuri/oră | 8,9 | 6,2 | +43% |

Trei metrici, aceeași direcție. Rata de grounding este aceeași (58% față de 56%). Româna nu este o
barieră. Niciuna dintre surse nu a colapsat.

Arhetipurile nu sunt forțate în forme de gaming: pe `go ghost`, `HOT_TAKE` a căzut exact pe
propoziția care dă titlul videoclipului, alături de `REVEAL`, `CLUTCH`, `CALLBACK` și `STORY`.

† **Interviul este o rulare cenzurată, păstrată ca artefact al modului de eșec.** `anchors#0` acoperă
0-2240s — primele 57% din sursă — și a produs **zero** ancore, înregistrat ca `unusable`,
`parsed=False`. Raportarea corectă: 7 payoff-uri distincte observate; 6,7/oră pe durata întreagă,
rulare cenzurată; 15,5/oră în regiunea efectiv procesată, pur descriptiv; densitatea întregii surse
**necunoscută**. Nu se numește „plafon inferior": asta ar presupune că recuperarea adaugă rezultate
fără să schimbe chunk-ul reușit, iar o rerulare completă nu garantează asta. Nu se rerulează ad-hoc
pentru o cifră mai frumoasă — S7b a reparat recuperarea per chunk; validarea cere încă o clonă nouă.

**Rata de eșec operațional este ea însăși un rezultat.** Pe cele trei surse terminate înainte de
moistcr1tikal: **1 din 5 cereri de chunk `anchors` a venit neutilizabilă, 20%**; socotind și
`promises`, 1 din 10 cereri structurate. Nu sunt eșecuri de generalizare și nu se citesc ca atare —
dar înseamnă că o singură rulare per sursă nu ajunge pentru o cifră pe care se ia o decizie.

**Retrase explicit, fiindcă au fost scrise înainte de a fi verificate:** „interviul are 11 momente"
(are 7 descoperiri distincte), „interviul are 6 valid" (are 4 payoff-uri cu cel puțin o variantă
validă), și cifra de 1 eșec din 9 chunk-uri (este 1 din 5 pe cererile de `anchors`).

**Ce NU a demonstrat pilotul.** Că v2 produce clipuri mai bune. Densitatea, grounding-ul și
distribuția sunt diagnostice: motorul își poate activa bugetul story și poate produce impecabil 33 de
payoff-uri proaste. `Precision@10` pe momente considerate exportabile de om, v2 contra legacy în
review orb, procentul self-contained și corectitudinea marginilor cer judecată umană și rămân
nemăsurate. Acesta este restul gate-ului, și el este Batch 10.

**Condițiile rulării, ca să nu fie citite ca mai mult decât sunt.** Ollama a picat pe conexiune la
fiecare apel, înainte să vadă promptul, deci motorul efectiv a fost OpenAI la promises, anchors și
judge. Cohorta se numește „reasoning v2, fallback OpenAI" — nu este o evaluare a lui Ollama și nu
este independentă de provider.

## 14d. Review orb — cum se rulează, 2026-08-22

Pagina: `/clipper-review`. Sesiunea trăiește pe server; browserul ține doar id-ul,
ca un reload să nu abandoneze o sesiune la jumătate.

**O sesiune per proiect, alegerea operatorului.** Recomandarea de la review era un
singur shuffle global, stratificat, fiindcă amestecarea surselor împiedică
evaluatorul să prindă ritmul unei surse și să-l ducă în clipul următor. Per proiect
se pierde exact atâta: clipurile unei surse vin la rând, deci un efect de context
între ele **nu poate fi exclus**. În schimb se poate opri curat la granița unei
surse, ceea ce contează la ~15 clipuri pe sesiune și un evaluator singur.

**Consecința la citirea rezultatelor:** o diferență legacy/v2 măsurată în interiorul
unei surse este comparabilă; una măsurată **între** surse nu, fiindcă fiecare sesiune
are propriul context și propria oboseală. Agregarea peste proiecte se face pe
proporții per sursă, nu pe suma clipurilor.

Câte clipuri are fiecare sursă de evaluat, după re-scorare cu `shadow_rank`:

| sursă | legacy | v2 | ambele | de evaluat |
|---|---|---|---|---|
| `pilotf81b` go ghost | 8 | 8 | 1 | **15** |
| `pilotee0e` vlog RO | 8 | 8 | 2 | **14** |
| `pilot6b38` Jensen | 8 | 8 | 1 | **15** |

Diferența v2 față de legacy la re-rulare: 7 din 8 pe `go ghost`, 6 pe vlog, 7 pe
interviu. Acordul între board-uri este de una-două poziții din opt.

## 14e. Ce a găsit review-ul orb, 2026-08-22

Două sesiuni complete, câte una per sursă, pe export-uri întregi.

| sursă | board | văzute | da | nu | nesigur | precizie | margini ok |
|---|---|---|---|---|---|---|---|
| go ghost | legacy | 8 | 5 | 2 | 1 | 0,71 | 2 |
| go ghost | **v2** | 8 | 6 | **0** | 2 | **1,00** | **5** |
| vlog RO | legacy | 8 | 0 | 4 | 4 | **0,00** | 3 |
| vlog RO | **v2** | 8 | 1 | 2 | 5 | **0,33** | 2 |
| Jensen | legacy | 8 | 4 | 3 | 1 | 0,57 | **5** |
| Jensen | v2 | 8 | 4 | 3 | 1 | 0,57 | 1 |

**v2 conduce pe două surse din trei și este la egalitate pe a treia** — pe Jensen
identic la verdict, dar mai slab la margini, 1 față de 5. Eșantionul rămâne minuscul:
8 clipuri per board, iar la go ghost un singur răspuns schimbat mută precizia cu 15
puncte. Semnal, nu dovadă.

### Constatarea care contează mai mult decât comparația

**Pe vlogul românesc, 13 din 14 clipuri au probleme tehnice, iar cauza nu e selecția.**

Sursa e 4K (3840×2160), un tur de apartament filmat din mână.

**CORECȚIE, 2026-08-22.** Prima versiune a acestei secțiuni a numit cauza „crop fix pe
proiect", citind `layout_plan.face_rect`, identic pe toate clipurile. **Este fals.**
`layout_plan` este doar planul static de rezervă. Toate cele patru proiecte pilot au
`dynamic_edit=true`, iar când planul dinamic există workerul cheamă rendererul dinamic,
nu pe cel static — `clipper_render_jobs.py:201`. Fișierele `.cmd.txt` ale export-urilor
arată crop-uri care se schimbă de zeci de ori într-un clip: pe un clip Jensen, x-ul trece
prin 1623 → 2139 → 1623 → 1667 → 1751 → 1515 → 2159 → 2067.

Deci: `face_rect` fix **nu este crop-ul care a produs aceste export-uri**, `keyframes=[]`
pe split-screen **nu explică imaginile**, iar conectarea keyframe-urilor statice **nu ar
fi schimbat nimic** cât timp planul dinamic e activ. Diagnosticul a măsurat planul, nu
rezultatul — aceeași eroare ca sesiunea de review servită din preview-uri.

**Cauza reală.** `dynamic_edit` este construit explicit pentru **facecam + gameplay** și
este aplicat nediferențiat pe interviu, vlog și talking-head. Regula lui — cea mai mare
față din fiecare eșantion, redusă la un singur cluster dominant — este corectă pe un
stream de gaming, unde există o singură față într-un colț fix. Pe un interviu cu doi
vorbitori alege alternativ; pe un vlog în care camera se plimbă urmărește orice seamănă
cu o față; pe un Just Chatting urmărește **fețele din videoclipul reacționat**, ceea ce
evaluatorul a observat direct: „nu ia webcam pe toate video-urile, de acum ia fețele din
video-ul la care se uită".

Cadrele extrase rămân valabile ca dovadă a rezultatului: perdea pe tot ecranul, balcon
gol, toc de ușă, pat cu pisică; iar pe moistcr1tikal o pagină YouTube întreagă cu bara de
căutare, like/dislike, SHARE și SAVE în cadru, fără creator.

**A doua corecție.** Am raportat și că `faces.json` are zero fețe pe moistcr1tikal, deci
că detecția eșuează tăcut. Și asta e fals — citisem greșit formatul. Structura reală este
`{samples: [{t, boxes}], times: [...]}`, iar sursele au 1285, 1595 și 737 de eșantioane
cu cel puțin o față. **Detecția funcționează; direcționarea ei nu.**

### Același defect pe interviu, și formularea exactă a evaluatorului

Jensen: 12 din 15 clipuri cu problemă tehnică, aceeași cauză — 3840×2160, clasificat
`interview` cu încredere **0,418**, `split_screen` la 15 din 15, și **un singur
`face_rect` pentru tot proiectul**: `{x: 1312, w: 392}`, adică 392 px dintr-un cadru
de 3840.

Cadrele extrase arată de ce cifra e 12 din 15 și nu 13 din 14 ca la vlog:

> „când apare altceva decât fețele lor pe ecran se vede prost fiindcă e tăiat"

Pe un interviu camera stă mare parte din timp pe vorbitori, deci crop-ul fix nimerește;
încadrarea pe fețe este chiar bună. **Fiecare cutaway este însă distrus** — primul cadru
inspectat este o diagramă „8 MILLION PIXELS" tăiată la o fâșie de grilă fără sens. Pe
vlog camera nu stă aproape niciodată pe față, deci aproape totul se distruge.

**Cauza unică, după corecția de mai sus:** crop-ul **se mișcă**, dar îl mișcă o regulă
scrisă pentru facecam + gameplay. Nu este o eroare de clasificare și nu este un crop
înghețat — este urmărirea celui mai mare cluster de fețe, aplicată unui material în care
cel mai mare cluster de fețe nu este subiectul.

Nota de la go ghost, singura sursă fără acest defect, este de alt ordin de mărime:

> „cu câteva milisecunde mai devreme decât trebuia se termină"

**Ce înseamnă asta pentru citirea preciziei.** Pe vlog și pe Jensen, `worth_exporting`
măsoară un amestec de selecție și de randare, iar evaluatorul a spus explicit că prin
„problemă tehnică" înțelege „e editat prost". Singura sursă pe care precizia măsoară
curat selecția este go ghost, unde layout-ul este `talking_head` și defectul nu apare —
și acolo v2 are 1,00 față de 0,71.


## 14b. Stare de aprobare

Batch 0 și Batch 1 au **undă verde**, cu ajustările din §13.17, §13.23 și §13.24 deja integrate.
Ordinea de rulare la implementare: mai întâi baseline-ul complet, apoi testele țintite, apoi întreaga
suită Clipper după fiecare batch. Batch-urile 2–10 rămân neaprobate până când Batch 0 și 1 sunt
livrate și verificate.

## 15. Definition of Done

Reasoning v2 este gata de activare implicită numai când toate condițiile sunt adevărate:

- setarea este controlabilă și observabilă end-to-end;
- fiecare candidat poate explica prin trace sursa hook/context/payoff/reaction;
- metricile corespund boundary-ului final;
- întreg stream-ul are coverage temporal și nicio coadă nu este tăiată tăcut;
- judge-ul evaluează momente diverse, nu variante redundante;
- board-ul conține momentele selectate explicit de judge, plus — numai după depășirea plafonului de
  runde — backfill euristic **declarat** din `not_evaluated`, marcat ca atare în trace; fallback-ul
  la eșec de judge rămâne atomic;
- auto-exportul nu intră ca verdict pozitiv;
- learned ranker câștigă pe proiecte nevăzute, la LOPO, cu interval de încredere care nu include
  zero;
- toate testele de reliability, restart și retry rămân verzi;
- legacy poate fi reactivat imediat;
- review-ul orb confirmă o îmbunătățire față de baseline pe corpus multi-gen.

Până atunci, `story_v2` se tratează ca sistem experimental măsurat, nu ca promisiune de calitate
bazată pe existența unui LLM în pipeline.
