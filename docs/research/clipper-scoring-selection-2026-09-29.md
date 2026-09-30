# Clipper — scorarea și alegerea clipurilor: ce spune cercetarea

Cercetare din **29 septembrie 2026**, cu conectorul Firecrawl (articole științifice, documentații, pagini de produs,
teste independente). Continuă [`clipping-apps-survey-2026-09-29.md`](clipping-apps-survey-2026-09-29.md), care a
acoperit semnalele și încadrarea; aici e doar partea de **scorare, judge LLM, ranker și selecție**.

| etichetă | înseamnă |
|---|---|
| **[OBS]** | spus public de un producător sau de un recenzent; nemăsurat de noi — o etichetă, nu o măsurătoare |
| **[LIT]** | articol publicat (arXiv, conferințe); cifrele sunt ale autorilor, pe datele lor, nu pe ale noastre |
| **[ASM]** | deducția noastră; se verifică înainte de a construi pe ea |
| **[CF]** | starea ClipForge, verificată în cod pe 29.09, cu fișierul și linia |

---

## 1. Pe scurt

1. **Judge-ul vede momentele în ordinea scorului euristic** [CF], iar literatura arată că modelele care ordonează
   o listă sunt sensibile la ordinea în care o primesc [LIT]. Cu 7 ordini amestecate și agregare, alegerea top-1
   a crescut de la ~86% la 89–91% chiar la modelele din 2026. Riscul concret: judge-ul, pus să corecteze
   euristica, poate doar să o confirme. Se poate măsura ieftin (SL1–SL2) și repara ieftin (SL3–SL4).
2. **Reproductibil nu înseamnă valid.** Cache-ul S7c face ca judge-ul să răspundă la fel la reluare; asta e
   fiabilitate. Un studiu pe 21 de judge-uri a găsit fiabilitate test-retest >0,95 *împreună cu* bias de poziție
   >0,10 la două judge-uri folosite în producție [LIT].
3. **„Bun pentru editor" și „bun pentru public" sunt ținte diferite.** Evaluatorii umani n-au ghicit ce se
   re-vizionează mai bine decât întâmplarea, iar GPT-4o zero-shot a fost cât o bază „după poziție"; un model mic,
   antrenat pe etichete din domeniu, a avut ~4× AP [LIT]. Deci ținta „public" se evaluează separat, pe etichete
   de la public (Most Replayed, clipurile spectatorilor), nu prin judge.
4. **Scorurile de viralitate ale concurenței sunt comprimate și prezic slab** [OBS]: 82% din 7.101 clipuri OpusClip
   au primit ≥80; OpusClip însuși spune că la gaming scorul „corelează slab". Confirmă rangul relativ al
   ClipForge; nu există o țintă absolută de copiat.
5. **Ranker-ul are patru probleme de design** [CF]:
   - poarta de activare se măsoară pe setul de antrenare, deși decizia D-6 cere held-out;
   - e pointwise, deși D-7 descrie un ranker pe perechi;
   - vede doar 22 de trăsături de suprafață, fără sub-scoruri și fără verdictul judge-ului;
   - primește etichete numai pentru clipurile ajunse pe board (bias de selecție).
6. **Feedbackul uman pe perechi** (§26, neconstruit) e mai discriminativ decât notele absolute. Cu sortare activă
   costă ~n·log n comparații în loc de n² [LIT].
7. **Metricile de după postare** se normalizează pe canal și pe durată. Urmăritorii și trendurile domină
   popularitatea, iar durata distorsionează timpul de vizionare [LIT].

Planul SL1–SL12 e în §8. Prefixul **SL** evită confuzia cu loturile R0–R8, cu SC (subtitrările sursei) și cu CA.

---

## 2. Cum funcționează azi [CF]

| etapă | fișier | ce face | fapte măsurate deja |
|---|---|---|---|
| scor euristic | `scoring.py`, `scoring_profiles.py` | 16 sub-scoruri 0–100, sumă ponderată cu 10 rânduri de ponderi scrise de mână; `eligibility` separat | pe 1073 de candidați, clasamentul avea 16,22 puncte de „spread" în total; `platform_fit` a fost pus la 0 fiindcă dădea 2,10 din ele |
| pool pentru judge | `candidate_groups.py` | momente, nu variante; buget 80: 50% euristic, 25% story, restul acoperire | înainte: judge-ul vedea 80 din 909 candidați (9%) și un singur candidat story din 38 |
| judge | `llm_judge.py`, `llm_engine.py` | un singur apel ordonează tot pool-ul: „dacă doar {want} pot fi publicate, care?"; întoarce top min(n, 2×want); 3 perspective + 11 motive de respingere; temperatură 0,2 (`llm_engine.py:37`); max 80 de clipuri × 900 de caractere (`:48`) | scorarea absolută anterioară a dat 8 valori distincte pe 46 de candidați — de aceea s-a trecut la ordonare forțată |
| din verdict în scor | `llm_judge.apply_ranking` (`:173`) | poziția → 100…0; perspectivele mută clipul local; −12 per motiv de respingere; blend 0,7 cu euristica (`clipper_llm_weight`, `config.py:308`) | — |
| board | `selection.py` | board-ul vine numai din momentele `selected`; cel mult 2 runde (`:55`); completare marcată | 7 din 10 câștigători erau `not_evaluated` înainte de regula asta |
| ranker învățat | `ranker.py` | regresie logistică `lr-1`, 22 de trăsături, L2 1e-3; etichete exportat 1,0 / aprobat 0,75 / respins 0; activ numai dacă NDCG@5 > euristica | inactiv: nicio etichetă manuală suficientă |
| feedback | `feedback.py`, `blind_review.py` | doar acțiunile `manual` etichetează; review orb pe proiect, ~15 clipuri pe sesiune, un evaluator | când s-a introdus `origin`: 61 din 69 de rânduri erau exporturi, zero aprobări sau respingeri — ranker-ul era la un pas de activare fără niciun exemplu negativ |

**Ordinea pe care o vede judge-ul** [CF]: `build_groups` sortează după scorul euristic (`candidate_groups.py:131`),
`build_shortlist` pune întâi cota euristică sortată descrescător (`:258`), apoi story, apoi acoperirea, iar
`judge_prompt` numerotează clipurile în ordinea pool-ului. Deci id-ul 0 e liderul euristic, iar id-ul coincide cu
poziția în prompt.

---

## 3. Judge-ul LLM

### 3.1 Ordinea contează [LIT]

| sursă | rezultat |
|---|---|
| Permutation self-consistency (arXiv:2310.07712) | amestecă lista de mai multe ori, agregă clasamentele; +7–18% (GPT-3.5) și +8–16% (LLaMA-2 70B) față de un singur apel |
| PCFJudge (arXiv:2603.20562) | 7 permutări: top-1 de la 86,00% la 91,33% (GPT-5.4) și de la 86,33% la 89,67% (Claude Sonnet 4.6) |
| Ranked by Position (arXiv:2607.24869) | doar reordonând candidații, un element irelevant ajunge în top-5 în până la 57% din cazuri (50 de ordini); stabilitatea la permutări prezice vulnerabilitatea |
| Position bias & preference consistency (arXiv:2608.03091) | măsoară instabilitatea preferințelor pe perechi, incoerența globală și consistența listei finale; reducerea bias-ului de expunere nu le repară automat |
| Lost in the Middle (arXiv:2307.03172) | modelele folosesc cel mai bine informația de la începutul și de la sfârșitul unui context lung; la mijloc performanța scade semnificativ |

[ASM] La noi pool-ul are până la 80 × ~900 de caractere, deci o listă lungă, iar primele 40 de poziții sunt exact
favoriții euristicii. Dacă judge-ul favorizează începutul listei, efectul arată ca „judge-ul e de acord cu
euristica" și nu se poate distinge de un acord real fără o permutare.

### 3.2 Reproductibil nu înseamnă valid [LIT]

- „Reliability without Validity" (arXiv:2606.19544): 21 de judge-uri, ~541.000 de judecăți. Acordul brut
  supraestimează: între acord exact și kappa Cohen sunt 33–41 de puncte procentuale pe MT-Bench. Clasamentul
  judge-urilor se mută cu până la 14 poziții între benchmark-uri. Test-retest >0,95 coexistă cu bias de poziție >0,10.
- „Judging the Judges" (arXiv:2604.23178): bias-ul de **stil** (preferința pentru markdown) e cel mai mare
  (0,10–0,76), mai mare decât cel de poziție. Debiasing-ul a adus până la +11,5 puncte.
- MT-Bench (arXiv:2306.05685): un judge puternic ajunge la >80% acord cu oamenii, cât acordul dintre oameni; are
  bias-uri de poziție, de lungime și de auto-preferință.

[CF] S7c reia verdictul exact pentru aceeași întrebare. E corect pentru cost și pentru reluare, dar îngheață și
bias-ul acelui singur eșantion.

### 3.3 Pointwise, pairwise, listwise, setwise [LIT]

- Pointwise (fiecare clip notat singur): ieftin, dar slab și comprimat — ce a măsurat și ClipForge (8 valori pe 46).
- Pairwise (PRP, arXiv:2306.17563): cel mai robust pentru modele moderate, dar costă O(n²); are variante liniare
  (fereastră glisantă).
- Setwise (arXiv:2310.09497): compromis între eficiență și calitate; RefRank (arXiv:2506.11452): comparație
  față de o referință comună, O(n).
- Listwise (ce avem): bun la calitate, dar sensibil la ordine.

Concluzie [ASM]: trecerea de la pointwise la ordonare forțată a fost corectă. Pasul următor nu e altă formă de
prompt, ci **robustețe la ordine**.

---

## 4. Ce optimizăm: trei ținte diferite

| țintă | ce înseamnă | cine o poate judeca | dovezi |
|---|---|---|---|
| **editorială** | clipul se înțelege singur, are hook, payoff, final | omul; judge-ul LLM aproximează omul | MT-Bench: judge ≈ acordul între oameni (pe conversații, nu pe clipuri) |
| **public** | ce re-vizionează, ce urmăresc până la capăt | numai datele de la public | YTMR500 (arXiv:2309.06102): ~300 de evaluatori, P@1 **9,6%** vs 10% aleator, acord α = −0,017. Rhapsody (arXiv:2505.19429): 13K podcasturi, etichete din Most Replayed — GPT-4o zero-shot AP 0,032 ≈ baza „după poziție" 0,031; Gemini 0,037–0,040; Llama-3.2-1B cu ~6M parametri antrenați 0,134–0,153 (text + audio HuBERT cel mai bun) |
| **acoperire** | câți ajung să-l vadă | nimeni, din conținut | TikTok (arXiv:2111.02452): numărul de urmăritori e cel mai puternic predictor. WEBSHORTS (arXiv:2605.18653): viralitatea depinde de trenduri externe. arXiv:2507.19863: modelele de popularitate se degradează când se schimbă trendurile |

Consecințe:

- Judge-ul rămâne unealta pentru ținta **editorială**. Literatura are dovezi pentru acest rol doar în alte
  domenii; la noi acordul cu omul se măsoară prin SL9.
- Ținta **public** are nevoie de etichete de la public. Surse gratuite: Most Replayed și clipurile spectatorilor
  din Twitch (CA2, CA4 din sondajul anterior). Most Replayed apare de obicei de la ~50K de vizualizări [LIT
  Rhapsody], deci pe VOD-urile streamerilor mari probabil există.
- Ținta **acoperire** nu se promite și nu se scorează. E poziția din iulie, acum cu dovezi.
- [ASM] **Scurgere**: dacă Most Replayed devine trăsătură (CA2), nu mai poate fi și etichetă de evaluare pentru
  același scor. Se păstrează întâi ca etichetă de evaluare; trăsătura vine abia după, cu o sursă de evaluare
  separată (clipurile spectatorilor sau metrici de după postare).
- [LIT] Semnalele implicite pot ține loc de etichetare manuală: durata videoclipurilor create de utilizatori
  (arXiv:1903.00859). Un detector se poate adapta și la gustul unui singur om, din istoricul lui de highlight-uri
  (arXiv:2007.09598). Exact situația ClipForge: un singur om aprobă.

Protocolul Rhapsody e ușor de reprodus la noi:
- VOD-ul se împarte în 100 de segmente, iar vârfurile curbei sunt etichetele pozitive;
- se compară board-ul euristic, board-ul v2 și o selecție aleatoare, prin hit rate / precizie / AP;
- numitorul se tipărește primul.

---

## 5. Ranker-ul și etichetele

### 5.1 Poarta de activare

[CF] `ranker.py:186`: „Train-set metrics, not held-out: with 40-200 rows a holdout split is mostly noise". Decizia
D-6 (`plans/ai-stream-clipper-decisions.md`) cere însă „held-out NDCG@5". Cu 22 de trăsături și ~40 de rânduri,
o regresie logistică bate aproape sigur euristica **pe datele pe care a învățat** — deci poarta nu dovedește nimic.

[LIT] La date corelate, validarea încrucișată aleatoare supraestimează: în modelele ecologice, cu până la 0,16 AUC
(arXiv:2502.03480); soluția standard e lăsarea deoparte a unui grup întreg. [ASM] La noi grupul natural e
**proiectul** (o sursă): clipurile aceluiași stream seamănă între ele.

**SL6**: poarta = NDCG@5 cu *leave-one-project-out*:
- se raportează pe fiecare proiect, cu numitorul (câte clipuri etichetate are fiecare);
- ranker-ul se activează doar dacă bate euristica pe majoritatea proiectelor, nu doar în medie.

Asta respectă D-6 fără problema „holdout-ului zgomotos": fiecare rând e testat exact o dată.

### 5.2 Pe perechi, în interiorul proiectului

[CF] D-7 descrie „a pairwise logistic ranker over ~20 features"; codul e pointwise, cu etichete 1,0 / 0,75 / 0.

[ASM] Formularea pe perechi (aprobat vs respins **din același proiect**, logistic pe diferența trăsăturilor) scoate
nivelul general al stream-ului: un stream liniștit și unul agitat nu mai concurează pe aceeași scală. Exact
problema pe care `selection.py` a rezolvat-o pentru judge — „două scale care nu se compară".

### 5.3 Ce vede ranker-ul

[CF] `FEATURE_ORDER` (`ranker.py:41`) are 22 de trăsături de suprafață: durată, poziție, cuvinte, RMS, mișcare,
tăieturi, fețe… Nu vede cele 16 sub-scoruri, `context_debt`, latența hook-ului, payoff-ul sau verdictul judge-ului.

[ASM] Cu 40 de etichete, un model peste **sub-scoruri + perspectivele judge-ului** (~20 de intrări care conțin
deja cunoștințele puse de mână) învață mai mult din aceleași date decât unul peste trăsături brute. Practic, ar
învăța ponderile profilului în locul celor scrise de mână. Ambele variante se compară sub SL6. Trăsăturile care
pot lipsi (chat, heatmap) au nevoie de un flag de disponibilitate (capcana `0.0`).

### 5.4 Bias de selecție

[LIT] Feedbackul implicit e distorsionat de poziție și de ce i s-a arătat utilizatorului (Joachims et al.,
arXiv:1608.04468). Remediile standard: randomizare pe o parte din trafic, ponderare inversă cu propensitatea.

[CF] Omul etichetează doar ce ajunge pe board, deci ranker-ul învață „care dintre favoriți", niciodată „ce a ratat
board-ul". **SL8**: în fiecare sesiune de review orb intră 1–2 candidați din afara board-ului
(`not_selected_in_judged_pool` sau aleși aleator dintre cei eligibili), marcați intern și neanunțați. Costă
câteva clipuri pe sesiune și dă singura măsură a *recall*-ului.

### 5.5 Feedback uman pe perechi (§26)

[LIT] Comparația pe perechi e mai discriminativă decât nota absolută și evită ca fiecare om să-și interpreteze
altfel scala (arXiv:2010.00370). Costul se reduce prin eșantionare activă:
- sortarea repetată e o strategie simplă și eficientă sub modelul Bradley–Terry (arXiv:1502.05556);
- EZ-Sort a redus comparațiile umane cu 90,5% față de toate perechile (arXiv:2508.21550).

**SL9**: în review, „pe care l-ai posta?" între două clipuri din **aceeași** sursă:
- perechile se aleg prin MergeSort (~n·log₂n; pentru 12 clipuri, cel mult 33 de comparații în loc de 66 de perechi),
  cu scoruri Bradley–Terry la final;
- același set dă și acordul om–judge, raportat ca kappa, nu ca procent brut (§3.2).

### 5.6 Variante pe moment și granițe (§27)

[LIT PodReels, arXiv:2311.05867] Creatorii de teasere au respins clipuirea complet automată: „prea multe
variante, de calitate slabă, greu de editat". Unealta lor propune **3 ferestre** pe criterii alese de om, iar
omul alege și rafinează — 59% din timp și 44% din efortul mental față de bază. Teaserele bune: un singur moment,
~30 s (11–51 s, medie 27,2 s pe 30 de exemple), hook potrivit genului.

[CF] Story engine produce deja 2–4 tăieturi pe moment; judge-ul alege momentul, scorul alege tăietura. **SL10**:
tăieturile aceluiași moment, arătate una lângă alta în editor. Alegerea omului e o preferință pe perechi despre
**granițe** — datele pentru §27, astăzi neconstruit.

---

## 6. Metricile de după postare (rafinează CA11)

- **Durata distorsionează** [LIT]: timpul de vizionare e confundat cu durata clipului (D2Q, Kuaishou,
  arXiv:2206.06003). Corecția: comparație cu distribuția clipurilor de durată apropiată, ca percentilă, nu ca
  valoare brută (arXiv:2508.11086).
- **Canalul domină** [LIT arXiv:2111.02452]: urmăritorii sunt predictorul cel mai puternic, deci eticheta e
  relativă la istoricul canalului.
- **Trendurile se schimbă** [LIT arXiv:2507.19863]: validarea se face în ordinea timpului (antrenare pe vechi,
  test pe nou), nu amestecat.
- **Protocolul de test** [OBS Loopdesk]:
  - întrebarea, cohorta și rezultatul se fixează dinainte;
  - o parte din postări sunt alese aleator dintre clipurile eligibile, ca bază;
  - editarea e aceeași indiferent de scor;
  - se raportează Spearman și rata de succes a primelor alegeri;
  - regula de oprire e fixată dinainte.
- [OBS] Testele recenzenților pe OpusClip (30 de clipuri): 80+ vs <50 ≈ 2,3× vizualizări medii, dar cel mai bun
  clip avea 64 și cel mai slab 87. Eșantioane mici, fără bază aleatoare — exact ce protocolul de mai sus evită.

---

## 7. Rubrica judge-ului — ce confirmă sursele

| sursă | ce spune | la noi [CF] |
|---|---|---|
| PodReels [LIT] | hook potrivit genului (amuzant la comedie, emoționant la society); te pui în locul unui ascultător nou | rubrici pe arhetip (`ARCHETYPE_SHAPE`); perspectiva `cold_viewer` |
| StreamLadder [OBS] | energie audio, acțiune vizuală, intensitatea reacției, cât de bine stă singur momentul | sub-scorurile `audio_energy`, `visual_energy`, `reaction`, `context_completeness` |
| OpusClip [OBS] | hook, flow, value, trend | hook / payoff / context; fără trend, deliberat |
| HIVE (arXiv:2507.02790) [LIT] | editarea descompusă: detecția momentului, alegerea începutului/finalului, tăierea a ce e irelevant | anchors → `candidate_boundaries` / `boundary_completion` → `dead_air` |

Nu e nevoie de o rubrică nouă. Rămâne un risc de formă: stilul e bias-ul cel mai mare al judge-urilor
[LIT arXiv:2604.23178], iar pachetele noastre diferă ca lungime și structură [CF `_render_packet`]:
- un moment story are PAYOFF cu citat, linii NEEDS și metrici;
- unul fără story are doar „PAYOFF: none identified" și transcriptul.

O parte din diferență e informație reală; o parte poate fi preferință pentru pachetul mai lung [ASM]. Se verifică
în SL2 cu o variantă în plus: același pool, cu toate pachetele aduse la aceleași titluri.

---

## 8. Planul propus

Efort: **S** = zile, **M** = o săptămână sau două, **L** = mai mult.

| ID | ce | efort | poarta / decizia |
|---|---|---|---|
| SL1 | din rundele de judge stocate: distribuția pozițiilor (id-urilor) celor aleși, pe categorii (euristic / story / acoperire) | S, fără apeluri LLM | nu separă bias-ul de acordul real (poziția = calitatea euristică); arată doar dacă merită SL2 |
| SL2 | același pool în 3 ordini (euristică, inversă, aleatoare cu seed) + o variantă cu pachete uniformizate, pe 3 surse stocate; suprapunerea selecției (Jaccard@want), Kendall τ, instabilitatea pe perechi | S–M, ~4× costul unei runde | prag ales dinainte, nu măsurat: suprapunere < 0,8 → SL4 |
| SL3 | ordinea pool-ului amestecată cu seed, seed-ul scris în `reasoning_trace` | S | trece prin shadow + review orb, ca orice schimbare de selecție |
| SL4 | K = 3–5 permutări, agregare Borda/Kemeny, doar pe runda finală sau la limita de tăiere | M | numai dacă SL2 arată instabilitate; costul K× e scris în trace |
| SL5 | evaluare offline pe etichete de la public (Most Replayed, clipurile spectatorilor): board euristic vs v2 vs aleator | M, după CA2/CA4 | numitorul întâi; `unavailable` pentru sursele fără etichete |
| SL6 | poarta ranker-ului → leave-one-project-out | S | respectă D-6; raport pe fiecare proiect |
| SL7 | ranker pe perechi în proiect; intrări = sub-scoruri + verdict (vs cele 22 de trăsături) | M | comparat sub SL6; ≥40 de etichete manuale |
| SL8 | felie de explorare în review-ul orb (1–2 candidați din afara board-ului pe sesiune) | S | etichetele din afara board-ului apar distinct în raport |
| SL9 | review pe perechi cu sortare activă + Bradley–Terry; acord om–judge ca kappa | M | — |
| SL10 | tăieturile unui moment arătate alăturat; alegerea = preferință de granițe (§27) | M | — |
| SL11 | normalizarea metricilor de după postare: percentilă pe durată, pe canal, fereastră fixă, felie aleatoare | S, peste CA11 | nicio etichetă din vizualizări brute |
| SL12 | model mic în domeniu (tip Rhapsody) pe Most Replayed + audio, pe GPU-ul local | L | numai după SL5, care stabilește baza de comparat |

Ordinea recomandată: SL1 → SL2 (decide) → SL3/SL4. SL6 și SL8 **acum**, fiindcă sunt ieftine și contează doar dacă
există înainte să se adune etichetele. SL5 după ce CA2/CA4 salvează datele. SL7 la 40 de etichete. SL9–SL10 odată
cu munca de UI. SL12 la sfârșit.

## 9. Ce nu recomand

| nu | de ce |
|---|---|
| un „procent de viralitate" calibrat | nimeni din piață nu îl are validat; ținta „acoperire" nu se poate prezice din conținut |
| înlocuirea judge-ului acum cu un model antrenat | nu avem încă etichete de public; SL5 întâi, SL12 după |
| rescrierea diversității (DPP/MMR) | literatura există (SeqDPP), dar nu avem un defect măsurat la diversitatea actuală |
| trăsături de trend | tot fără sursă legitimă (WEBSHORTS arată că ar ajuta, dar cere context web în timp real) |
| Most Replayed și ca trăsătură, și ca etichetă pe același scor | scurgere; evaluarea ar măsura scorul cu propria lui intrare |

---

## Surse

Judge LLM și ranking:
- Found in the Middle: Permutation Self-Consistency — https://arxiv.org/abs/2310.07712
- Permutation-Consensus Listwise Judging (PCFJudge) — https://arxiv.org/abs/2603.20562
- Ranked by Position: Order Sensitivity as an Attack Surface — https://arxiv.org/abs/2607.24869
- Position Bias Undermines Preference Consistency in Listwise Reranking — https://arxiv.org/abs/2608.03091
- Lost in the Middle: How Language Models Use Long Contexts — https://arxiv.org/abs/2307.03172
- Reliability without Validity (LLM-as-a-Judge) — https://arxiv.org/abs/2606.19544
- Judging the Judges: Bias Mitigation Strategies — https://arxiv.org/abs/2604.23178
- Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena — https://arxiv.org/abs/2306.05685
- Pairwise Ranking Prompting — https://arxiv.org/abs/2306.17563 · Setwise — https://arxiv.org/abs/2310.09497 ·
  RefRank — https://arxiv.org/abs/2506.11452

Ce prezice publicul:
- Can we predict the Most Replayed data? (YTMR500) — https://arxiv.org/abs/2309.06102
- Rhapsody: highlight detection in podcasts — https://arxiv.org/abs/2505.19429
- Indicators of Virality in TikTok Short Videos — https://arxiv.org/abs/2111.02452
- Will It Go Viral? (WEBSHORTS) — https://arxiv.org/abs/2605.18653
- Anchoring Trends: popularity prediction drift — https://arxiv.org/abs/2507.19863
- Deconfounding Duration Bias in Watch-time Prediction (D2Q) — https://arxiv.org/abs/2206.06003
- Relative Advantage Debiasing for Watch-Time Prediction — https://arxiv.org/abs/2508.11086

Etichete, validare și preferințe:
- Unbiased Learning-to-Rank with Biased Feedback — https://arxiv.org/abs/1608.04468
- Unbiased cross-validation of spatio-temporal models — https://arxiv.org/abs/2502.03480
- Boosting Pair Comparison (PC vs ACR) — https://arxiv.org/abs/2010.00370
- Just Sort It! Active preference learning — https://arxiv.org/abs/1502.05556 · EZ-Sort — https://arxiv.org/abs/2508.21550
- Less is More: highlight detection from video duration — https://arxiv.org/abs/1903.00859
- Adaptive highlight detection from user history — https://arxiv.org/abs/2007.09598

Selecție și editare:
- PodReels: Human-AI Co-Creation of Video Podcast Teasers — https://arxiv.org/abs/2311.05867
- HIVE: From Long Videos to Engaging Clips — https://arxiv.org/abs/2507.02790
- Sequential DPP for video summarization — https://arxiv.org/abs/1807.10957

Produse și teste independente (consultate 29.09.2026):
- StreamLadder Virality Score — https://www.streamladder.com/clipgpt/virality-score
- OpusClip Virality Score — https://help.opus.pro/docs/article/virality-score
- ScaleReach, acuratețea scorului OpusClip — https://www.scalereach.ai/blog/how-accurate-is-the-opus-clip-virality-score ·
  https://www.scalereach.ai/blog/opus-clip-review/
- Loopdesk, cum se evaluează un scor de viralitate — https://loopdesk.ai/blog/ai-virality-scores-explained
- Techpresso, recenzie OpusClip — https://academy.techpresso.co/reviews/is-opus-clip-worth-it
