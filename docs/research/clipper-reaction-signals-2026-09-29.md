# Clipper — stream-uri de reacție: vocea cui e, ce spune chatul, ce e logistică

Cercetare din **29 septembrie 2026**, cu conectorul Firecrawl, pornită de la
[testul din cloud](clipper-cloud-test-2026-09-29.md). Testul a arătat trei probleme pe un stream de reacție:

- clipul #1 era vocea naratorului din trailer;
- board-ul n-a prins momentele la care a reacționat chatul;
- 3 din 10 clipuri erau logistică.

Continuă [`clipping-apps-survey-2026-09-29.md`](clipping-apps-survey-2026-09-29.md) (CA) și
[`clipper-scoring-selection-2026-09-29.md`](clipper-scoring-selection-2026-09-29.md) (SL). Planul de aici e RS1–RS8.

| etichetă | înseamnă |
|---|---|
| **[OBS]** | spus public de un producător sau într-o documentație; nemăsurat de noi |
| **[LIT]** | articol publicat; cifrele sunt ale autorilor, pe datele lor |
| **[MĂS]** | măsurat de noi, pe VOD-ul din test (un singur stream) |
| **[CF]** | starea ClipForge, verificată în cod pe 29.09, cu fișierul |
| **[ASM]** | deducția noastră; se verifică înainte de a construi pe ea |

---

## 1. Pe scurt

1. **Vocea streamerului se poate separa de conținut fără un model antrenat de noi.** Metoda e auto-înrolarea: vocea
   se învață din momentele în care fața din cameră vorbește, apoi fiecare segment e comparat cu ea. Pe video „din
   sălbăticie”, metoda audio-vizuală a avut 7,7% erori de diarizare, față de 20–24% doar din audio [LIT]. Diarizarea
   doar din audio e slabă tocmai pe astfel de surse: pyannote greșește 44,6% pe AVA-AVD (filme), față de 11,2% pe
   VoxConverse [LIT].
2. **Pe acest stream, chatul reacționează la ~10 s după momentul zgomotos, nu la 25–35 s** [MĂS]. Emote-urile de râs
   urmăresc sunetul cu 8–10 s întârziere (corelație 0,26, peste orice valoare obținută la întâmplare). Emote-urile de
   hype nu îl urmăresc deloc. Cu decalajul măsurat, **0 din 12** momente din chat cad în board. Cifra de 3/12 din
   testul anterior venea dintr-un decalaj ales după rezultat.
3. **Chatul completează imaginea, nu o înlocuiește** [LIT]. Pe League of Legends, chatul singur are F1 43, video-ul
   72, iar împreună 75.
4. **Analiza de sentiment obișnuită nu merge pe chat** [LIT]: VADER nimerește cât ghicitul, iar dicționarele de
   emote-uri ajung la ~62% (macro-F1). 60% dintre utilizatori văd emote-uri din extensii (BTTV, FFZ, 7TV), deci
   dicționarul trebuie citit pe canal, din API-urile lor publice.
5. **Logistica nu e tratată explicit nicăieri** [CF]: nici în calea euristică, nici în promptul LLM. Pentru YouTube
   există un model antrenat pe SponsorBlock, dar datele sunt CC BY-NC-SA, adică necomerciale [OBS]. Pentru Twitch nu
   există etichete, deci soluția practică e o etichetă în judge plus un lexicon mic, măsurate înainte [ASM].

## 2. Vocea cui e: streamerul sau conținutul

### 2.1 Ce s-a văzut

- **[MĂS]** Clipul #1 e naratorul trailerului („The sprawling satirical reimagining…”), iar #8 e dialog din trailer
  sau din joc.
- **[MĂS]** Transcriptul amestecă cele două voci, așa că titlul și scorul se sprijină pe cuvintele altcuiva.
- **[CF]** Handover-ul Clipper-ului spune deja că „atribuirea vorbitorului nu există”.

### 2.2 Ce spune literatura

- **Diarizarea doar din audio** (cine vorbește și când, în grupuri anonime) [LIT]. Erorile pyannote, ca DER, pentru
  3.1 → `community-1`:

  | setul de date | 3.1 | `community-1` |
  |---|---|---|
  | VoxConverse | 11,2% | 11,2% |
  | AMI | 18,8% | 17,0% |
  | AVA-AVD (filme) | 49,7% | 44,6% |
  | Ego4D | 51,2% | 46,8% |

  Pe video „din sălbăticie”, jumătate din timp e atribuit greșit. Modelele cer un token Hugging Face și acceptarea
  condițiilor. Licența e MIT pentru 3.1 și CC-BY-4.0 pentru `community-1` [OBS]. `community-1` are un mod
  „exclusiv”, cu un singur vorbitor activ odată, care se aliniază ușor cu timpii cuvintelor. WhisperX leagă deja
  faster-whisper de pyannote prin `assign_word_speakers` [OBS].
- **Auto-înrolarea audio-vizuală** (Chung et al., 2020, setul VoxConverse) [LIT]:
  1. Detecția vorbitorului activ (ASD) arată când vorbește fața din imagine. Au folosit două detectoare, iar un
     segment conta doar dacă ambele erau de acord. Asta a redus alarmele false din râs și muzică.
  2. Din segmentele sigure rezultă embedding-ul vocii acelei persoane.
  3. Vorbirea fără față activă se atribuie prin distanța cosinus. Dacă nu trece pragul, rămâne „necunoscut”.

  Rezultat: DER 7,7%, față de 23,8% la sistemul de referință doar audio (20,2% cu îmbunătățirea vorbirii). Capcana
  notată de autori: SyncNet se aprinde când fonemele din vorbirea de fundal se potrivesc cu buzele.
- **Același principiu fără urmărirea feței** (VGG, „Look, Listen and Recognise”, 2024) [LIT]: se aleg din indicii
  audio-vizuale câteva exemple audio foarte sigure pentru fiecare personaj. Apoi toate segmentele se clasifică după
  ele.
- **Personal VAD** (Ding et al., 2020) [LIT]: detecție de vorbire condiționată de vocea-țintă, cadru cu cadru, în trei
  clase: fără vorbire, vorbitorul-țintă, alt vorbitor.
- **Embedding-uri de vorbitor** [OBS]: ECAPA-TDNN (SpeechBrain) are EER 0,80% pe VoxCeleb1-O, iar WeSpeaker ResNet34
  0,72%. Sunt cifre pe înregistrări curate. Pe un stream (compresie, joc în fundal) vor fi mai slabe [ASM]. Înrolarea
  din același VOD elimină însă diferența de microfon și de canal [ASM].
- **ASD ușor** [LIT]: Light-ASD are 94,1% mAP pe AVA-ActiveSpeaker, cu 1,02 M parametri și 0,63 GFLOPs; LR-ASD are
  94,5%. E același model propus pentru încadrare în CA9, deci aceeași ieșire servește la ambele.

### 2.3 Ce propun [ASM]

- **Înrolarea din VOD:**
  - vocea streamerului se învață din câteva minute în care fața din camera lui vorbește sigur (ASD, cu pragul
    strict);
  - fallback: grupul de voce cu cel mai mult timp de vorbire.
  - Nu e nevoie de ASD pe tot VOD-ul, ajunge un eșantion.
- **Ponderea streamerului pe candidat:** timpul de vorbire al vocii înrolate raportat la tot timpul de vorbire.
  Intră ca trăsătură pentru scorer și ca rând în trace.
- **Marcaje în transcript:** judge-ul și titlurile primesc transcriptul marcat `[streamer]` / `[conținut]`.
  - Un clip de reacție are nevoie de conținut, dar nu poate fi **doar** conținut.
  - Titlul nu se ia din vorbirea conținutului.

## 3. Chatul, mai precis

### 3.1 Decalajul se măsoară, nu se presupune [MĂS]

**Metoda**, în [`chat_lag.py`](clipper-cloud-test-2026-09-29/chat_lag.py):

- sunetul, pe secundă: cât de tare e față de propria mediană pe ±60 s;
- chatul, pe secundă: ponderea mesajelor cu emote de râs sau de hype;
- ambele netezite pe 5 s;
- corelația lor, pentru decalaje între −30 și +90 s;
- ca bază de comparație: aceeași statistică după ce seria de chat e deplasată circular cu ≥ 300 s, de 200 de ori.

| seria de chat | cel mai bun decalaj | corelație | la întâmplare: p95 / maxim |
|---|---|---|---|
| râs + hype | +12 s | 0,132 | 0,095 / 0,163 |
| **doar râs** | **+8 s** (0,263 și la +10 s) | **0,263** | 0,112 / 0,183 |
| doar hype | fără legătură (−18 s) | 0,032 | 0,116 / 0,170 |

Stabilitate, pe varianta râs + hype:

- netezire 3/5/9/15 s → 13/12/10/8 s;
- sonoritate absolută în loc de relativă → 13 s;
- prima jumătate a VOD-ului → 14–15 s, a doua → 6 s.

**Ce înseamnă:**

- Chatul râde la ~10 s după momentul zgomotos. Emote-urile de hype („W”, „Pog”) nu urmăresc sunetul pe acest stream
  [MĂS].
- Cele 23–27 s de la Lightor se măsoară de la **începutul** unui highlight, care include pregătirea. Nu e aceeași
  mărime [LIT] [ASM]. Pentru un clip, vârful din chat minus ~10 s dă climaxul, iar granițele se caută înaintea lui.
- Comparația din testul anterior, refăcută cu decalajul măsurat: **0 din 12** momente la 10 s și la 15 s (un board
  aleator ar prinde 12%). Cifra de 3/12 la 35 s era un decalaj ales pentru că se potrivea. E capcana din CLAUDE.md,
  în care o verificare se compară cu ea însăși.

### 3.2 Emote-urile [LIT]

Kobs et al. (2020), pe 14,4 milioane de mesaje Twitch:

- Adnotatorii umani sunt de acord doar moderat asupra sentimentului unui mesaj (κ Fleiss 0,497).
- VADER are 34,0% macro-F1, practic cât ghicitul (32,7%).
- Dicționarele de emote-uri ajung la 60,5–61,7%, iar un CNN antrenat pe etichetele lor la 62,6%.
- 60% dintre utilizatorii chestionați folosesc BTTV sau extensii similare.

Seturile de emote ale fiecărui canal se citesc public [OBS]:

- BTTV: `api.betterttv.net/3/cached/users/twitch/{id}`;
- FFZ, prin BTTV: `…/3/cached/frankerfacez/users/twitch/{id}`;
- 7TV: `7tv.io/v3/users/twitch/{id}`.

Pe acest stream, categoria care poartă semnalul e râsul, nu hype-ul (§3.1) [MĂS]. O hartă mică emote → categorie
(râs, hype, șoc, tristețe) e deci mai utilă decât un scor de sentiment.

### 3.3 Reluarea chatului e plafonată pe canalele mari [MĂS]

- În test, volumul a stat între 505 și 640 de mesaje pe minut, cu un coeficient de variație sub 0,10.
- N-am găsit nicio documentație Twitch despre asta.
- Consecință: pe canalele mari, volumul nu poate fi trăsătură. Ponderile, lungimea mesajelor și asemănarea dintre
  ele rămân utilizabile.

### 3.4 Chat și conținut împreună [LIT]

- **Fu et al. (2017), League of Legends:**

  | model | NALCS (engleză) | LMS (chineză) |
  |---|---|---|
  | doar chat | 43,2 | 39,7 |
  | doar video | 72,2 | 69,2 |
  | chat + video | 74,7 | 70,0 |

  Scorurile sunt F1. Doar ~10% dintre spectatori scriu în chat.
- **KLive (Kuaishou, 2024):**
  - aliniază comentariile și transcriptul la cadre printr-un modul învățat (bazat pe DTW), nu printr-un decalaj fix;
  - folosește câte un embedding pentru fiecare streamer, pentru că publicul fiecăruia are alt gust;
  - se antrenează pe feedback implicit al utilizatorilor, folosit ca etichetă slabă.

- **Pentru noi** [ASM]: chatul e o trăsătură și o departajare în banda comprimată de scoruri (testul, §7). Nu e
  selectorul unic.

## 4. Logistica

### 4.1 Ce s-a văzut și ce face codul

- **[MĂS]** 3 din 10 clipuri din board: cum se opresc reclamele, green screen, full screen.
- **[CF]** Calea euristică are un lexicon de umplutură, dar e doar de ezitări („um”, „uh”, „like”;
  `server/services/clipper/candidate_terms.py`).
- **[CF]** Promptul de nominalizare LLM ignoră „narrating routine actions, listing inventory, filler, and stretches
  where nothing is resolved” (`server/services/clipper/llm_prompts.py`). Logistica stream-ului nu e numită.

### 4.2 Ce există

- **SponsorBlock-ML** [OBS]: un model pe transcript, antrenat pe baza de date SponsorBlock. Detectează sponsorizări,
  autopromovare și îndemnuri de tip „subscribe”. Datele sunt CC BY-NC-SA 4.0 (necomerciale), deci licența se verifică
  înainte de orice folosire în produs.
- **Spotify, „Detecting Extraneous Content in Podcasts”** (2021) [LIT]: clasificatori pe text și pe tiparele de
  ascultare. Scoaterea conținutului străin îmbunătățește rezumatele (ROUGE).
- **Cu LLM** [LIT]: GPT-4o pe 421 de transcripte pentru detecția reclamelor (2025, lucrare în curs). În ChildSafeAds
  2026, cel mai bun sistem doar pe transcript a avut macro-F1 0,652, dar la clasificarea reclamelor deja găsite
  (tip, categorie, risc), nu la detecția lor.
- **Twitch n-are un echivalent** [ASM]: categoriile SponsorBlock sunt doar pentru YouTube. Logistica de stream
  (reclame, setări, verificări de sunet, BRB) nu e acoperită.

### 4.3 Ce propun [ASM]

- **Logistica în promptul LLM:** „stream logistics: ads, settings, audio checks, overlays, BRB” trece pe lista de
  ignorat, iar judge-ul întoarce o etichetă `logistics`. Asta e partea ieftină.
- **Un lexicon mic pentru calea euristică** („turn off the ads”, „full screen”, „green screen”, „can you hear me”,
  „mic”, „subscribe”, „prime”), măsurat înainte. Precizia se verifică pe un eșantion etichetat din board-urile
  stocate, cu numitorul întâi. Un cuvânt-cheie e o etichetă, nu o măsurătoare.

## 5. Planul propus

| id | ce | valoare | cost | cum se verifică |
|---|---|---|---|---|
| RS1 | pe board-urile stocate: câte clipuri sunt dominate de altă voce decât a streamerului (etichetare umană pe un eșantion) | stabilește dacă §2 merită | S | numitorul întâi; `unavailable` pentru clipurile fără cameră |
| RS2 | înrolarea vocii streamerului din VOD (ASD strict + embedding ECAPA/WeSpeaker; fallback: vocea dominantă) | mare pe stream-urile de reacție | M | acord cu eticheta umană din RS1; refuz când înrolarea are prea puține secunde |
| RS3 | ponderea streamerului pe candidat + transcriptul marcat pentru judge și titluri | mare | S, peste RS2 | shadow + review orb, ca orice schimbare de selecție |
| RS4 | decalajul chatului estimat pe fiecare VOD (§3.1), cu refuz sub pragul obținut la întâmplare | mare pentru CA7 | S | `chat_lag.py` pe mai multe VOD-uri; decalajul stabil între jumătăți |
| RS5 | dicționarul de emote al canalului (BTTV/FFZ/7TV) + harta emote → categorie | medie | S | ce pondere din mesaje primește o categorie (numitorul întâi) |
| RS6 | trăsături de chat în vectorul candidatului (ponderea râsului la t + decalaj, lungimea, asemănarea) | mare | M | SL5/SL6 (lăsând deoparte câte un proiect), nu presupus |
| RS7 | logistica în promptul LLM + eticheta `logistics` din judge | medie | S | câte clipuri de logistică rămân pe board, înainte și după |
| RS8 | lexiconul de logistică pentru calea euristică | mică–medie | S | precizia pe un eșantion etichetat, înainte de livrare |

Ordinea: RS1 și RS4 întâi, pentru că sunt măsurători ieftine care decid dacă restul merită. Apoi RS7, cel mai ieftin
câștig, apoi RS2–RS3 și RS5–RS6. RS6 intră în ranker doar prin poarta SL6.

## 6. Ce nu recomand

- **Sentiment standard pe chat** (VADER și altele): nimerește cât ghicitul pe limbajul Twitch [LIT].
- **Un decalaj fix.** 25 s de la Lightor măsoară altceva, iar pe acest stream decalajul a fost ~10 s [MĂS]. Când
  estimarea nu trece pragul obținut la întâmplare, răspunsul e `unavailable`, nu o valoare implicită.
- **Diarizarea doar din audio pe stream-uri de reacție:** greșește aproape jumătate din timp pe video „din sălbăticie”
  [LIT].
- **SponsorBlock-ML în produs** înainte de verificarea licenței necomerciale.

## Surse

- Personal VAD (Ding et al., Odyssey 2020) — https://www.isca-archive.org/odyssey_2020/ding20_odyssey.pdf
- pyannote 3.1 — https://huggingface.co/pyannote/speaker-diarization-3.1
- pyannote `community-1` și modul exclusiv — https://huggingface.co/pyannote/speaker-diarization-community-1 ,
  https://www.pyannote.ai/blog/community-1
- Spot the conversation: speaker diarisation in the wild (Chung et al., 2020) — https://arxiv.org/abs/2007.01216
- Look, Listen and Recognise (2024) — https://arxiv.org/abs/2401.12039
- ECAPA-TDNN, SpeechBrain — https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- WeSpeaker — https://github.com/wenet-e2e/wespeaker
- Light-ASD (CVPR 2023) — https://arxiv.org/abs/2303.04439 ; LR-ASD (IJCV 2025) —
  https://duanhaihan.github.io/publications/2025/IJCV2025.pdf
- WhisperX — https://github.com/m-bain/whisperx
- Emote-Controlled (Kobs et al., ACM TSC 2020) — https://dl.acm.org/doi/10.1145/3365523 ,
  cod și date: https://github.com/konstantinkobs/emote-controlled
- Video Highlight Prediction Using Audience Chat Reactions (Fu et al., EMNLP 2017) — https://arxiv.org/abs/1707.08559
- A Multimodal Transformer for Live Streaming Highlight Prediction (KLive, 2024) — https://arxiv.org/abs/2407.12002
- Lag-calibration pe comentarii sincronizate (Ping, 2017) — https://arxiv.org/abs/1708.02210
- Lightor — https://arxiv.org/abs/1910.12201
- API-uri de emote: BTTV — https://betterttv.com/developers ; 7TV — https://7tv.app/api/docs ;
  listă de endpoint-uri BTTV/FFZ/7TV — https://gist.github.com/chuckxD/377211b3dd3e8ca8dc505500938555eb
- SponsorBlock-ML — https://github.com/xenova/sponsorblock-ml
- Detecting Extraneous Content in Podcasts (Spotify, 2021) — https://arxiv.org/abs/2103.02585
- Leveraging ChatGPT for Sponsored Ad Detection (2025) — https://arxiv.org/abs/2502.15102
- ChildSafeAds Shared Task 2026 — https://arxiv.org/abs/2608.19165
