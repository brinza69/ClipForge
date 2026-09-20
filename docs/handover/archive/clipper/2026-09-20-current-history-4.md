# Arhivă Clipper — R8, S7–S8 și încadrarea pe faze

Mutat din CURRENT la 20 septembrie 2026. Istoric, inclusiv afirmații ulterior
retrase; nu este verdictul stării actuale. Conținutul secțiunilor este păstrat.

## Batch R8 — ÎNCHIS, 2 septembrie 2026

`dead_air.delivered_shots` reconstruiește secvența pe care a livrat-o FFmpeg prin intersecția
shot-urilor planificate cu intervalele păstrate. Un shot fără nicio durată rămasă dispare; unul
secționat produce două bucăți, iar joncțiunea care sare peste timp eliminat este declarată separat
ca `trim_jumps`, niciodată drept tăietură normală sau echivalentă. Agregarea păstrează aceeași regulă
ca echivalența: total complet sau `unavailable`, cu limita inferioară numită separat.

Span-urile declarate sunt acceptate numai dacă sunt pozitive, sortate, fără suprapunere și, când
fereastra este declarată, în interiorul ei. Auditul R0 trece neschimbat pe piloturi: **58 clipuri,
1.341 shot-uri și 116 tăieturi echivalente**. `trim_jumps` este `unavailable`, nu zero: cele 58 de
sidecar-uri precedă cheia `drop_spans`, deci nu demonstrează că n-a existat trim. Gate-ul R8 este
acoperit prin cazurile sintetice și prin intrarea reală a auditului.

## Batch S7 — resolverul și S7a–S7f livrate, 3 septembrie 2026

`quote_resolver.py` + `scripts/measure_quote_drift.py`. Vezi
[`docs/refs/grounding-2026-08-31.md`](../../../refs/grounding-2026-08-31.md).

**164 de afirmații pe 6 surse: bound 128 (78,0%), ambiguous 15 (9,1%), absent 21 (12,8%),
|drift| median 0,9s, p90 18,1s, max 78,1s.**

**Rezultatul contrazice așteptarea cu care a fost construit.** Driftul NU se grupează: 99 din 128 de
afirmații localizate sunt deja în raza de ~9,3s a regulii livrate, 29 nu sunt, iar prinderea lor cere
o rază de peste 20s care crește direct ambiguitatea de 9,1%. **Deci fereastra nu e răspunsul** — iar
mutarea evidentă, `_NEIGHBOURS` de la 1 la 3, ar fi urcat cifra ascunzând că un sfert dintre ratări
n-au legătură cu fereastra. De aia resolverul trebuia primul.

Cele 21 `absent` reconciliază exact măsurătoarea veche: 10 „fără potrivire" + 11 `subsequence`, o
subsecvență fiind o parafrază, nu o potrivire slabă.

**Pasul doi se schimbă pe baza datelor:** nu remăsurare după lărgire, ci **A/B de prompt cu
`atom_ids`** — `matched_by` e `timestamp` pe toate cele 164, deci modelul nu numește niciodată un
atom.

**S7a — envelope și invalidare țintită — este livrat.** `reasoning_cache.py` este contractul comun,
iar `clipper_cache.py` descrie intrările fiecărui artefact. Atoms, promises, threads, episodes,
anchors și `segment_types` poartă acum versiune de envelope, identitatea intrărilor și hash-ul
payloadului. `episodes.json` există pe disc și detectorul de ancore primește exact lista validată;
un rezultat gol nu declanșează o reconstrucție ascunsă. Fingerprintul include JSON strict și
canonic, modelele implicite rezolvate pe fiecare provider, temperatura, contextul, timeout-ul,
chunking-ul și upstream-urile relevante. Schimbarea unui semnal nu invalidează promises, iar
schimbarea transcriptului invalidează tot reasoning-ul dependent.

**Migrare intenționat incompatibilă:** fișierele reasoning bare și vechiul `{stamp, data}` sunt
refuzate o singură dată și se refac la primul score. `measure_snap_board_delta.py` refuză atoms vechi
în loc să le citească drept generația curentă. Suita completă: **1.654 passed, 2 failed**, exact cele
două 404 TikTok preexistente.

**S7b — recovery per chunk pentru promises și anchors — este livrat.** `reasoning_chunks.py`
păstrează în envelope câte un rând pentru fiecare interval și text exact, cu `pending`, `usable` și
`unusable` distincte. După fiecare cerere se scrie checkpointul; rerun-ul reutilizează numai
chunkurile `usable` și reapelează numai vecinii neutilizabili. O listă goală este un rezultat real,
dar un JSON non-gol din care normalizarea nu poate produce niciun promise/anchor rămâne retryable.
Proveniența numește providerul, modelul rezolvat, amprentele promptului/răspunsului, toate
încercările, numărul de obiecte normalizate și lipsa seedului (`nondeterministic: true`). Un state
nested malformat este refuzat integral, ca un rând corupt să nu se deplaseze peste chunkul următor.
Suita completă după S7b: **1.669 passed, 2 failed**, aceleași 404 TikTok preexistente.

**S7c — cache per rundă pentru judge — este livrat.** `judge_cache.py` păstrează răspunsul brut
pentru promptul exact și îl reaplică determinist. Rândurile neparsabile, goale sau fără verdict
aplicabil sunt `unusable` și se reapelează; starea malformată ori cu runde duplicate este refuzată
integral. Envelope-ul include identitatea sursei/transcriptului, versiunea promptului, ordinea
providerilor, modelele rezolvate și setările de inferență, iar rândul persistă providerul folosit,
amprentele promptului/răspunsului, toate încercările, `seed: null` și `nondeterministic: true`.
Testul prin worker dovedește că prima rulare scrie și a doua nu apelează providerul. Eșecul scrierii
checkpointului este vizibil în trace, dar nu anulează verdictul deja aplicat; fallback-ul după un
răspuns JSON inutilizabil este raportat ca fallback. Suita completă după S7c: **1.678 passed,
2 failed**, aceleași 404 TikTok preexistente.

**S7d — `anchor_id` canonic — este livrat.** Identitatea este atribuită după dedupe-ul de overlap,
înainte de variante, și este namespaced de dovezile exacte ale sursei. Confidence-ul și proveniența
nu o schimbă; un răspuns semantic diferit de la un apel nondeterminist primește deliberat alt id.
Variantele o păstrează atât top-level, cât și în `StoryEvidence`, iar `selection_trace` o arată
separat de `moment_id`. Censusul folosește ancora pe run-urile noi și payoff bucket numai pe
artefactele vechi. Un grup în care dedupe a unit mai multe ancore păstrează reuniunea tuturor
id-urilor și grounding-ul fiecăreia, deci liderul nu micșorează numitorul. Nu schimbă selecția.
Suita completă după S7d: **1.684 passed, 2 failed**, aceleași 404 TikTok preexistente.

**S7e — scara dedupe — este livrată.** Definiția grupului și alegerea tăieturii din grup sunt acum
două întrebări diferite. Topologia greedy se construiește numai din `heuristic_score`, înghețat
înainte de judge, fiindcă un verdict nu are voie să transforme A~B și B~C într-un singur moment când
A nu seamănă cu C. După ce grupul este stabil, liderul și diversity citesc `selection_score`, care
este propagat la toate variantele momentului; un moment nejudecat cade explicit pe euristică.
`overall` mai este citit numai pentru artefacte fără niciuna dintre scalele numite. O scară
necunoscută, un scor declarat dar invalid sau un candidat non-record nu sunt transformate în altă
măsurătoare.

Măsurat fără rescriere pe toate cele **13** artefacte `candidates.json`: cele opt proiecte fără
amestec de scară rămân identice la grupuri și top-8; numai cele cinci cu verdict judge stocat se
schimbă. Noile numere reproduc exact gruparea făcută înainte de verdict și înregistrată în trace:
`pilotf81b` 23, `pilotee0e` 49, `pilot6b38` 78, `pilot2c8a` 278 și `gateslice4h` 294. Regruparea veche
pe `overall` dădea 31/48/74/273/304. Perechea 23 contra 31 și, mai ales, 294 contra 304 este aceeași
discrepanță care fusese reconstruită manual restaurând `heuristic_score`; acum codul o împiedică.
Artefactele stocate nu au fost re-score-uite, deci `selection_trace` vechi păstrează deliberat
grupurile vechi. Suita completă după S7e: **1.696 passed, 2 failed**, aceleași 404 TikTok.

**S7f — identitatea comună selection/render — este livrată.** `RunTrace.run_id` era deja comun
celor două artefacte ale score-ului; acum devine `selection_run_id` pe fiecare clip nou și în
sidecar-ul randării lui. Orice `shadow_run_id` trebuie să coincidă înainte ca DB-ul să înlocuiască
board-ul. Exporturile păstrate peste re-score rămân legate de rularea care le-a creat, iar cele
istorice rămân `null`, nu sunt atribuite trace-ului curent. ID-ul nu intră în fingerprint-ul imaginii.
Legătura face o nepotrivire observabilă, dar nu arhivează automat un trace vechi suprascris. Suita
după S7f: **1.712 passed, 2 failed**, aceleași 404 TikTok.

**S7 este închis.**

## Batch S8a — fișierul review-ului este legat de sesiune, 3 septembrie 2026

`review_media.py` și ruta de review captează SHA-256 pe TOT exportul și pe sidecar, nu doar pe
primii 8 MB. Versiunea vine din sidecarul observat, nu din rendererul instalat; `selection_run_id`,
fereastra și transcriptul sunt păstrate cu sesiunea. Lipsa istorică rămâne lipsă, iar o sesiune cu
versiuni mixte le enumeră fără să pretindă una comună. Pozițiile/rank-urile sunt fixate la creare.

Înainte de prezentare, video și răspuns, ambele amprente se verifică. Fișierul schimbat dă 409,
nu primește verdict pentru versiunea nouă. Un proiect cerut fără board, un membru fără export sau
un amestec de selection runs în același proiect refuză sesiunea întreagă. Modificările ulterioare
în DB nu schimbă timpii/textul arătat. Răspunsul și evenimentul `reviewed` păstrează amprentele
media, separat de aprobările de produs. Rezultatele vechi se pot citi; sesiunile vechi fără
snapshot nu mai pot primi răspunsuri noi. Nu s-au modificat fișierele media sau creat review-uri reale.

**Limită explicită:** hash-ul leagă bytes, nu certifică imaginea, durata, vizionarea de către om
sau asocierea istorică sidecar/encode. S8a nu închide S8. Urmează
randarea neutră, payoff/diversitate în rubrică, review tehnic separat, minimum 10 SURSE distincte
(nu 10 proiecte/clonări) și 150 de momente, macro-agregare și intervale de încredere. Teste:
**1.739 passed, 2 failed**, aceleași 404 TikTok; typecheck curat.

## Batch S8b — dezvăluire numai după completare, 3 septembrie 2026

Ruta `/result` și butonul nu mai arată nici board-urile, nici tally-ul parțial înaintea ultimului
răspuns valid. Completitudinea compară id-urile planificate cu cele judecate, nu doar numărul.
Răspunsurile salvate nu mai pot fi rescrise; o retrimitere identică este acceptată fără feedback
dublat. Lock-ul OS din `review_lock.py` acoperă citirea/scrierea și copia feedback în ambele procese
de backend, nu numai într-un event loop. Concurența primește 409 retryabil; lock-ul se eliberează
la ieșirea procesului. Fișierul mic `.lock` rămâne intenționat pe disc pentru a nu crea doi inodes
independenți sub același nume. Nu este un job persistent care trebuie deblocat manual.

Sesiunea JSON rămâne sursa de adevăr. Dacă salvarea răspunsului reușește și DB-ul pică, retrimiterea
aceluiași răspuns completează feedback-ul lipsă; nu rescrie judecata. Sesiunile vechi (schema 1/2)
pot fi consultate ca istorice, dar nu pot continua: rezultatul lor putea fi dezvăluit pe parcurs.
Schema nouă este 3, politica `sealed_until_complete_v1`; rubrica rămâne `blind_eval_v1`.
UI-ul permite consultarea istoricului sau pornirea unei alte sesiuni când reluarea este refuzată.

**Următorul pas implementabil:** randare neutră separată pentru evaluare și rubrica S8 completă,
nu activarea motorului. Trebuie păstrate cohorta de surse distincte, review-ul tehnic separat și
gate-urile umane; protecția API-ului nu demonstrează superioritate semantică. Nu s-a rulat vreun
model și nu s-au creat sesiuni reale în S8a/S8b.

Verificare după S8b: **1.752 passed, 2 failed**, numai cele două 404 TikTok preexistente;
66 teste de review trec, inclusiv API și două procese; typecheck curat.

## RE-PLANIFICARE ȘI RE-RANDARE — 5 septembrie 2026

Cele 58 de exporturi ale piloturilor au fost **re-planificate**, nu rejucate. `rerender_pilots.py`
re-encoda din sidecarul de pe disc, deci planul rămânea cel din 22 august — motivul pentru care R1 a
aterizat pe 29 august, a scos 116 tăieturi invizibile din planner și **n-a schimbat niciun export**.
`scripts/replan_and_rerender.py` rulează `_decide_render` din nou.

Originalele sunt în `<proiect>/exports_pre_replan/`, iar `exports_pre_caption_fix/` de dinainte
rămâne neatins. O a doua rulare nu le poate suprascrie.

**Nu s-a pornit fără dovadă.** `scripts/verify_replan.py` re-planifică fără să encodeze și cere patru
probe; a rulat până a trecut pe toate. Apoi un micro-gate pe patru clipuri, câte unul pentru fiecare
defect confirmat, înainte de corpus.

### Poarta R0, pe corpusul nou

```
clipuri                    58        exit 0, integrity_ok: True
shot-uri                 1231        (erau 1.341)
tăieturi echivalente        0        ← erau 116, cifra de bază a batch-ului
salturi induse de trim      0
granițe necontigue          0
start pe primul cuvânt  58/58
coadă <= 50ms           22/58        ← neschimbat, e defectul de boundary
compoziție          crop=1198, fit=33
fingerprints          valid=58        ← erau `unavailable`
```

**Cele 116 sunt zero.** Prima oară când reparația R1 ajunge într-un fișier, la șapte zile după ce a
fost scrisă.

### Preflight-ul R7, pe corpusul nou

```
APPROVE 0    REVISE 67    REJECT 22    UNDECIDED 12

geometry_and_duration                  pass 58   fail  0   unavailable  43
cut_equivalence_and_profile_rhythm     pass  0   fail  0   unavailable 101
subject_present_when_required          pass  2   fail 12   unavailable  87
usable_frame_in_fit_and_no_chrome      pass  0   fail  0   unavailable 101
captions_not_duplicated_or_unreadable  pass  0   fail 55   unavailable  46
boundary_complete                      pass 44   fail 56   unavailable   1
provenance_complete                    pass 58   fail  0   unavailable  43
```

Trei mișcări reale, iar cele 43 de `unavailable` de peste tot sunt proiectele nere-randate:

- **`provenance_complete`: 0 → 58 treceri.** Verificarea a fost `unavailable` pe 101 din 101 tot
  batch-ul — întâi fiindcă nimic nu purta amprentă, apoi fiindcă un digest de rețetă nu atinge
  fișierul. Acum sidecarul poartă și `output_identity`, iar cele două jumătăți se verifică amândouă.
- **`cut_equivalence`: 23 eșecuri → 0.** Nu mai există niciun clip cu tăieturi pe care privitorul nu
  le poate vedea. Rămâne `unavailable` peste tot, fiindcă jumătatea de ritm nu se evaluează —
  starea onestă, nu o regresie.
- **`subject`: 0/0/101 → 2 treceri, 12 EȘECURI, 87 unavailable.** Prima oară când verificarea are
  intrare, fiindcă `regime_view` ajunge acum pe sidecar.

### Cele 12 eșecuri de subiect sunt toate `pilot2c8a`, și confirmă automat gate-ul uman

| clip | shot-uri `crop` | fără țintă | bază |
|---|---:|---:|---|
| `003a5c53c51d` | 46 | **37** | `stable_anchor` |
| `ec47597c60f2` | 17 | **17** | `stable_anchor` |
| `2a59b1e41880` | 21 | 17 | `stable_anchor` |
| `38aa005c7c1f` | 20 | 17 | `stable_anchor` |
| `ad1b8004ece9` | 10 | **10** | `stable_anchor` |
| `d646da7201ad` | 9 | 8 | `stable_anchor` |

Trei clipuri au **fiecare** shot `crop` ținut peste un interval unde ținta e măsurat absentă. Toate
poartă `target_basis: stable_anchor` — adică o ancoră a fost găsită, iar întrebarea „era ținta acolo"
e alta, exact distincția pe care Codex a numit-o: *o bază e o metodă, nu o măsurătoare.*

**Niciun alt pilot nu are vreun eșec.** Și pilotul care le are pe toate 12 e chiar cel pe care omul l-a
marcat pe 31 august: „personajul din browser e pus bine când vorbește". Verificarea construită după
review-ul lui Codex găsește acum defectul R3a singură, pe exact sursa unde un om îl văzuse.

### O scăpare a mea, și reparația

Prima re-randare a folosit `burn/default` pe toate cele 15 clipuri `pilotf81b` — deci **am recreat
cele 37 de duplicate**, exact ce avertizase Codex. Construisem mecanismul de politică de captions și
nu setasem setarea pentru proiectul pe care omul îl confirmase.

Reparat: `source_has_burned_captions: true` pe `pilotf81b`, care înregistrează verdictul lui din 31
august („se mai vede subtitrarea" = a SURSEI, 4 din 4), apoi cele 15 clipuri re-randate.
**REJECT 37 → 22.** Cele 22 rămase sunt `39c89ae2e16e` și `43a509687a33` — surse pe care detectorul
le dă `present` și pe care **nimeni nu le-a confirmat**, deci rămân respinse. Corect: detectorul e
`calibrated: false` și n-are voie să decidă singur.

### Ce NU s-a mișcat, și de ce

- **`boundary_complete` 44/56/1, neschimbat.** Re-planificarea nu re-scorează, deci ferestrele sunt
  aceleași. R5a mută finalurile la următoarea RULARE DE SCORING, nu la o re-randare.
- **`usable_frame` 101 `unavailable`.** Cache-ul de chrome e cheiat pe identitatea mp4-ului, iar 58 de
  fișiere tocmai s-au schimbat — deci verdictele lor sunt corect invalidate și cer ~66 de minute de
  OCR. Cache-ul a făcut exact ce trebuia.
- **Cele 57 de joncțiuni lungi** rămân. Verdictul uman din 4 septembrie a ales hard cut dintre trei
  prezentări; niciuna nu era una pe care el s-o numească bună.

## ÎNCADRAREA PE FAZE — stare la 9 septembrie 2026

Istoricul complet al arcului 5–9 septembrie este în
[`handoff-clipper-session-5.md`](../../archive/clipper/handoff-clipper-session-5.md).
Aici doar starea.

### Ce este livrat și măsurat

**Stratul suprimat, reparat.** `services/clipper/layout_policy.py` decide
`SECOND_CAMERA` / `NO_SECOND_CAMERA` (`SETTING = "source_has_a_second_camera"`);
`plan_dynamic_edit(..., no_second_camera=True)` scoate ramura `alive`. Verificat
**pe cadre randate**, nu pe plan: pe `pilotf81b` 48,1 s → **0,0 s** în care
încadrarea nu conține subiectul, fețe găsite în export 60/71 → **71/71**, clip de
control identic pe octeți (`23d9ec2a87842a67`).

**Generațiile de exporturi.** `services/clipper/export_generations.py` înlocuiește
cele două `_preserve` care aveau bug-uri opuse — unul continua când backup-ul
exista și pierdea generația pe care o înlocuia, celălalt refuza și nu putea
păstra niciodată a doua. Serial (`exports_pre_replan_02`), inventar cu sha256
scris **în interiorul** copiei. 19 inventare există deja pe disc.

**Adresarea temporală.** Indexul se citește ÎNAINTE de `read()`, timpul DUPĂ —
cele două proprietăți ale decodorului sunt în dezacord cu un cadru în același
moment. Reparat în `source_caption_observation.observe` și în cele trei scripturi
care îl oglindesc, cu test care pică pe ordinea veche. `build_phase_regions._in_clip`
refuză orice cadru din afara clipului: **f2425 era cadru de construcție al fazei
`screen` și e la 242,5 s față de un final la 242,42.**

### Ce NU este verificat, și de ce

**Cele patru regiuni ale lui `b23c14c41495` sunt înghețate dar NU sunt
verificate.** Candidatul curent e construit din adnotări citite pe proxy la
`--scale 3.0`, despre care lotul de calibrare a arătat că sunt deplasate cu 10–154
px sursă față de o toleranță declarată de ±25,6 px orizontal / ±14,4 vertical —
**de șase ori toleranța, în ambele direcții**. Toleranța nu era o măsurătoare a
instrumentului, era o presupunere despre el.

**Constatarea deschisă:** pe primele 7 din 21 de cadre ale fazei `screen`, citite
întregi la rezoluția sursei, capul vorbitorului ajunge la x 0,785–0,810 față de o
muchie a regiunii la 0,755 — **tăiat cu până la 141 px sursă**. Aceeași margine a
trecut prin patru verdicte (tăiat → ținut → nemăsurat → tăiat) și abia ultimul s-a
uitat la obiectul întreg.

Rămân în plus neverificate: subiectul fazei `speech` (44 + 55 cadre de hold-out
fără adnotare de față), jumătatea de LINII pentru regiunile care s-au mutat între
timp, tranzițiile (rezultatul lor stă pe regiuni care s-au mutat de atunci) și
deficitul din `screen` q0, unde a existat un singur cadru eligibil.

**Nimic din toate astea nu e cablat la randare.**

### Instrumentele, și de ce fiecare există

| Fișier | Ce face, și defectul care l-a cerut |
|---|---|
| `scripts/watch_annotations.py` + `_fresh.py` | geometria confirmată vizual, trei loturi (construcție / hold-out / fresh). Poartă valorile ÎNLOCUITE și un motiv pe cutie, fiindcă întrebarea următorului cititor e „a fost mereu așa, sau a fost mutată după un verdict" |
| `scripts/build_phase_regions.py` | o regiune constantă pe fază, din observații confirmate; refuză o fază fără adnotare de subiect, și orice cadru din afara clipului |
| `scripts/verify_phase_regions.py` | singurul test necircular; verifică LINIILE și SUBIECTUL separat, plus tranzițiile; `--set holdout\|fresh` |
| `scripts/select_fresh_lot.py` | alege lotul ÎNAINTE să-l vadă cineva, după o regulă convenită în avans; raportează deficitul, nu-l completează |
| `scripts/replay_annotations.py` | desenează coordonatele STOCATE peste cadrul pe care îl numesc — verifică transcrierea și adresarea, nu geometria |
| `scripts/source_crop.py` | **sursa 2560×1440, fără interpolare.** `--full --bare` e vederea pentru pasul 1; `--edge` pentru o singură margine. Grila e etichetată în fracțiuni din CADRUL ÎNTREG |
| `scripts/edge_calibration.py` | pasul 2: intervale precise, doar pentru marginile care stabilesc extremele |
| `scripts/coverage_bounds.py` | pasul 1: o limită conservatoare pentru fiecare parte pe fiecare cadru relevant |

### Cinci lucruri de nu repetat

1. **Un cadru pe planșă.** Planșele cu două cadre alăturate au propria riglă per
   tile; fiecare cadru din dreapta a ieșit deplasat cu 0,26–0,30 din cadru și a
   distrus prima adnotare întreagă.
2. **Mărirea proxy-ului nu adaugă detaliu.** 480×270 mărit la 1440 interpolează.
   Sursa e 2560×1440 și e pe disc.
3. **Un decupaj strâns pe o muchie ascunde conturul care decide extrema.**
   `f2400` a fost citit la înălțimea 0,20–0,35, unde silueta se vede pe cer, iar
   capul e cel mai lat mai jos, în bokeh.
4. **Nu compara o măsurătoare cu una luată cu alt instrument.** Prima rulare de
   hold-out a raportat un fapt despre două treceri de citire ca fapt despre
   regiune.
5. **Nu înlocui ±0,01 cu ±0,05 fiindcă acoperă discrepanțele.** Incertitudinea e
   intervalul măsurat pe cadrul căruia îi aparține; verdictul e dacă muchia
   regiunii cade înăuntrul lui.

### Punctul exact de reluare pentru încadrare

Planul e al lui Codex, în două treceri, și **nu cere încă un lot nou de 36**:

1. **PASUL 1, în curs: `coverage_bounds`.** 27 din 258 de perechi cadru/parte au
   limită; 1 confirmată absentă; **230 necitite**. Rulează cu exit 2 până se
   închide. Vederea e `source_crop --full --bare` — obiectele întregi, **fără
   nicio cutie desenată**, fiindcă limita trebuie confirmată pe sursă, nu
   moștenită din cutia greșită. Se înregistrează o limită sigură („marginea
   dreaptă e înainte de 0,79"), nu o poziție inventată.
2. **PASUL 2: `edge_calibration`,** intervale precise doar pentru marginile care
   ies extreme ale reuniunii (`coverage_bounds --extremes` le numește) și pentru
   cazurile neclare. Ce nu poate influența reuniunea nu merită precizie.
3. **Construcția** ia **capetele exterioare** ale intervalelor (stânga/sus =
   capătul inferior, dreapta/jos = capătul superior), reuniune peste cadrele
   fazei, intervalele păstrate separat de cutia derivată, rotunjirea în pixeli
   păstrează conținerea. Promisiunea rezultată e „conține toate pozițiile permise
   de observațiile înregistrate" — **nu** „păstrează subiectul pe toată durata".
4. **Reconstruiește o singură dată**, apoi randează proba video: conținerea,
   mărimea textului și a subiectului, tranzițiile și retragerea ceasului, pe toată
   secvența. Cele trei tabele existente rămân material de dezvoltare și regresie,
   cu istoricul păstrat; **nu mai pot fi prezentate ca verificare independentă** a
   candidatului nou.
5. Abia după ce instrumentul e stabil: cele 99 de cadre de față pentru `speech`.
