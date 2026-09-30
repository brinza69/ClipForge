# Clipper — ce fac alte aplicații de clipping și ce merită preluat

Cercetare din **29 septembrie 2026**, făcută cu conectorul Firecrawl: căutare web, paginile de produs și de
documentație ale producătorilor, indexul de articole științifice și indexul pentru dezvoltatori (README-uri,
documentații de API). Continuă [`ai-stream-clipper-competitive-analysis.md`](ai-stream-clipper-competitive-analysis.md)
(30 iulie: doar OpusClip și clipping.net) — ce e acolo nu se repetă aici. Scorarea, judge-ul LLM, ranker-ul și
selecția au documentul lor: [`clipper-scoring-selection-2026-09-29.md`](clipper-scoring-selection-2026-09-29.md).

| etichetă | înseamnă |
|---|---|
| **[OBS]** | spus public de producător pe paginile lui. **Nemăsurat de noi** — o etichetă, nu o măsurătoare (CLAUDE.md) |
| **[LIT]** | articol publicat sau documentație tehnică (API, bibliotecă) |
| **[ASM]** | deducția noastră; poate fi greșită; se verifică înainte de a construi pe ea |
| **[CF]** | starea ClipForge, verificată în cod pe 29.09, cu fișierul și linia |

Nu s-a copiat cod, text sau design de la nimeni; doar concepte de produs publice.

---

## 1. Pe scurt

Ce aduce nou cercetarea, în ordinea valorii pe efort:

1. **Semnale „de mulțime", gratuite, pe care nu le citim deloc** [CF: zero apariții în `server/`]:
   - clipurile făcute deja de spectatori pe Twitch (`vod_offset`, `view_count` în API-ul Helix);
   - „Most Replayed" de pe YouTube (`heatmap` în yt-dlp) — `fetch_metadata` îl are în mână și îl aruncă;
   - chat-ul (replay YouTube prin yt-dlp, Twitch prin TwitchDownloaderCLI). Eklipse îl declară unul din cele trei
     semnale principale, iar Lightor a măsurat cum se folosește corect: nu volumul brut, ci volum + mesaje scurte +
     mesaje asemănătoare, cu un decalaj de reacție de 23–27 s.
2. **Fraza „clip that / clip it"** în transcript — Twitch Auto Clips și Eklipse o tratează ca declanșator explicit.
3. **Încadrarea pe intervale** (layout pe segment, ca în OpusClip) + **detecția vorbitorului activ** (Klap;
   Light-ASD, open-source) — exact golul rămas deschis la închiderea Clipper-ului (RSK 3/3).
4. **Vocabular propriu la transcriere** (OpusClip „Brand Vocabulary"; `hotwords` în faster-whisper).
5. **Bleep/mute pe audio** pentru înjurături — acum mascăm doar textul subtitrării.
6. **Bucla de performanță pe metricile care contează acum** (engaged views, procentul vizionat, curba de retenție),
   nu pe vizualizări brute, care din 2025/2026 măsoară doar acoperirea.

Planul, cu efortul și poarta de validare pentru fiecare punct, e în §9.

---

## 2. Ce s-a analizat

### 2.1 Clippere pentru stream / gaming

| produs | cum găsește momentele [OBS] | ce are în plus [OBS] |
|---|---|---|
| **Twitch Auto Clips** (nativ, alfa, numai canale în engleză) | speech-to-text + AI: sentiment pozitiv și entuziasm al streamerului, momente amuzante, interacțiuni; AI-ul „evaluează conținutul **și activitatea din chat**"; comanda vocală „Twitch, Clip That / Clip It" face un clip din **ultimele 60 s** | titluri din **citate directe** din stream; subtitrare implicită; cadrare verticală ajustabilă; clipuri gata în general în 30 min după stream; în lucru: un **flux de review înainte de publicare**; „merge cel mai bine cu audio curat; calitatea variază când vorbesc mai mulți deodată" |
| **Eklipse** | „gameplay (kills, wins, achievements), vârfuri audio (entuziasm, râs, furie) și **sentimentul din chat (spike-uri de emote, viteza mesajelor)**, combinate într-un scor de potențial viral"; momente „hype" = explozii de chat, **sub trains**; detecție specifică pe joc (CS2: ace, runde clutch; CoD: multi-kill; LoL: teamfight); comanda vocală „clip it" | facecam + gameplay stivuite în 9:16; „AI Content Agent" = ordinea și ritmul clipurilor într-o compilație; export 1440p ca să rămână lizibil HUD-ul; clipuire automată a fiecărui stream conectat; postare programată |
| **StreamLadder ClipGPT** | tot VOD-ul (Twitch / Kick / YouTube): interacțiuni amuzante cu chat-ul, momente intense, reacții haotice, „clutch plays, hilarious deaths, chat-driven chaos"; scor de viralitate; analiza începe **cât încă se descarcă** VOD-ul | detecție facecam **inclusiv pentru VTuberi**; auto-crop |
| **StreamGen** | schimbări de gameplay, intensitate audio, reacții pe cameră, context din chat | cenzurarea cuvintelor; predare pre-editată în DaVinci Resolve / Premiere |
| **Insights Capture, Powder** | înregistrare locală + evenimente de joc live (Insights: „10.000+ titluri") | rulează pe PC-ul jucătorului, nu în cloud |
| **WayinVideo** | AI specific pe joc (LoL, Valorant, CS2, Fortnite, GTA V) + **căutare după cuvinte-cheie** („Find Moments") | — |
| **Streamlabs Cross Clip, Framedrop** | Cross Clip nu caută momente, doar reîncadrează un clip ales; Framedrop analizează VOD-uri Twitch/YouTube | layout-uri facecam/gameplay; compilații; extensie Chrome |

### 2.2 Clippere generaliste

| produs | ce e relevant pentru noi [OBS] |
|---|---|
| **OpusClip** (față de iulie) | 7 layout-uri: Fill, Fit (4:3 + benzi), Split (numai dacă ambii apar **împreună** în cadru), Three, Four, Screenshare (ecranul sus, vorbitorul jos), **Gameplay (30% vorbitor sus, 70% joc jos)**. Se aplică „când e aplicabil", cu alt layout automat altfel; **layout schimbabil pe un singur segment din timeline**; reîncadrare manuală. Plus: Brand Vocabulary, Censor Curse Words (și în API), Speech Cleanup, Auto SFX, „narrative templates", prompt-uri ClipAnything, export Premiere/DaVinci, API + server MCP, import automat de pe YouTube |
| **Klap** | „active speaker detection"; reîncadrare care „înțelege conținutul"; „hook title"; analytics; API deschis; numește scorul de viralitate OpusClip „decorativ" |
| **Submagic** | finisare: zoom automat sincronizat cu transcriptul (6 stiluri), SFX, emoji, B-roll, eliminarea tăcerilor, descriere + hashtag-uri |
| **Choppity, AI Video Cut, Vizard** | criterii AI personalizate / prompt-uri pe tip de clip (teaser, tutorial, review); calendar de postare; analytics |

Majoritatea surselor din 2.2 sunt pagini de marketing sau comparații scrise de un concurent. Au fost folosite doar
pentru *ce funcții există*, niciodată pentru *cât de bine merg*.

### 2.3 Open-source și literatură

| sursă | ce aduce |
|---|---|
| **Lightor** (arXiv:1910.12201) [LIT] | highlight-uri din chat Twitch: trei trăsături, decalajul reacției, pragul de volum (§3.1) |
| arXiv:1707.08559 · arXiv:1807.09715 · arXiv:2407.12002 · arXiv:1708.02210 [LIT] | chat + imagine pe LoL · cameră + voce + joc, nesupervizat, pe PUBG · aliniere temporală între modalități · calibrarea decalajului comentariilor |
| Twitch Science, blog 2017 [LIT] | emote-urile din chat-ul clipurilor, ponderate TF-IDF, grupate în 250 de clustere tematice (PogChamp = uimire/hype, BibleThump = emoție) |
| **ClipsAI** [LIT] | reîncadrare = diarizare pyannote + PySceneDetect + fețe MTCNN/MediaPipe; `min_segment_duration=1.5`, `samples_per_segment=13` |
| **opensource-clipping** (NaufalRizqullah) [LIT] | camera trece pe vorbitorul activ; pillarbox blurat când vorbesc doi în aceeași scenă; split dinamic; `deadzone 0.15`, `smooth 0.30`, `jitter 5 px`, `snap 0.25` |
| **TalkNet** (ACM MM 2021), **Light-ASD** (CVPR 2023) [LIT] | „fața asta vorbește acum", din buze + audio: 92,3% / 94,1% mAP pe AVA-ActiveSpeaker |
| PANNs, EfficientAT, YAMNet [LIT] | etichetarea evenimentelor audio pe AudioSet (~520 de clase: râs, țipăt, urale, împușcătură, explozie): mAP 0,431 (CNN14) / până la 0,483 / model foarte mic |
| yt-dlp, TwitchDownloaderCLI, twitch-dl, Twitch Helix, YouTube Analytics API, SponsorBlock, faster-whisper [LIT] | sursele de date și parametrii din §3–§8 |

---

## 3. Semnale pentru găsirea momentelor

| semnal | cine îl folosește | ClipForge azi [CF] | lot |
|---|---|---|---|
| transcript + LLM | toți | da: story engine, ancore, judge (`story.py`, `llm_select.py`, `llm_judge.py`) | — |
| energie / vârfuri audio | Eklipse, StreamGen | da (`signals.py`) | — |
| râs / strigăt | Eklipse („laughter, rage") | euristic, fără model: tare + vocal + fără cuvinte (`vocal_bursts.py`) | CA10 |
| **chat: viteză, emote, lungime** | Eklipse, StreamGen, Twitch Auto Clips, Lightor | **nu există** | CA7 |
| **clipurile spectatorilor** | niciun produs nu o declară; e forma directă a „implicit crowdsourcing"-ului din Lightor [ASM] | **nu există** | CA4 |
| **Most Replayed (YouTube)** | — | **nu există** | CA2 |
| **„clip that / clip it"** | Twitch Auto Clips, Eklipse | **nu există** | CA1 |
| evenimente de joc (kill, clutch) | Eklipse, Insights, Powder, WayinVideo | nu, deliberat (analiza din iulie, §6) | amânat; proxy prin CA10 |
| cadre / scene / mișcare | OpusClip ClipAnything | da: mișcare, tăieturi, fețe | — |
| prompt / cuvinte-cheie | OpusClip, WayinVideo, Choppity, AI Video Cut | nu (gol declarat în iulie) | CA12 |
| segmente de evitat (sponsor, audio amuțit) | — | nu | CA3, CA4 |

### 3.1 Chat-ul — ce a măsurat Lightor și cum ar intra la noi

[LIT] arXiv:1910.12201, pe VOD-uri Twitch de Dota 2 și LoL:

- **Volumul singur nu ajunge.** Metoda de comparație care citește doar curba numărului de mesaje (Toretter) a
  nimerit începutul momentului în sub 20% din cazuri; corecția decalajului a dat de ~3 ori mai mult. Boții și
  discuțiile fără legătură umflă volumul.
- **Trei trăsături**, pe ferestre glisante de 25 s, normalizate la [0, 1], combinate cu regresie logistică:
  numărul de mesaje; **lungimea medie** (la un moment bun lumea scrie scurt: emote); **similaritatea** mesajelor
  (toată lumea spune același lucru).
- **Decalajul**: începutul momentului = vârful chat-ului − c. Constanta c, învățată din date etichetate, a rămas
  între **23 și 27 s** indiferent câte videoclipuri de antrenare s-au folosit — „timpul de reacție" al
  spectatorilor.
- Precizie@K **70–90%**, cu **un singur** videoclip etichetat pentru antrenare.
- Condiție: **peste 500 de mesaje/oră** (80% din VOD-urile canalelor top-10 de Dota 2 o îndeplineau).
- Limită: chat-ul se entuziasmează și la momente din afara subiectului (pauze, pregătiri).
- Două vârfuri la mai puțin de 120 s unul de altul au fost tratate ca același moment.

[LIT] Twitch Science: aceleași emote, ponderate TF-IDF, spun **tema** clipului. [ASM] De aici se poate deriva tipul
momentului (amuzant / hype / tensionat / trist) pentru arhetip, titlu și hashtag-uri.

**De unde vin datele** [LIT]:

- YouTube: `yt-dlp --write-subs --sub-langs live_chat --skip-download <URL>` → `<id>.live_chat.json`. Există
  numai pentru videoclipuri care au fost live (`was_live`, deja în `fetch_metadata`).
- Twitch: `TwitchDownloaderCLI chatdownload -u <vod> -o chat.json` (JSON cu toate câmpurile originale; opțional
  emote BTTV/FFZ/7TV) sau `twitch-dl chat json`.
- Kick: extensii publice analizează chat-ul VOD-urilor Kick, deci datele există; calea de acces e **de
  verificat** [ASM].

**Cum intră în ClipForge** [CF + ASM]:

- un artefact nou (`storage.py` e singurul loc care știe căile), descărcat la ingest, independent de transcriere;
- o serie pe aceeași grilă ca celelalte semnale din Pass A, cu decalajul aplicat;
- rolul: **propunător de candidați și coroborare**, nu clasament de unul singur — din cauza limitei de mai sus;
- stări proprii: `unavailable` (nu există chat), `too_sparse` (sub pragul de volum), `ok`. Un chat lipsă nu e
  „zero entuziasm" — ce nu s-a putut citi nu trece niciodată drept rezultat (CLAUDE.md);
- c se derivă din distribuția noastră (clipurile aprobate), nu se copiază 25 s din articol: întârzierea stream-ului
  diferă între platforme și setări [ASM]. Aceeași lecție ca la `quote_resolver`: fereastra se derivă, nu se alege;
- **capcana din ranker**: `FEATURE_ORDER` (`ranker.py:41`) citește o trăsătură lipsă ca `0.0`, deci „fără chat"
  s-ar confunda cu „chat liniștit". Trăsătura are nevoie de un flag de disponibilitate alături, iar schimbarea
  vectorului cere bump de `MODEL_VERSION` (`ranker.py:31`). Nu există încă un model antrenat
  (`MIN_TRAINING_EXAMPLES = 40`, `ranker.py:35`) — **acum** e momentul ieftin.

### 3.2 Semnale gratuite din metadate

| semnal | ce e exact [LIT] | cum se folosește [ASM] |
|---|---|---|
| **Twitch Get Clips** | per clip: `vod_offset` (secunda din VOD unde începe clipul; `null` dacă VOD-ul nu e disponibil sau încă nu s-a calculat — întârziere de ordinul minutelor), `duration`, `view_count`, `created_at`, `creator_name`, `title` | densitatea clipurilor și vizualizările lor pe axa VOD-ului = unde s-au uitat oamenii deja; titlurile lor = context |
| **Twitch Get Videos** | `muted_segments`: porțiunile amuțite pentru drepturi de autor | un candidat care le atinge: refuz sau avertisment |
| **YouTube Most Replayed** | yt-dlp `heatmap` = listă de `{start_time, end_time, value}`: 100 de segmente, valori normalizate 0–1. Curba din player e netezită; gropile de lângă vârfuri sunt artefacte de desen, nu date | prior pe **regiuni**: rezoluția e durata/100 (3,6 min pe un VOD de 6 h), deci nu pentru granițe. Lipsește la videoclipurile cu puține vizualizări → `unavailable` |
| **capitole** | yt-dlp `chapters` | prior pentru `episodes.py`; context pentru titlu |
| **SponsorBlock** | prin yt-dlp: `sponsor`, `selfpromo`, `interaction`, `intro`, `outro`, `preview`, `filler`, `music_offtopic`, `poi_highlight` | exclude reclamele citite; `poi_highlight` = pozitiv slab. Acoperirea pe VOD-uri de stream e probabil mică — se măsoară pe sursele noastre înainte |

[CF] `downloader._extract_info_sync` are deja dicționarul complet yt-dlp și, la `return`
(`server/services/downloader.py:232`), păstrează doar titlul, canalul, durata etc.; `heatmap` și `chapters` se pierd.

---

## 4. Încadrare și layout — golul rămas la închiderea Clipper-ului

[CF] Din `handover/areas/clipper/CURRENT.md` (28.09): încadrarea automată taie oameni pe reacții și pe clipurile cu
două camere (RSK 3/3); soluția livrată e încadrarea manuală din editorul de reacție, care face numai
`game_top_face_bottom`; încadrarea pe intervale („Kai doar când vorbește") și atribuirea vorbitorului nu există.

Ce fac ceilalți:

- **Layout pe segment** [OBS OpusClip]: fiecare bucată din timeline are layout-ul ei; un layout se aplică numai
  „când e aplicabil" (Split doar dacă ambii apar împreună, Screenshare doar dacă se detectează un ecran), altfel se
  trece automat pe altul.
- **Facecam sus** [OBS]: OpusClip Gameplay = 30% vorbitor sus / 70% joc jos; Eklipse stivuiește facecam + gameplay;
  ghidul clipping.net citat în iulie: „facecam + action".
- **Vorbitorul activ** [OBS Klap; LIT open-source]: camera urmărește cine vorbește; când vorbesc doi deodată,
  cadru larg / pillarbox blurat; split dinamic între ecran întreg și ecran împărțit.
- **Fără ping-pong** [LIT]: segment minim 1,5 s (ClipsAI); zonă moartă de 15% și prag de „snap" de 25% înainte de
  o tăietură dură (opensource-clipping).
- **Modelul potrivit** [LIT]: diarizarea (pyannote) spune „vorbitorul A", nu *care față*; ASD (TalkNet, Light-ASD)
  răspunde direct „fața asta vorbește acum". Light-ASD: 94,1% mAP pe AVA, gândit să fie ușor.

Recomandarea, în trei trepte, fiecare utilă singură:

1. **CA8a** — varianta cu camera sus în editorul de reacție (e deja pe lista de update-uri: „varianta (a)" din
   next-36 §2).
2. **CA8b** — intervalele de layout, manual: o listă `(t0, t1, layout, rects)` pe clip, editată în editor și legată
   de versiunea sursei și de intervalul clipului, cum leagă deja `reaction_edit.py` un layout întreg.
3. **CA9** — propunerea automată a intervalelor: Light-ASD pe urmele de față existente → probabilitatea „vorbește"
   pe eșantion → histerezis + segment minim → layout pe interval. Suprapunere sau nesiguranță → cadrul larg, sigur
   (aceeași regulă ca în `edit_profiles.py`: o clasificare slabă cumpără o editare *mai sigură*). O voce din afara
   cadrului (Kai vorbește, dar nu e în imagine) nu are o față care vorbește → se păstrează cadrul curent.
   **Poarta**: măsurătoarea din pixelii fișierului randat folosită la RSK (bb9b, 5d89, 8c89; 800/800 de
   eșantioane). Propunerea automată trebuie să păstreze persoanele cel puțin cât varianta manuală acceptată.

---

## 5. Transcriere

- [OBS OpusClip] Brand Vocabulary: corectezi un cuvânt în editor → „aplică peste tot" → opțional „adaugă în
  vocabular"; lista de nume proprii se aplică tuturor transcrierilor următoare.
- [LIT faster-whisper] `hotwords` pune termenii în promptul **fiecărei** ferestre; `initial_prompt` intră direct
  doar în prima (după autorul PR-ului #731). Buget ~224 de tokeni. Recuperează nume *aproape* auzite, nu nume pe
  care modelul nu le cunoaște.
- [CF] Apelul din `server/services/transcriber.py:508` folosește `word_timestamps=True` și `vad_filter=True`, fără
  `hotwords` sau `initial_prompt`.
- **CA5**: o listă de vocabular pe proiect și pe canal (streameri, co-streameri, jocuri, argou), trimisă ca
  `hotwords`, alimentată și de un „înlocuiește peste tot" în editorul de subtitrare. **Poarta**: re-transcrierea unei
  surse stocate, cu numărul de apariții corecte ale numelor cunoscute înainte/după (numitorul tipărit întâi).
- [OBS Twitch] Auto Clips cere audio curat și „variază când vorbesc mai mulți deodată". [ASM] Pe stream-urile cu
  joc sau muzică tare, separarea vocii înainte de Whisper ar putea ajuta — numai ca experiment măsurat.

---

## 6. Finisare

| funcție | cine [OBS] | ClipForge [CF] | recomandare |
|---|---|---|---|
| bleep / mute la înjurături | OpusClip (Censor Curse Words, și în API), StreamGen | numai în text: `mask_profanity` (`captions.py:75`), cu `profanity_mask` implicit `False` (`clipper_settings.py:125`); sub-scorul `safety` | **CA6**: liniște sau ton peste intervalele cuvintelor deja marcate, în **același** encode (regula 7), opțional, implicit oprit |
| loudness | OpusClip Speech Enhancement, Submagic | da: compander + loudnorm + limiter (`ffmpeg_tools.py`) | — |
| zoom automat, SFX, emoji, B-roll | Submagic, OpusClip | zoom: `push_amount` e 0.0 în toate stilurile stocate (CLAUDE.md) | numai ca experiment prin `blind_review` (CA16) |
| titlu / hook | Twitch: titluri din citate directe; Klap: „hook title"; vidIQ: dacă hook-ul vine la secunda 8, taie începutul lent | titlu extractiv + LLM opțional (`headline.py`); latența hook-ului în `story.py` | direcția e confirmată; nimic nou de construit |
| intro/outro, brand kit, muzică | OpusClip | nu (gol din iulie) | neschimbat |

---

## 7. Flux de lucru

- **Import automat** [OBS]: OpusClip importă ultimele videoclipuri YouTube; Eklipse clipuiește fiecare stream
  conectat; Twitch livrează în ~30 min după stream. [CF] La noi URL-ul se introduce manual. **CA13**: un watcher pe
  canal (Twitch Get Videos / feed-ul RSS YouTube) care pune `clipper_ingest` în coadă.
- **Review înainte de publicare** [OBS]: Twitch abia adaugă un astfel de flux; Insights îi reproșează lui Eklipse
  că nu are. [CF] Avem blind review, preflight și verificarea umană ca regulă de release — avantaj, de păstrat.
- **Compilații** [OBS Eklipse, Framedrop]: „best of stream" din clipurile aprobate. **CA14**.
- **Export în editor** [OBS OpusClip, StreamGen]: proiect Premiere/DaVinci pentru finisare manuală. **CA15** (FCPXML).

---

## 8. Bucla de performanță

[CF] `ranker.py` e complet și inactiv (40 de exemple etichetate necesare); `feedback.py` etichetează numai acțiunile
manuale. Nu există nicio preluare de metrici de pe platforme.

Ce s-a schimbat la metrici (articole care citează centrul de ajutor YouTube, [OBS]):

- din **31 martie 2025**, o vizualizare de Short se numără la simplul start, fără durată minimă; vechea definiție a
  devenit **engaged views**. Din **24 august 2026**, la fel pentru toate formatele. Vizualizările brute măsoară
  acoperirea, nu calitatea;
- metrica primelor 1–2 secunde e **„viewed vs swiped away"** — există numai în YouTube Studio.

[LIT] API-ul YouTube Analytics are `engagedViews`, `averageViewPercentage` și curba de retenție (`audienceWatchRatio`,
`relativeRetentionPerformance` pe dimensiunea `elapsedVideoTimeRatio`); „viewed vs swiped away" nu apare în listă.

**CA11**: preluarea din YouTube Analytics API pentru canalele proprii — `engagedViews`, `averageViewPercentage`, curba
de retenție — la o vârstă fixă a postării (de ex. 72 h; [ASM] sursele de creatori spun că majoritatea
vizualizărilor unui Short vin în 48–72 h), normalizate pe canal. „Viewed vs swiped away" rămâne introdus manual.
Curba mai răspunde la două întrebări rămase deschise: unde pleacă lumea la început (hook) și la final (EN3, coada de
după ultimul cuvânt, e oprit, iar dovada de până acum vine dintr-un singur experiment orb, limitat).

Niciodată etichete din vizualizări brute. Un clip fără metrici e `unavailable`, nu „slab".

---

## 9. Planul propus

Efort: **S** = zile, **M** = o săptămână sau două, **L** = mai mult. Fiecare punct e un lot separat, cu poarta lui.

| ID | ce | valoare | efort | poarta de validare |
|---|---|---|---|---|
| CA1 | „clip that / clip it / clip this / somebody clip" în transcript → candidat care se termină la frază | mare pe stream-uri în engleză | S | pe transcriptele stocate: câte apariții, câte cad pe clipuri aprobate, câte fals-pozitive („clip" în alt sens) |
| CA2 | păstrarea `heatmap` + `chapters`; heatmap ca prior pe regiuni, capitole pentru `episodes.py` | mare pe VOD-uri YouTube populare | S | câte surse au heatmap (numitorul întâi); suprapunerea vârfurilor cu clipurile aprobate |
| CA3 | SponsorBlock: excluderea sponsorizărilor și a autopromovării | mică–medie | S | acoperirea pe sursele noastre; la ~0, nu se livrează |
| CA4 | Twitch: clipurile spectatorilor (`vod_offset`, `view_count`) + `muted_segments` | mare pe VOD-uri Twitch | S–M | aceeași suprapunere; credențiale de aplicație Twitch [ASM: Get Clips acceptă un token de aplicație — de verificat] |
| CA5 | vocabular → `hotwords` | medie | S | numele cunoscute, recunoscute înainte/după, pe o sursă re-transcrisă |
| CA6 | bleep/mute audio pe cuvintele marcate | medie (monetizare, siguranța contului) | S | test prin handler: liniște exact pe intervalele cuvintelor, un singur encode |
| CA7 | pista de chat (YouTube + Twitch): trăsăturile Lightor + emote pe categorii + decalaj calibrat | mare | M | precizia propunerilor pe clipurile aprobate; c derivat din distribuția noastră; `unavailable` / `too_sparse` testate prin `main()` |
| CA8 | camera sus + intervale de layout manuale în editor | mare (golul RSK) | M | acceptare umană pe sursele R2 și Speed/Kai, ca la RSK |
| CA9 | Light-ASD → intervale propuse automat | mare | M–L | harness-ul RSK din pixeli: persoanele păstrate ≥ varianta manuală |
| CA10 | etichetator audio (EfficientAT / PANNs / YAMNet): râs, țipăt, urale, împușcătură, explozie | medie | M | comparație cu `vocal_bursts` pe aceleași ferestre etichetate; bump de `MODEL_VERSION` |
| CA11 | metrici YouTube Analytics → etichete pentru ranker | mare pe termen lung | M | 40 de clipuri etichetate; nicio etichetă din vizualizări brute |
| CA12 | căutare în limbaj natural (embedding-uri pe atomi + rerank LLM; opțional cadre SigLIP) | medie | M–L | întrebări cu răspuns cunoscut pe o sursă etichetată |
| CA13 | watcher de canal → ingest automat | medie | M | — |
| CA14 | compilație „best of stream" | medie | M | — |
| CA15 | export FCPXML | mică | S–M | — |
| CA16 | experimente: zoom pe reacții, SFX, cold open | necunoscută | S fiecare | blind review; nu înainte de CA7–CA11 |

Ordinea recomandată: CA1 → CA2 → CA5 → CA6 (ieftine, independente), apoi CA7 și CA8 în paralel, CA9 după CA8,
CA10–CA11 când vine rândul ranker-ului. Trăsăturile noi pentru ranker (din CA1, CA2, CA4, CA7, CA10) ar trebui să
intre **înainte** de primul antrenament: exemplele etichetate înainte de o trăsătură nouă o citesc ca `0.0`.

ID-urile au prefixul **CA** ca să nu se confunde cu loturile R0–R8 ale Clipper-ului și nici cu sursa „R2" din
handover.

---

## 10. Ce nu recomand

| nu | de ce |
|---|---|
| detectoare pe joc (OCR pe killfeed, tabele de scor) | poziția din iulie rămâne: investiție mare pentru fiecare joc; revenim numai dacă se lucrează constant pe 1–2 jocuri |
| „trend score" | tot nu există o sursă legitimă; Klap afirmă analiză de trenduri și știri, dar nu se poate verifica |
| scorul de viralitate prezentat ca predicție | chiar Klap numește scorul OpusClip „decorativ"; păstrăm rangul relativ + sub-scorurile |
| B-roll AI, voce AI, dublaj pe clipuri de reacție | nu se potrivesc cu reacția reală a streamerului |
| modele vizuale în cloud ca implicit | ClipForge rămâne local; cloud-ul rămâne opțional (ca `review_vision.py`) |

## 11. Ce confirmă cercetarea despre direcția actuală

- Titlul extractiv (`headline.py`) = practica Twitch (titluri din citate directe).
- Verificarea umană înainte de postare = ce adaugă acum Twitch și ce i se reproșează lui Eklipse că nu are.
- Scorul relativ cu sub-scoruri, în locul unui număr opac (critica, interesată, a lui Klap la adresa OpusClip).
- Procesarea locală = diferențiatorul pe care îl vând Insights și Powder.
- Benzile de durată (`PLATFORM_BANDS` în `scoring_profiles.py`: TikTok 15–45 s, Shorts 15–60 s) cuprind intervalul
  de 15–35 s raportat de creatori pentru Shorts — dovadă slabă, nu o măsurătoare, și nu un motiv să redăm duratei
  ponderea din scor, pusă la zero deliberat.

---

## Surse

Produse și documentație (consultate 29.09.2026):

- Twitch Auto Clips — https://help.twitch.tv/s/article/auto-clips
- Twitch API: Clips — https://dev.twitch.tv/docs/api/clips/ · Reference (Get Clips, Create Clip From VOD) —
  https://dev.twitch.tv/docs/api/reference · Videos (`muted_segments`) — https://dev.twitch.tv/docs/api/videos/
- Eklipse — https://eklipse.gg/ · https://eklipse.gg/features/
- StreamLadder ClipGPT — https://www.streamladder.com/clipgpt/ai-clipping ·
  https://www.streamladder.com/clipgpt/auto-clip/twitch
- StreamGen (comparație scrisă de producător) — https://streamgen.cc/blog/best-ai-clipping-tool/
- Insights vs Eklipse — https://insights.gg/blog/insights-vs-eklipse
- WayinVideo — https://wayin.ai/blog/eklipse-alternative/ · https://wayin.ai/blog/opusclip-alternative/
- OpusClip — https://help.opus.pro/llms.txt · https://help.opus.pro/docs/article/layout-and-reframing ·
  https://help.opus.pro/docs/article/brand-vocabulary.md · https://help.opus.pro/docs/article/virality-score ·
  https://help.opus.pro/docs/article/9947095-clip-anything · https://help.opus.pro/docs/article/clipanything-qa-7.md
- Klap — https://klap.app/alternatives/opus-clip
- Submagic — https://www.submagic.co/features/auto-zooms · https://www.submagic.co/features/magic-clips
- Comparații (numai pentru lista de funcții) — https://exemplary.ai/opus-clip-alternative ·
  https://www.choppity.com/blog/best-opus-clip-alternatives/ · https://www.aivideocut.com/free-opus-clip-alternative
- YouTube Analytics API, metrici — https://developers.google.com/youtube/analytics/metrics
- Metricile Shorts după 2025 — https://gyre.pro/blog/understanding-the-youtube-shorts-algorithm-a--guide (citează
  https://support.google.com/youtube/answer/10059070 și https://support.google.com/youtube/answer/2991785) ·
  https://1of10.com/blog/youtube-shorts-analytics/ · https://vidiq.com/blog/post/Clip-youtube-videos-long-to-short/

Instrumente și biblioteci:

- yt-dlp `heatmap` — https://github.com/yt-dlp/yt-dlp/issues/7100 · „Most Replayed", reverse engineering —
  https://priyavr.at/blog/reversing-most-replayed/
- yt-dlp `live_chat` — https://stackoverflow.com/questions/55789448
- TwitchDownloaderCLI — https://github.com/lay295/twitchdownloader · twitch-dl chat —
  https://github.com/ihabunek/twitch-dl
- SponsorBlock prin yt-dlp — https://wiki.sponsor.ajay.app/w/Segment_Categories
- faster-whisper `hotwords` — https://github.com/SYSTRAN/faster-whisper/pull/731
- ClipsAI — https://www.clipsai.com/references/resize · opensource-clipping —
  https://github.com/NaufalRizqullah/opensource-clipping
- TalkNet — https://github.com/TaoRuijie/TalkNet-ASD · Light-ASD pe AVA —
  https://opencodepapers-b7572d.gitlab.io/benchmarks/audio-visual-active-speaker-detection-on-ava.html
- PANNs — https://github.com/qiuqiangkong/audioset_tagging_cnn · EfficientAT — https://github.com/fschmid56/EfficientAT
- Twitch Science, emote și teme — https://blog.twitch.tv/en/2017/10/05/using-chat-emotes-as-signals-for-content-themes-13c31d4a0f74/

Articole:

- Lightor — https://arxiv.org/abs/1910.12201
- Video Highlight Prediction Using Audience Chat Reactions — https://arxiv.org/abs/1707.08559
- Deep Unsupervised Multi-View Detection of Video Game Stream Highlights — https://arxiv.org/abs/1807.09715
- A Multimodal Transformer for Live Streaming Highlight Prediction — https://arxiv.org/abs/2407.12002
- Video Highlights Detection and Summarization with Lag-Calibration based on Concept-Emotion Mapping of
  Crowd-sourced Time-Sync Comments — https://arxiv.org/abs/1708.02210
