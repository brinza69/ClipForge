# Reasoning v2 — verificare în cod și întrebări deschise

- **Cine:** Claude (Opus 5), sesiune 2026-08-21
- **Ce verifică:** `docs/plans/ai-stream-clipper-reasoning-v2.md`
- **Metodă:** fiecare claim citit în sursă și, unde se putea, re-măsurat pe artefactele din
  `data/clipper/2d3375ee3420` și `data/clipper/slice4h00test`. Nicio cifră luată pe încredere.
- **Status:** document istoric. Deciziile au fost integrate în planul consolidat — vezi
  [`ai-stream-clipper-reasoning-v2.md`](ai-stream-clipper-reasoning-v2.md) §13. Două puncte de aici
  au fost **retrase**: propunerea din Î1 (judecații ocupă automat primele 80 de poziții) era greșită
  și reintroducea aceeași inversiune în sens opus, iar cifra „19" din §1 folosea o toleranță de 50 ms
  nedeclarată — valoarea corectă, cu toleranță zero, este 20.

## 1. Ce s-a confirmat

Planul e corect pe toate cele patru P0 și pe P1/P2 verificabile static. Locurile exacte:

| Claim | Unde |
|---|---|
| metrici story stale | `candidate_boundaries.py:341` — `out = dict(cand)` copiază `story`, suprascrie doar start/end. `context_debt`/`hook_latency` se produc în `candidate_proposals.py:120`, înainte de refinement |
| două payoff-uri | `candidates.py:381` — `_payoff_time()` mecanic. `payoff_position`, `setup_ratio`, `payoff_strength`, `post_payoff_energy` descriu payoff-ul mecanic. `story.payoff_t` nu e citit niciodată de `extract_features` |
| scări incompatibile | `llm_judge.py:190` — `subset = sorted(...)[:80]`; bucla de re-blend din `apply_ranking` iterează `subset`, nu `cands` |
| auto-export = label pozitiv | `clipper_render_jobs.py:303` înregistrează `"exported"` necondiționat; `feedback.py:124` — `if "exported" in decisive: return LABEL_EXPORTED`, bate orice reject ulterior |
| chunking pe caractere | `CHUNK_CHARS = 120_000`, `chunk_lines()` nu are noțiune de timp, cotă fixă `per_chunk=10` |
| fallback tăcut | `llm_select.py:340` — `if answer and answer.strip(): return answer`, fără validare JSON |
| stamp incomplet | `_anchor_stamp` = prompt + reasoning + engines + duration. Fără fingerprint de transcript, fără nume de model, fără chunk config |
| content type divergent | scoring folosește tipul local (`clipper_build.py:279`), persistarea folosește `content_type=profile` (`clipper_build.py:590`) |
| ranker pe date de train | `ranker.py:186`, comentariu explicit; `_baseline_rank` cade pe `rank_position`, atribuit după judge+dedupe |

Cifrele din §4 se reproduc: 69/15/8 și 920/38/20 exact. Context în afara ferestrei finale: 8 și 19
(planul zice 20 — diferență de prag, nu de fond). `confidence` și `payoff_strength` chiar lipsesc
din blocul `story` al candidaților.

## 2. Trei lucruri mai grave decât spune planul

**2.1 — Efectul măsurat al scărilor incompatibile.** Pe `slice4h00test`, după judge:

- 80 judecate, 840 nejudecate;
- 60 din cele 80 au primit `llm_score = 0`, deci `overall` a scăzut la ~0.3× euristic;
- maximul dintre nejudecați: **60.7**;
- rezultat: un candidat pe care judge-ul l-a respins explicit pierde în fața unuia pe care nu l-a
  văzut niciodată;
- **12 din primii 20 de pe board nu au trecut deloc prin judge**;
- **1 singur candidat story din 38 a ajuns la judge**.

Comentariul din `apply_ranking` documentează exact acest bug — a fost reparat *în interiorul*
subset-ului și lăsat intact între subset și restul pool-ului.

**2.2 — Story engine-ul nu e „greu de activat", e inaccesibil din aplicație.**
`_default_settings()` (`routers/clipper.py:65`) nu conține nici `llm_select`, nici
`reasoning_version`. `_normalise_settings` păstrează doar cheile care există deja în defaults, deci
ambele sunt **aruncate silențios** la create și la patch. Nu există drum prin UI sau API care să
pornească story_v1 per proiect; funcționează doar prin variabile globale de mediu.

**2.3 — `segment_types` nu are stamp deloc.** `clipper_build.py:78` citește artefactul orbește,
indiferent ce s-a schimbat în configurație. Batch 6 acoperă asta generic, dar cazul nu e numit.

## 3. Ce trebuie corectat în plan

**3.1 — Claim-ul „packet de judge insuficient: primele 900 caractere" e pe jumătate greșit.**
Măsurat pe cele 80 de candidate judecate: mediana packet-ului e **423 caractere**, iar **2 din 80**
ating limita de 900. Trunchierea nu e problema. Problema e a doua jumătate a propoziției — packet-ul
nu marchează payoff, context și reacție. Prima jumătate trebuie scoasă, altfel cineva va mări
`MAX_CLIP_CHARS` degeaba și va raporta o reparație inexistentă.

**3.2 — Gate-ul Batch 0 blochează tot.** 10 surse și 150 de momente etichetate manual, înainte de
orice alt batch. În repo-ul ăsta etichetarea costă sesiuni întregi (68 de rects pentru facecam, 11
surse în `source-labels.md`). Propunere: se sparge în două — trace-urile și `reasoning_run.json`
sunt ieftine și se fac imediat; golden set-ul crește incremental și blochează doar gate-urile din
Batch 5, 7 și 9.

**3.3 — Ordinea inversează cost/beneficiu.** Cele două defecte cu impact demonstrat sunt și cele mai
ieftine, dar stau în Batch 5 și Batch 7: consistența scărilor e o schimbare de câteva linii,
`origin=auto` la înregistrarea exportului la fel.

**3.4 — `scoring.py` are deja 505 linii**, peste limita de 500 din CLAUDE.md. Batch 5 îl umflă.
Split-ul trebuie să fie în plan, nu descoperit la implementare.

**3.5 — Batch 7 contrazice un comentariu măsurat** din `ranker.py:186` („metrici pe train, nu
holdout: cu 40–200 de rânduri un split e mai ales zgomot") fără să-l numească. Regula 11 din
CLAUDE.md spune că exact acele comentarii sunt sursa de adevăr. Planul trebuie să declare explicit
că îl înlocuiește și pe ce bază.

## 4. Întrebări deschise — aici chiar am nevoie de a doua opinie

**Î1. Ce se întâmplă cu cei 840 nejudecați?** §5 spune „judge atomic pe toate momentele din pool",
Batch 4 spune shortlist de 80. Regula concretă de fuziune lipsește, și e singurul loc unde greșeala
se reintroduce automat în Batch 5.

Propunerea mea: judecații ocupă pozițiile 1..80 în ordinea dată de judge, restul îi urmează în
ordine euristică pură, fără nicio blendare între cele două scări. Simplu, monoton, imposibil de
inversat. Costul: un candidat aflat pe locul 81 euristic nu mai poate urca niciodată, oricât de bun
ar fi — recall-ul devine în întregime responsabilitatea shortlist-ului stratificat.

Întrebare: accepți plafonul ăsta de recall, sau ai o schemă de fuziune care păstrează o cale de
urcare pentru nejudecați fără să compare din nou două scări brute?

**Î2. Cum au fost produse rulările story pe care se bazează §4?** Dacă `llm_select` și
`reasoning_version` sunt aruncate de `_normalise_settings`, artefactele cu 15 și 38 de candidați
story nu pot proveni din fluxul normal al aplicației. Ori au fost rulate cu env global, ori cu un
script. Contează pentru că determină dacă restul setărilor din acele rulări (min/max clip, profil,
praguri) corespund cu ce ar primi un utilizator real — adică dacă baseline-ul din Batch 0 este
comparabil cu producția sau nu.

**Î3. Din cele ~25 de P2 nevalidate, care sunt susținute de artefacte?** Le pot verifica pe toate,
dar vreau ordinea în care sunt susceptibile să fie reale. Dacă ai deja dovadă pentru unele, spune
care și pe ce sursă — restul le tratez ca ipoteze.

**Î4. Stratificarea shortlist-ului (40 euristic / 20 story / 20 coverage) e fitată sau aleasă?**
Pe `slice4h00test` cota story ar fi luat 20 din 38, adică peste jumătate din candidații story ai
sursei. Pe o sursă cu 300 de candidați story ia tot 20. Repo-ul are un istoric documentat de praguri
absolute care nu măsoară nimic pe surse cu densitate diferită. Ar trebui să fie cotă relativă, sau
ai un motiv să rămână fixă?

**Î5. Care e criteriul de oprire pentru Batch 2?** Gate-ul spune „zero metrici story stale în
corpus" și „minimum 95% coverage pentru contextul grounded la variantele balanced". Al doilea e
măsurabil doar dacă „grounded" are o definiție operațională. În plan grounding-ul e descris ca
„susținere în transcript", ceea ce e binar doar dacă există o regulă de potrivire. Care regulă?
Potrivire lexicală, prezența timestamp-ului într-un atom, sau verificare cu model? De răspunsul ăsta
depinde dacă Batch 2 e testabil sau doar plauzibil.

## 5. Ordinea pe care aș executa-o

1. contract de activare (Batch 1) — fără el nimic nu poate fi testat prin fluxul normal
2. consistența scărilor de scor — fix minimal acum, fusion completă în Batch 5
3. `origin=manual|auto|system` la feedback — oprește ranker-ul din a învăța din propriile decizii
4. trace-uri + `reasoning_run.json` (jumătatea ieftină din Batch 0)
5. `StoryEvidence` + remeasure (Batch 2)
6. coverage temporal + validare JSON înainte de fallback (Batch 3)
7. restul, ca în plan

## 6. Observație de context

Toate dovezile din §4 al planului și toate măsurătorile de mai sus vin din două surse, ambele
gaming. Tot ce se calibrează pe ele se calibrează pe gaming — aceeași limitare pe care o are deja
clasificatorul de content type (6/11 pe corpusul uniform). Nu invalidează planul; înseamnă doar că
gate-urile numerice din Batch 5 și 9 nu au voie să fie fixate pe corpusul ăsta.
