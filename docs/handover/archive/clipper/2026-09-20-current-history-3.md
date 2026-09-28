# Arhivă Clipper — R7, review și gate vizual

Mutat din CURRENT la 20 septembrie 2026. Istoric, inclusiv afirmații ulterior
retrase; nu este verdictul stării actuale. Conținutul secțiunilor este păstrat.

## Batch R7 — preflight de publicare, LIVRAT 31 august 2026, CORECTAT după review-ul Codex

**Defectul pe care îl repară e chiar în verdict**, și e declarat ca proprietate intenționată în
docstring-ul lui `review.py`: „a review that fails must not lose an export. It returns a verdict of
APPROVE with a warning, which is the same shape as a clean pass." Cinci căi de eșec întorc APPROVE.
**Măsurat: 12 din 101 de clipuri primesc APPROVE cu `sampled: 0`** — 18% din toate aprobările de pe
disc sunt aprobări ale unor clipuri pe care nu s-a uitat nimeni.

**Al patrulea cuvânt: `UNDECIDED`.** Nimic nu a picat și ceva nu a putut fi privit. Regula e că nu e
niciodată `APPROVE`; failure-safe se păstrează prin ce face în aval — nu blochează nimic, ca și azi —
nu prin a-l numi trecere. Cele două alternative sunt scrise în modul ca să nu fie reluate: a plia o
verificare imposibilă într-un `revise` pune două fapte sub un cuvânt, iar a păstra `APPROVE` cu un
steag `established` lângă lasă cuvântul care poartă decizia să mintă — forma lui
`changed_without_moving`.

### Cele opt defecte pe care le-a găsit Codex, și ce a arătat fiecare

Prima versiune trecea toate testele ei și era greșită în șapte locuri din opt. **Trei verificări
erau ORBITE STRUCTURAL** — fiecare citea un câmp care nu exista în ce primea, deci semnalul pe care
existau ca să-l poarte nu ajungea nicăieri, iar rezultatul arăta ca o măsurătoare curată:

1. **Boundary-ul se măsura pe `cand["words"]`** — adică pe lista `inside` a lui `_neighbourhood`,
   construită tocmai prin ARUNCAREA cuvântului care încalecă marginea, care e exact ce caută
   `start_inside_word` și `end_inside_word`. Docstring-ul lui `_straddled` o spune. Verificarea
   căuta în colecția făcută prin scoaterea a ceea ce caută. Pe transcriptul întreg: **12 exporturi
   livrate se termină în interiorul unui cuvânt.** Defectul pe care R5a l-a scos din 261 de
   ferestre, reintrodus la consumator.
2. **Captions căuta `lands_on`/`worst`/`refused`**, numele lui `placement_view` cu un strat mai jos,
   în timp ce `caption_corpus.measure` trimite `on_face`/`worst_share`/`placement_refused`. Deci
   `ON_FACE in placement["lands_on"]` era `in []` pe toate cele 101 clipuri. **Acum ajunge la 38.**
3. **Boundary citea `eligible`**, care e verdictul MOMENTULUI și pe care R5 îl lasă `True` pe baza
   unei reparații pe care `boundary_view` doar A PROPUS-O. Nimeni n-a aplicat-o exportului de pe
   disc. Aceeași familie cu `caption_plan.y_pct`. Citește acum DEFECTELE, și ia și axa `technical`
   pe care R5 a lăsat-o deliberat afară numind R7 drept proprietarul întrebării.
4. **Refuzurile coexistau cu APPROVE.** `refused` era calculat, tipărit și lăsat în afara
   verdictului — șapte verificări trecute plus o a opta verificare refuzată, sau plus a doua
   corecție, ieșeau `APPROVE` cu obiecția într-un câmp alături, iar auditul ieșea 0. Exact forma lui
   `changed_without_moving`. Acum orice refuz blochează `APPROVE` și pică rularea.
5. **Trei verificări treceau pe o singură jumătate a propriului nume.** `{"duration": 30}` fără
   nicio listă de shot-uri trecea `geometry_and_duration` — **12 din 101 de clipuri au forma asta**;
   zero tăieturi echivalente trecea toată „echivalență ȘI ritm" deși §4 însuși spune că ritmul nu se
   măsoară; iar o rețetă goală cu hash-ul ei corect trecea `provenance_complete`, fiindcă
   `render_input` completează cu `None` orice cheie absentă. Un hash valid nu atinge nicăieri
   fișierul livrat.
6. **Auditul crăpa pe un sub-record corupt.** `caption_plan: [1]` → `AttributeError` din mijlocul
   buclei, deci raportul acoperea clipurile dinainte și nu spunea niciodată unde s-a oprit.
7. **Cache-ul de OCR scria `<clip>.chrome.json` în `exports/`**, unde ȘASE locuri din repo citesc
   `*.json` ca sidecar de clip. Singurul fișier lăsat de rularea întreruptă făcea deja
   `audit_clipper_exports` să raporteze `orphan_sidecar` **și să iasă cu 2** — un cache care strica
   poarta de bază pentru care fusese colectat. Mutat în `<proiect>/chrome_cache/`, legat de
   dimensiunea și mtime-ul mp4-ului plus toată configurația detectorului; OCR-ul deja plătit e
   păstrat.
8. **Corecția nu-și impunea propria condiție.** Verificatorul era opțional, deci calea NEVERIFICATĂ
   era cea implicită, iar unul care întorcea `None` o trecea. Și citea NUMELE verificării: `captions`
   răspunde la trei întrebări și doar una e despre poziție, deci `highlight_floor_2.33` cumpăra o
   mutare de caption care nu repară o paletă. Acum citește motivul, față de `publish_checks.ON_A_FACE`,
   verificatorul e obligatoriu și numai `False` îl eliberează.

O corecție a mea, găsită rulând pe corpus: **31 de clipuri n-au `composition` pe niciun shot** —
sunt de dinaintea cheii. Prima versiune a verificării de subiect le numea „în afara listei închise",
adică 31 de înregistrări corupte. Verdictul e `unavailable` în ambele cazuri; motivul e ce citește
omul.

### Cele cinci module

- `publish_preflight.py` — vocabularul și verdictul. `APPROVE` doar când toate cele șapte trec. O
  listă goală e `UNDECIDED`. **Un refuz nu e un input indisponibil** și blochează `APPROVE`.
- `publish_checks.py` — șase verificări. **Ordinea e regula:** un defect MĂSURAT e `fail` orice
  altceva n-ar fi putut fi privit; abia apoi o jumătate nemăsurabilă îl face `unavailable`; un `pass`
  cere fiecare jumătate a propriului nume demonstrată.
- `publish_captions.py` — a șaptea, desprinsă la limita de 500 de linii. Singura care împacă patru
  surse.
- `publish_corpus.py` — unde stă fiecare semnal. Transcriptul e OBLIGATORIU, nu implicit.
- `bounded_correction.py` — singura corecție permisă și cele patru condiții, dintre care două nu erau
  de fapt impuse.

**Rezultatul pe corpus** (`scripts/audit_publish_preflight.py --with-source-captions
--with-chrome`, 101 clipuri, integritate curată, exit 0):

```
APPROVE 0    REVISE 59    REJECT 37    UNDECIDED 5

geometry_and_duration                  pass 58   fail  0   unavailable  43
cut_equivalence_and_profile_rhythm     pass  0   fail 23   unavailable  78
subject_present_when_required          pass  0   fail  0   unavailable 101
usable_frame_in_fit_and_no_chrome      pass  0   fail 16   unavailable  85
captions_not_duplicated_or_unreadable  pass  0   fail 64   unavailable  37
boundary_complete                      pass 44   fail 56   unavailable   1
provenance_complete                    pass  0   fail  0   unavailable 101
```

Defalcarea eșecurilor, fiindcă totalul singur nu spune nimic: captions = **37 duplicat de strat**
(rejectable) + **38 caption peste o față** (revisable, se suprapun pe 11 clipuri); boundary = 16
`clipped_release`, 16 `end_mid_sentence`, 12 `end_inside_word`+`end_mid_sentence`, 11
`clipped_release`+`end_mid_sentence`, 1 cu `orphan_tail`; frame = **16 exporturi cu chrome de
player**, restul `not_detected`, care nu e o trecere.

## Al doilea review Codex — cinci P1 și două P2, toate reparate

Prima versiune a reparațiilor trecea toate testele ei și era greșită în șapte locuri. Cea mai
usturătoare: **reparația mea de geometrie purta chiar defectul pe care îl repara.** `clip_report`
întoarce stringul `"unavailable"` doar când lista de shot-uri nu se poate citi; când SE poate, dă un
contor, iar un contor de necunoscute arată `{"unavailable": 19}`. Comparam tot câmpul cu santinela.
**31 din cele 89 de treceri aveau fiecare compoziție necunoscută** — de aceea cifra e acum 58, nu 89.
Un container nu e scalarul pe care îl conține.

- **De ce lipsește un input e TIPAT acum** — `absent` / `refused` / `unreadable`, decis de producător.
  Auditul sorta corupt de absent căutând fragmente în textul motivului, ceea ce nu e un contract:
  **cinci intrări corupte diferite treceau prin `main()` cu exit 0 și `integrity: true`** fiindcă
  producătorii formulau refuzul altfel. Refuzurile pistei de fețe nu ieșeau deloc din `faces`.
- **Populația e verificată, nu presupusă.** Un sidecar care declară alt `clip_id` sau alt
  `project_id` e refuzat, iar un mp4 fără sidecar e numit. Poarta R0 face deja ambele.
- **O BAZĂ E O METODĂ, NU O MĂSURĂTOARE.** `target_basis: stable_anchor` trecea verificarea de
  subiect pe șaisprezece eșantioane cu `evidence.target = 0.0` și pe zero eșantioane cu
  `target_covered: false`. Acum intersectează fiecare shot `crop` cu dovada per segment, iar un crop
  ținut peste un interval unde ținta e măsurat absentă e o constatare, nu o trecere. Se citește DOAR
  dovada, niciodată regimul propus alături — ăla ar compara un crop livrat cu un plan.
- **Nu mai există `False` pentru `own_layer`.** Un `.ass` lipsă însemna „exportul ăsta n-are strat
  propriu", și nu înseamnă: `.ass`-ul e instrucțiunea, mp4-ul e artefactul, iar ștergerea
  instrucțiunii după ardere nu scoate nimic din video — și absența aia e ce lăsa verificarea de
  duplicat să treacă.
- **Validarea containerelor mutase gaura cu un nivel mai jos:** `defects=[7]` e o listă, deci trecea
  verificarea de tip, nu se potrivea cu nimic din mulțimile închise, și ieșea curat. La fel un nume
  de defect scris greșit. La fel `contrast={}`, care intra în ramura „paletă lizibilă" fără nicio
  latură de obiectat.
- **Cache-ul se cheiază pe toate constantele detectorului**, enumerate din modul de către PRODUCĂTOR.
  Lista ținută de consumator era deja incompletă cu una: `SAMPLES_MIN` e citit de `classify` și nu
  era în cheie. Și identitatea mp4-ului se ia ÎNAINTE și DUPĂ detecție — un minut de OCR e destul ca
  o re-randare să aterizeze la mijloc.

## OCR-ul de chrome — rulat, și de ce n-a mers prima dată

**`server/.venv` avea `torch 2.13.0+cpu`.** `torch.cuda.is_available()` era `False`, deci
`source_chrome._reader()` încerca `gpu=True`, eșua și cădea tăcut pe CPU: **zero verdicte în 38 de
minute**. CUDA-ul care merge e al lui `ctranslate2` (whisper) și nu se transmite la torch.

Reparat cu `torch 2.13.0+cu126` — aceeași versiune, doar backend-ul. **Măsurat: 68s per export,
~115 minute pe corpus.** Estimarea de „~3 ore" era corectă pentru un GPU; venv-ul doar nu avea unul.

Cele 101 de verdicte sunt în `<proiect>/chrome_cache/`, migrate cu
`scripts/migrate_chrome_cache.py` ca să poarte `measured_with`. Migrarea e VERIFICATĂ: trei din cele
patru constante erau deja în fiecare verdict și scriptul refuză orice fișier ale cărui valori diferă
de ale modulului; a patra n-a fost schimbată niciodată de la `dbd1cc0`.

**Cele 37 de respingeri sunt defectul cu care s-a deschis R0**, dar acum fiecare se sprijină pe
AMBELE straturi: sursa are captions arse (`pilotf81b`, `39c89ae2e16e`, `43a509687a33`) **și** exportul
are `.ass`-ul lui. Un verdict despre proxy-ul proiectului nu e o propoziție despre un export anume,
iar direcția inversă — lipsa verdictului de sursă — putea ajunge `PASS` pe o verificare al cărei prim
cuvânt e „nedublat".

**Două contracte, ținute separat** (a doua decizie a lui Codex). Auditul DESCRIPTIV poate ieși verde
peste un corpus aproape integral `UNDECIDED` — că majoritatea celor șapte n-au intrare ESTE
constatarea, iar o poartă permanent roșie ar îngropa-o. POARTA DE PUBLICARE e celălalt contract:
numai `APPROVE`, fără refuzuri, cu dovezi actuale — și nimic nu e cablat la ea. Ce datorează totuși
rularea descriptivă e INTEGRITATEA, și de asta e codul de ieșire: sidecar necitibil, înregistrare care
a aruncat, corpus gol, orice refuz.

**Cerința de subiect vine din TRATAMENTUL LIVRAT** (prima decizie a lui Codex), nu dintr-o listă de
profile pe care nu a scris-o nimeni: un `crop` pune o fereastră 9:16 undeva fiindcă a spus o ancoră,
deci cere dovada țintei; un `fit` păstrează cadrul întreg, deci o diagramă sau un plan larg nu
datorează nicio față. Clipurile numai-`fit` TREC. Un crop cere `regime_view.target_basis`, iar
`unanchored_face` nu e destul — cazul Moist urmărește *o* față, cea din browser.

**De ce fiecare `unavailable` rămâne așa:**
- **subject** — cerința e derivată acum, dar `regime_view` nu e pe niciun sidecar stocat; apare la
  prima randare prin `clipper_shadow_views`. 58 de clipuri au shot-uri `crop`, 31 n-au `composition`
  deloc, 12 n-au listă de shot-uri.
- **frame** — RULAT. 16 exporturi au chrome de player; restul de 85 ies `not_detected`, care
  nu e o trecere (recall mic și nedemonstrat, iar modulul o spune). Istoric păstrat fiindcă e o
  capcană de rig: prima încercare a dat **zero verdicte în 38 de minute** fiindcă `server/.venv`
  avea `torch 2.13.0+cpu`. `torch.version.cuda` e `None` și
  `torch.cuda.is_available()` e `False`, deci `source_chrome._reader()` încearcă `gpu=True`, eșuează
  și cade tăcut pe `gpu=False`. Pornită pe 31 august, rularea a produs **zero verdicte în 38 de
  minute** — estimarea de „~3 ore" din handover-ul anterior presupunea GPU și era greșită. CUDA-ul
  care merge e al lui `ctranslate2` (whisper) și nu se transmite la torch. **Orice cifră din
  documente de forma „5s pe sursă pe GPU" pentru o cale easyocr nu a fost măsurată în venv-ul
  ăsta** — asta include `source_captions`. Se deblochează cu un wheel de torch cu CUDA (rig-ul e un
  RTX 2080 Super); până atunci OCR-ul e muncă de peste noapte, nu de o pauză de cafea.
- **provenance** — două motive acum, nu unul: sidecarele n-au `input_fingerprint`, și chiar când vor
  avea, un digest valid acoperă rețeta, nu fișierul livrat. Nu există azi drum către un `pass`.
- **captions** parțial (37) — `worst_share_complete` e fals fiindcă semnalele de UI și text-sursă
  n-au detecție per shot.
- **equivalence** (78) — 43 fără listă de shot-uri citibilă, 35 fiindcă ritmul nu se evaluează.

## GATE-UL VIZUAL UMAN — FĂCUT, 31 august 2026

Vezi [`docs/refs/human-gate-2026-08-31.md`](../../../refs/human-gate-2026-08-31.md) pentru verdicte
verbatim și diagnostic. 20 de clipuri alese pe dovadă, **11 bad / 9 ok**. Trei porți care stăteau de
patru sesiuni s-au închis — toate trei **PICAT**:

- **R3a PICAT.** Crop-ul chiar urmărește personajul din browser pe Moist. Confirmă ce refuzau
  comentariile din cod să afirme în vreo direcție: `stable_track` găsește un cluster geometric, nu o
  persoană.
- **R3b PICAT, 8 din 8.** Nota decisivă: „detecția pe fețe e bună, doar în momentul în care intră
  16:9 cam 2 secunde nu e tranziția bună."
- **R6 captions duble PICAT, 4 din 4**, pe corpusul RE-RANDAT. Întrebat direct a cui subtitrare se
  vede, omul a răspuns: **a SURSEI.** Prima confirmare umană pe care o primește vreunul dintre
  instrumentele astea, și validează trei lucruri deodată: `source_captions` nu dădea fals pozitiv,
  cele 37 de respingeri ale preflight-ului sunt DEMONSTRATE (exact ce refuza Codex să le acorde), iar
  cerința celor două straturi era corectă.
- **Caption peste față TRECUT, 4 din 4.** Semnalul e adevărat geometric și fără consecință vizuală pe
  eșantion. **Primul lucru pe care o măsurătoare umană l-a contrazis** — ca `REVISABLE` în preflight
  pare prea strict.

### Joncțiunea crop↔fit e un salt de scară de minim 3,16x, prin construcție

Pe clipul cu timpi exacți: patru schimbări de compoziție, **toate patru în cele patru ferestre
raportate, zero ratate.** Ipoteza concurentă e falsificată de aceleași date — salturile de ancoră de
960px, 844px și **1138px, cel mai mare din clip**, sunt neraportate.

Măsurat pe toate cele 84 de joncțiuni: **median 3,58x, max 13,52x, niciuna sub 3x.** Minimul e o
identitate a geometriei 16:9 (`crop` 1,78x, `fit` 0,5625x), nu un număr de reglat.

**Livrat:** `dynamic_geometry.absorb_brief_fit_islands` — o insulă `fit` sub 4s, doar interioară,
doar spre `crop`, înainte de merge. **Prima schimbare din tot batch-ul care modifică imaginea
livrată, nu doar înregistrează lângă ea.** Direcția vine din corpus: insulele `crop` sunt 18 cu
minimul la 3,6s, cele `fit` sunt 39 cu 15 sub 4s.

**Ce NU repară, scris și în cod:** elimină 30 din 84, și **niciuna dintre cele patru pe care le-a
cronometrat omul** — alea sunt secvențe lungi și câștigate (8,8s / 13,8s / 15,8s / 14,6s / 7,5s).

### Fereastra `fit` mai îngustă — sugestia mea, măsurată și moartă

48 din 50 de cadre `fit` folosesc TOATĂ lățimea sursei; zero încap în 70%; o fereastră 4:5 ar păstra
45%. Ar tăia conținut pe practic fiecare shot `fit`. `scripts/measure_fit_content_width.py`.

### Cele 116 tăieturi invizibile sunt încă acolo

Al patrulea clip vlog, pe care diagnosticul de joncțiune nu-l acoperea, are **17 tăieturi
invizibile** — 20 de shot-uri devin 3 prin `merge_equivalent_shots`. Pe tot corpusul: **exact 116, pe
23 din 58**, cifra de bază a lui R0 neschimbată. **R1 a reparat plannerul pe 29 august și reparația
n-a ajuns niciodată într-un export**, fiindcă re-randarea din 31 august a rejucat planurile stocate
în loc să re-planifice. Cel puțin 6 din cele 11 clipuri marcate `bad` poartă și tăieturi invizibile.
