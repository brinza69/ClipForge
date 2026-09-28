# Arhivă Clipper — R4–R6 și corecții

Mutat din CURRENT la 20 septembrie 2026. Istoric, inclusiv afirmații ulterior
retrase; nu este verdictul stării actuale. Conținutul secțiunilor este păstrat.

## Batch R4 — INSTRUMENTAT, gate PENDING, 29-30 august 2026

**Review Codex, șase runde, verdict final:** „Nu mai văd blocante. Consider R4 închis corect ca
**instrumentare shadow**, nu ca motor activ." Toate cele opt constatări au fost reparate; niciuna nu
era în regula batch-ului, toate erau în plasare — ce moment compari, la ce rezoluție, față de care
margine. Regula „motiv plus loc" a rezistat de la început.

**Nu este închis**, ca R3a și R3b — dar dintr-un motiv în plus. Gramatica există complet și e
verificată automat; nimic nu a fost aplicat, `legacy_dynamic` rămâne înghețat, iar propunerea se
scrie în sidecar ca `rhythm_view`. Gate-ul lui §6 („auditul uman nu mai descrie montajul drept
agitat, numărul de tăieturi scade fără cadre moarte lungi") nu poate fi nici măcar ÎNCERCAT până
când `content_aware` nu devine livrabil: cere piloturile re-randate cu gramatica aplicată. Până
atunci se poate compara doar propunerea cu cadența livrată, în sidecar, clip cu clip.

**Regula, și este tot batch-ul: o tăietură are nevoie de un MOTIV și de un LOC.** Motivul e o
schimbare declarată în ce trebuie văzut — `treatment_change` de la R3b, `source_scene_cut` când
sursa a tăiat ea însăși, `action_beat` doar într-un interval pe care R3b l-a măsurat ca acțiune.
Locul e o graniță unde tăietura nu cade în mijlocul unui cuvânt, luată din `_boundaries`, nu
reimplementată. **O pauză fără nimic în spate este exact tăietura pe care batch-ul o elimină.**

Măsurat prin funcția livrată, nu descris: pe același talking-head liniștit cu șase pauze naturale,
`_cut_times` taie de opt ori — ultimele două (16,8s și 18,6s) fără nicio graniță, pur pe ceas — iar
R4 taie de zero ori.

**Asimetria contează.** `treatment_change` e obligatoriu și taie chiar și fără graniță
(`placement: unsnapped`) și chiar peste `min_shot_s`, cu violarea înregistrată în
`min_shot_violations`. A ține un crop pe o ancoră goală ca să protejezi o lungime minimă schimbă un
defect vizibil pe o metrică invizibilă — și lista de violări e felul în care un timeline de prezență
care pâlpâie devine ceva ce i se poate arăta unui om.

**Patru corecții din review-ul Codex, toate găsite citind codul:** două schimbări obligatorii nu pot
fi satisfăcute de aceeași tăietură (la 5,0s și 5,2s ambele se agățau de pauza de la 5,1s și
tratamentul dintre ele dispărea complet — fără tăietură, fără `held`, fără violare); coada runt nu e
o violare ci un refuz (o tăietură la 9,9s dintr-un clip de 10s e un flash de 100ms și iese în
`required_conflicts`); acoperirea parțială nu e o partiționare (secundele nemăsurate intrau automat
în `quiet` și un minut nemăsurat ieșea `below`); `indeterminate` ascundea un `above` demonstrabil —
trei tăieturi în patru secunde sunt 45/min, iar durata scurtă nu face asta ambiguu. Plus atribuirea:
o tăietură la granița `action → speaker` e o **tranziție**, creditată niciunei benzi.

**Benzile din §4 se compară, nu se impun.** Nimic nu adaugă o tăietură ca să atingă o bandă, nimic nu
scoate una ca să rămână în ea. `action` e singurul profil judecat pe două benzi — secundele numite
`action` și restul; fără măsurătoarea de mișcare ambele partiții ies `unavailable`, niciodată
contopite într-un număr care ar judeca liniștea cu banda agitată. `indeterminate` e un verdict real:
sub `60 / lo` secunde propria podea a benzii nu așteaptă încă nicio tăietură.

**Ce lipsește, spus pe față.** `UNMEASURED` numește per profil ce cere §4 și nimic nu măsoară:
`talking_head` cere reframe „la o idee sau emoție clară", iar singurul lucru care există e un regex
de cuvinte-cheie. O listă cu „bro" și „lol" nu e o emoție. De aceea `talking_head` și `conversation`
ies `below` banda lor — golul e măsurătoarea care lipsește, nu un ritm care are nevoie de umplutură.

**`dynamic_cuts.py` și `dynamic_edit.py` nu au fost atinse.** Planul le lista; a le modifica ar
schimba planul livrat, pe care R2 l-a înghețat și de care depind gate-urile vizuale deschise.
`delivers_profile()` face comutarea singur în ziua în care `content_aware` intră în `SELECTABLE`.

**Două corecții suplimentare, tot din review, ambele „conflict fabricat de plasare":** un snap nu
mai poate trece dincolo de următoarea schimbare obligatorie (mutarea unei tăieturi peste ea păstrează
ordinea numerică și tot pierde tratamentul dintre cele două), și o schimbare obligatorie nu mai poate
face snap în zona cozii (una la 9,2s dintr-un clip de 10s se muta la 9,5s, era scoasă de walk-back și
raportată ca imposibilă, deși propriul ei moment lasă un shot legal de 0,8s). Plus: o tăietură scoasă
la coadă păstrează acum TOATE motivele pe care le răspundea, nu doar primul.

**Simetria, găsită punând întrebarea inversă:** un snap nu poate trece de o schimbare obligatorie
**în niciun sens**. Înainte pierde tratamentul dintre cele două; înapoi, tăietura care introduce
schimbarea asta se întâmplă înainte ca precedenta să fi început. Ordinea tăieturilor nu prinde
niciunul — o tăietură dinaintea schimbării precedente dar de după TĂIETURA precedentă e în ordine.
Marginea cozii e însă inclusivă: `duration - min_shot_s` e ultimul loc legal, iar un `<` strict
arunca exact singura graniță pe care regula o permite.

**Ultimele două, de precizie:** marginile testau timpul BRUT al graniței în timp ce tăietura se plasa
la cel rotunjit, deci o pauză la 5,0499996 trecea de un plafon de 5,05 și ateriza exact pe el — pe
chiar schimbarea pe care nu avea voie s-o traverseze. Și egalitatea de moment compara `existing["t"]`,
adică unde a ajuns tăietura după snap, nu momentul cererii; se compară acum cu `requests`. Două
motive împart o tăietură doar dacă au fost CERUTE în același moment.

**Split nou:** `services/clipper/dynamic_rhythm_vocab.py` (listele închise),
`services/clipper/dynamic_rhythm_pace.py` (benzile), `tests/test_clipper_rhythm_place.py` (plasarea,
unde e fiecare constatare din review) și `workers/clipper_shadow_views.py` — tot ce o randare ÎNREGISTREAZĂ despre montajul pe
care nu l-a făcut (R3a + R3b + R4). `clipper_render_plan.py` trecuse de 500 de linii.

## Batch R5 — INSTRUMENTAT, gate NEMĂSURAT, 30 august 2026

Fiecare fereastră aleasă primește un **verdict de completitudine** în `candidates.json`, ca
`boundary_view`. Nimic nu se aplică: `eligible` nu decide nimic, fiindcă planul însuși condiționează
asta de trecerea corpusului.

**De ce era nevoie de un verdict, nu de încă o mutare.** `refine_boundaries` mută deja ambele margini
și `extract_features` le scorează deja. Un scor e un număr după care sortezi — nu poate spune cu voce
tare „clipul ăsta se oprește în mijlocul unui cuvânt". De aceea `candidate_boundaries.py` și
`story_evidence.py` nu au fost atinse, deși planul le lista.

**Gaura închisă:** `ends_on_sentence = 1.0 if text.endswith(".!?…")` e 0.0 pentru fiecare clip tăiat
vreodată dintr-un transcript fără punctuație — iar 0.0 acolo nu înseamnă „se termină la mijlocul
propoziției", înseamnă că nimeni nu a putut ști. `transcriber._clean_text` scoate punctuația implicit.
Acum verificările de propoziție ies `unavailable` și `eligible` e `None`; o tăietură în mijlocul unui
cuvânt rămâne măsurabilă oricum, fiindcă nu depinde de punctuație sau de limbă.

**Nouă defecte, patru blocante:** `start_inside_word`, `end_inside_word`, `end_mid_sentence`,
`orphan_tail`. `start_mid_sentence` nu blochează — un hook deschide legitim la mijloc, iar gate-ul
cere un om. `clipped_release` nu blochează — se repară prin padding, nu prin refuzarea momentului. E
pragul `TAIL_TIGHT_S` importat din `edit_quality`, ca partea de selecție și cea de randare să numere
ACELAȘI defect (22 din 58 la baseline).

**O singură reparație, bounded:** extinderea finalului la următoarea graniță de propoziție, mărginită
de durata maximă, de mediu și de prima fereastră care începe DUPĂ aceasta. Ce rămâne după reparație se
măsoară pe fereastra reparată, nu se presupune.

**Gate-ul E măsurabil, prin `--recompute`.** Toate cele 22 de proiecte de pe disc au fost scorate
înainte de R5, deci nu poartă verdict; `scripts/audit_clipper_boundaries.py` le numește
`predates_r5_rescore_needed` și tipărește numitorul înaintea numărătorii („0 din 144 candidați poartă
un verdict"). Cu `--recompute` încarcă transcriptul din DB și rulează ACEEAȘI funcție canonică
(`boundary_completion.attach`) peste ferestrele stocate, fără re-score și fără să atingă vreun scor
sau board. Măsoară REGULA, nu pipeline-ul — un verde acolo lasă integrarea end-to-end nedemonstrată.

**PRIMA MĂSURĂTOARE, pe tot corpusul (6.762 de candidați, 13 proiecte, toți cu verdict): 261 de
ferestre se termină în interiorul unui cuvânt și ZERO încep așa.** Gate-ul cere zero. Asimetria
261/0 e chiar demonstrația cauzei — `refine_boundaries` aplică `_snap` pe început și niciodată pe
finalul final. Verificat manual pe patru cazuri:
`_reaction_end` întoarce `min(w1, limit)`, iar când reacția lovește plafonul `REACTION_MAX_S` la
mijlocul unui cuvânt tăietura cade acolo; `_snap` nu se aplică niciodată pe finalul final, iar `_fit`
snapează doar la depășirea maximului. Toate patru poartă `reaction_kept`. **Reparat în R5a** — vezi mai jos.

**`--recompute` rulează regula de AZI peste ferestre produse de codul de atunci**, deci o cifră
agregată amestecă generații. Verificat: artefactul lui `slice4h00test` e din 14 august, iar
`_keep_release` a intrat a doua zi în `4a136de` — de acolo vin cele 880 din 920 de `clipped_release`
ale lui: pad-ul nu a rulat niciodată pentru el. Pe ferestrele lui vechi, implementarea de azi ar
adăuga padding la 748 din 880, deci proiectul demonstrează lipsa de proveniență a artefactelor, nu
că funcția curentă ar fi ocolită. Cele patru piloturi sunt din 22 august și **nu au
scuza asta**, deci trunchierea de acolo e defect viu, nu artefact vechi.

*(Am raportat întâi 171. Însumasem liniile unui raport trecut printr-un `tail`, adică o vedere
trunchiată — 261 e cifra din JSON-ul complet. Merită păstrat ca avertisment: agregarea unei vederi
parțiale arată exact ca o măsurătoare.)*

Restul, din JSON-ul complet: `end_mid_sentence` 3.681, `clipped_release` 1.855,
`start_mid_sentence` 573, `start_on_continuation` 432, `end_inside_word` 261, `orphan_tail` 176,
`required_context_outside` 76, `dead_tail` 55. Coada: median 0,40s / p90 0,40s pe 6.742 de ferestre.

## Batch R5a — finalul aterizează în afara unui cuvânt, 30 august 2026

**O singură modificare:** `_fit` snapează acum și finalul, ultimul lucru pe care îl face, mărginit de
maxim și de mediu, cu pull-back refuzat când ar coborî sub minim. Docstring-ul promitea „staying off
words" de la început; era adevărat doar pentru început, iar asimetria 261/0 e chiar demonstrația.

**Măsurat înainte/după cu `scripts/measure_boundary_snap.py`: 261 → 0**, zero refuzate pentru minim,
maxim sau mediu, 260 împinse înainte și 1 trasă înapoi. Deplasare mediană 0,10s, p90 0,66s — dar
**120 din 261 depășesc 0,15s**, deci presupunerea inițială („mutare deterministă sub 0,15s") era
greșită. Trei cazuri depășesc 3s, pe transcripte care conțin un token de 5,72s, unul de 5,14s și unul de
3,54s. **Nu am pus gardă pe durata cuvântului, și motivul e măsurat, nu de principiu:** p99 al
duratelor de token marchează 19 din cele 261 de mutări, p99.9 marchează exact cele trei extreme —
deci alegerea pragului s-ar face după ce vezi ce răspuns dă. Nici audio-ul nu tranșează: toate trei
extremele cad în intervale clasificate drept vorbire, cu RMS-ul activ, deci „adaugă secunde de
tăcere" nu e ceva ce a arătat cineva. Nici probabilitatea Whisper nu separă — 0,292 pentru „love" de
5,14s, dar 0,886 pentru un token de 3,54s și 0,997 pentru unul de 1,56s. O regulă statistică poate
spune că timestamp-ul e ciudat; nu poate spune unde se termină cuvântul.

`scripts/measure_boundary_snap.py` numește acum cele mai mari mutări per proiect, cu tokenul, durata
lui, percentila în distribuția transcriptului însuși și probabilitatea Whisper — ca un om să se poată
uita la ele, nu ca un prag să decidă în locul lui.

**Ce NU e măsurat, și e următorul pas:** efectul celor 261 de mutări asupra scorurilor de graniță,
dedupe-ului, shortlist-ului, judge-ului și board-ului. Cere un re-score end-to-end pe o **CLONĂ**, nu
pe proiectele-baseline. **Și nu doar pe cele patru piloturi:** cele trei mutări extreme sunt în
`39c89ae2e16e` și `43a509687a33`, deci clonele acelor două surse trebuie incluse, altfel gate-ul nu
testează chiar riscul tocmai descoperit. Baseline-ul R0 rămâne dovada despre cele 58 de fișiere vechi; rezultatele noi
se ștampilează cu versiunea nouă de boundary și nu se amestecă în aceeași comparație. S8 vine după.

## Batch R6 — detectorul de subtitrări arse, 30 august 2026

**Prima parte din R6 e livrată:** `source_captions.py` răspunde `present | absent | unknown` la
întrebarea dacă sursa are deja text ars. **4 din 4 împotriva etichetelor umane** —
`pilotf81b` (go ghost) `present`, celelalte trei `absent`. Nimic nu e aplicat: detectorul nu stinge
al doilea strat de captions.

**Două abordări au eșuat înainte și sunt scrise integral** în
[`../../../clipper-caption-detection.md`](../../../clipper-caption-detection.md) — euristici de
luminozitate pe benzi, iar a doua a pus un negativ peste pozitiv. Documentul există exact ca tabelul
celor patru abordări eșuate de facecam: ca să nu fie rerulate.

**Ce funcționează:** greutățile CRAFT ale lui `easyocr` sunt deja în cache local, deci detecția merge
offline, 5s pe sursă pe GPU. Dependența e OPȚIONALĂ — fără ea totul e `unknown` și analiza nu costă
nimic.

**Care încotro merg răspunsurile, fiindcă prima versiune le avea invers:** `present` = sursa are deja
captions → se **dezactivează** stratul ClipForge; `absent` → se **păstrează**; `unknown` → nu se
schimbă nimic. Deci `present` e răspunsul scump — unul greșit livrează un clip fără captions deloc —
și de aceea se obține din dovezile unei singure benzi, în timp ce `absent` cere toate benzile clar
negative.

**Discriminatorul e lățimea, și e contraintuitiv:** watermark-ul lui `pilot6b38` e în 13 din 14 cadre,
mai persistent decât adevăratul pozitiv, dar are 0,079 din lățimea cadrului față de 0,45. Persistența
singură ar fi clasificat greșit. Fiecare negativ pică pe altă axă.

**Trei corecții din review:** semantica inversată de mai sus; judecarea doar a benzii cu cele mai
multe cadre lăsa un watermark să mascheze pista reală de subtitrare; și un detector care aruncă pe
fiecare cadru ieșea `absent` — testul meu verifica benzile goale și nu verdictul. Numitorul e acum
ce s-a **analizat**, nu ce s-a eșantionat.

**Ce NU e demonstrat:** pragurile au fost alese cu răspunsul la vedere, pe patru surse. `calibrated:
false` călătorește cu fiecare verdict.

**A doua parte, livrată 30 august 2026:** `caption_placement.py` + `caption_placement_vocab.py`
(ce acoperă caption-ul ars, per shot, în pixeli de output) și `caption_choice.py` (de ce stă acolo:
poziția propusă, alternativele respinse, motivul). Nu mișcă niciun cadru.

Trei axe ținute separat — `conflicts` măsurat, `unavailable` nefurnizat, `refused` furnizat greșit.
`share` e reuniunea peste semnale și cutii, nu maximul, și e un plafon inferior când nu s-a măsurat
tot (`share_complete`); cu nimic măsurat e `None`, nu 0,0. Lista `COMPOSITIONS` e APLICATĂ: o
compoziție necunoscută e refuzată, nu tratată drept `crop`.

**Constatare vie, neremediată:** un `safe_zone` cu NaN trece de `captions._norm_rect` și se suprapune
peste caseta de caption la FIECARE poziție din scanare, ștergând căutarea de bandă și mutând
caption-ul livrat (măsurat 0,3092 → 0,4642). `caption_choice` o numără; nu o repară, fiindcă reparația
mută captions livrate.

**Browser chrome:** prima măsurătoare (easyocr, 20 de proxy-uri × 8 cadre uniforme) a dat 0
potriviri din 453 de tokeni citiți, și am concluzionat greșit că nu există pozitiv în corpus.
Review-ul uman v2 listează „browser UI" printre defectele rendererului v3 — pozitivul există, opt
instantanee dintr-o sursă de ore nu l-au atins. Se remăsoară pe EXPORTURILE randate, un cadru pe
secundă. Ce rămâne valabil: recognizer-ul citește prost conținutul care contează (`pilotf81b`,
singura sursă cu captions arse cunoscute, nu a dat niciun token peste confidence 0,5), deci recall-ul
e mic și `not_detected` nu poate însemna „curat".

**Rulate pe corpusul real** (`scripts/audit_caption_placement.py`, 101 sidecare): **27 din 27** de
clipuri cu shot `fit` și geometrie cunoscută au caption-ul pe banda de letterbox, deci contrastul e
starea fiecărui export letterboxat, nu un caz-limită. Și 8 din 99 de poziții nu pot fi produse de
regula de azi — exporturi anterioare scanării de bandă; unul singur stă peste un keep-out de față.

**Contrastul e aritmetic** (`caption_contrast.py`): un glif cu contur se separă de orice fundal prin
una dintre cele două culori ale lui, deci există o podea, cu formă închisă: rădăcina pătrată a
contrastului dintre cele două culori proprii ale glifului, `sqrt(21) = 4,58` pentru alb în negru. Un
sweep pe 256 de griuri o supraestima cu 0,025, fiindcă un pixel colorat are luminanța între nivelele
de gri. **Descoperirea e în paletă, nu pe letterbox:** toate
umpluturile trec, dar highlight-ul lui `Neon Pop` (2,33) și al lui `Viral Gradient` (2,72) nu pot
GARANTA 3,0:1. Citirea precisă: există o luminanță de fundal la care separarea coboară acolo, nu că
textul nu atinge niciodată 3:1. Ambele sunt presete livrabile azi, iar reparația schimbă o culoare
livrată — decizie pentru om, nu pentru un batch în umbră.

**`evidence_map.py` mapează dovezile în cadrul de output**, prin lanțul rendererului: proxy → sursă
(scări separate), `+ canvas_offset` fiindcă padding-ul precede cropul, fereastra de crop, apoi o
scalare. Refuză un shot al cărui crop se mișcă, unul care nu poate fi citit, dimensiuni proxy lipsă și
o cutie malformată; o cutie care ratează cropul e `off_frame`, nici refuz nici zero.

**Primele cifre pentru semnalul de față:** 2126 de shot-uri, **964 fără niciun eșantion în fereastra
lor** (detectorul eșantionează la ~2s, shot-urile au 1–4s), 1162 cu fețe mapate, 459 de cutii care
ratează cropul, și **26 din cele 99 de clipuri plasate au caption-ul peste o față detectată**. Cifra e
un PLAFON INFERIOR: cele 964 de shot-uri neeșantionate plus semnalele de UI și text-sursă, care nu au
detecție per shot, fac ca **zero clipuri să aibă cazul cel mai rău stabilit**.

**`source_chrome.py` detectează chrome-ul de player în exporturile randate.** 14 din 14 exporturi
Moist îl au (5–32 de cadre cu hit fiecare), față de UN singur hit în 27 de exporturi din alte zece
proiecte — deci pragul de 2 cadre separă complet, cu marjă de 5 la 1. Ipoteza cu URL-uri a picat:
39 de potriviri pe pozitive, toate la confidence 0,00–0,01, zero URL-uri reale. `not_detected` nu
înseamnă curat, și `is_a_warning()` o spune în cod.

**DEFECT LIVRAT, GĂSIT DE OM ȘI REPARAT (31 august 2026):** pe shot-urile `fit` caption-ul nu era
desenat deloc. `pad=...:color=black@0` face barele TRANSPARENTE ca să se vadă blur-ul, iar arderea
subtitrărilor peste cadrul acela scria textul în planurile de culoare și lăsa alfa pe zero — deci
`overlay` îl compunea afară. Pe `crop` cadrul e opac și caption-ul supraviețuia; pe `fit` caption-ul
stă în bară prin construcție și dispărea. **331 de secunde din 27 dintre cele 88 de clipuri stocate,
7,7% din corpus, un clip mut pe 88% din lungime.** `caption_placement` raportase acele 27 ca „caption-ul
cade pe banda de letterbox" — adevărat despre geometrie, și nu întreba niciodată dacă textul e desenat.
Reparat mutând `subtitles` după `overlay`; dovedit re-randând clipul și extrăgând același cadru.

**Ce a mai rămas din R6:** detecția per-shot pentru UI și pentru textul sursei — lipsește un DETECTOR
nu un mapper, fiindcă `regions.hud` are 8 dreptunghiuri în tot corpusul și toate sunt cutii fixe de
colț — și cablarea în sidecar. `panels_to_keep_out` nu e mapper generic: sare peste shot-urile de
față.

## DEFECT LIVRAT, GĂSIT DE OM ȘI REPARAT — 31 august 2026

**Pe shot-urile `fit` caption-ul nu era desenat deloc.** `pad=...:color=black@0` face barele de
letterbox TRANSPARENTE ca să se vadă copia blurată; arderea subtitrărilor peste cadrul acela scria
textul în planurile de culoare și lăsa alfa pe zero, deci `[bg][fg]overlay` îl compunea afară. Pe
`crop` cadrul e opac și caption-ul supraviețuia; pe `fit` caption-ul stă în bară prin construcție.

**331 de secunde din 27 dintre cele 88 de clipuri stocate, 7,7% din corpus.** `pilotee0e/a9f653576eb7`
era mut pe 88% din lungime, `pilot2c8a/ad1b8004ece9` pe 71%.

Reparat mutând `subtitles` după `overlay` în `dynamic_render`. Dovedit în trei pași: cadru din
exportul stocat fără text deși `.ass` are un eveniment activ; reproducere izolată cu două etichete,
una peste sursă și una peste bară, din care apărea doar prima; re-randare și același cadru cu textul
acolo.

**Toate cele 58 de exporturi ale piloturilor sunt re-randate** (`scripts/rerender_pilots.py`, 54 de
minute, zero refuzuri). Originalele sunt în `<proiect>/exports_pre_caption_fix/`, iar scriptul refuză
să pornească a doua oară peste ele. Verificat automat: banda de caption s-a schimbat pe 19 din 27; pe
celelalte 8 caption-ul era deja în cadrul sursă.

**Ce a arătat instrumentul, și de ce nu l-a prins:** `caption_placement.ON_LETTERBOX` raporta acele
27 de clipuri drept „caption-ul cade pe banda de letterbox". Adevărat despre GEOMETRIE, și nu întreba
niciodată dacă textul e desenat. O măsurătoare despre unde cade ceva nu e o măsurătoare că acel ceva
există.

## DOUĂ CIFRE CORECTATE, ambele găsite verificând reparația

**Poziția livrată e în `.ass`, nu în `caption_plan.y_pct`.** Sidecar-ul stochează PRESET-ul; `.ass`
poartă ce a decis `resolve_position` după keep-out-uri, și ăla se arde. Coincid pe 53 din 99 și
diferă pe 46, cu până la 933 de pixeli. `caption_corpus` citește acum din `.ass`, cu
`caption_y_source` care spune `ass` sau `caption_plan`. Corecția mută „clipuri cu caption peste o
față detectată" de la **26 la 38**.

**„Ar produce regula de azi poziția asta" NU se poate răspunde din sidecar.** `clipper_captions`
re-plasează caption-ul la randare cu keep-out-urile stocate PLUS `panels_to_keep_out(panels, shots)`,
iar `panels` nu e stocat nicăieri. Comparând `y_pct` a ieșit „8 din 99"; comparând poziția arsă cu
aceleași keep-out-uri incomplete a ieșit 54. **Niciunul nu era un fapt despre regulă.** E
`unavailable` acum, cu motivul numit, și nu pică rularea — e o proprietate a formatului stocat.
