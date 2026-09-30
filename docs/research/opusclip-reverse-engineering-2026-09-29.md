# OpusClip — reverse engineering din surse publice (29 septembrie 2026)

Am ales OpusClip: e liderul pieței și singurul concurent rulat de utilizator pe același VOD ca
[testul nostru din cloud](clipper-cloud-test-2026-09-29.md). Sursele sunt doar publice:

- centrul de ajutor (inclusiv `llms.txt`);
- specificația OpenAPI a API-ului lor;
- paginile de produs, anunțurile de angajare și contul oficial de X;
- ce a produs contul utilizatorului pe VOD-ul nostru.

Nimic decompilat, niciun endpoint privat, nicio autentificare în contul utilizatorului. Completează ce spun deja
[`clipping-apps-survey-2026-09-29.md`](clipping-apps-survey-2026-09-29.md) și
[`clipper-scoring-selection-2026-09-29.md`](clipper-scoring-selection-2026-09-29.md) despre OpusClip: layout-uri,
Brand Vocabulary, scorul comprimat.

| etichetă | înseamnă |
|---|---|
| **[OBS]** | spus de OpusClip într-o sursă publică, citat din ea |
| **[ASM]** | dedus de noi din indicii; nivelul de încredere e scris lângă |
| **[MĂS]** | măsurat de noi |
| **[CF]** | starea ClipForge, verificată în cod pe 29.09 |

---

## 1. Pe scurt

1. **Pipeline-ul e unul clasic, cu etape și reluări** [OBS]. Un proiect trece prin `IMPORT → CURATE → REFINE →
   RENDER → UPLOAD`. Pe drum poate fi `STALLED`, cu un contor de blocări, un job de cluster și un URL de worker.
   Fiecare prompt nou creează o rulare nouă (`runId`, `curationId`).
2. **Două modele de curare, plus modele pe gen** [OBS]:
   - **ClipBasic** se bazează doar pe transcript și pe poziția vorbitorilor, iar cuvintele-cheie trebuie să apară în
     vorbire;
   - **ClipAnything** e multimodal: descrie fiecare scenă ca într-un „scenariu” (transcript, momente, rezumat
     vizual, sentiment) și dă fiecărei scene o notă de viralitate;
   - peste ele stau 18 genuri, cu subgen, fiecare cu „modele de curare potrivite”.
3. **Produc multe clipuri și le ordonează după un scor** [OBS]:
   - 23–32 de clipuri la 30–60 de minute de video, 32–42 la 60–120;
   - scorul merge de la 0 la 99 și are patru componente: hook, flow, value, trend;
   - titlul automat intră doar pe primele 10;
   - un clip poate fi lipit din **mai multe intervale** din sursă (`timeRanges`), iar pagina principală spune că
     momentele sunt „rearanjate”.
4. **Stack-ul probabil** [ASM, încredere medie–mare]:
   - ASR din familia Whisper: API-ul spune că ascunde câmpurile `seek`, `tokens` și log-probabilitățile, care sunt
     exact câmpurile de segment ale lui Whisper;
   - diarizare în stilul pyannote (etichete `SPEAKER_00`);
   - Google Cloud (URI-uri GCS);
   - import prin yt-dlp (`YTDLP_LINK`);
   - Gemini, cel puțin pentru șabloanele generative (postarea lor oficială).
5. **Învață din utilizatori** [OBS]: are butoane de „inimă” și „thumbs down” pe fiecare clip. Datele utilizatorilor
   individuali (nu enterprise) „pot” fi folosite la îmbunătățirea modelelor.
6. **Nu pomenesc nicăieri semnale de la public** [OBS, prin absență]: chat, clipurile spectatorilor, Most Replayed.
   ClipAnything declară că nu dă rezultate pe video fără sunet sau aproape static, pentru că se ghidează după
   schimbările de sunet și de imagine. Pentru stream-uri, semnalele de la public rămân locul unde ClipForge poate
   face altceva decât ei (CA4, CA7, RS4–RS6).

## 2. Pipeline-ul, citit din contractul API [OBS → ASM]

Specificația (`https://help.opus.pro/api-reference/openapi.json`, OpenAPI 3.0, 21 de rute) descrie:

| ce apare în API | ce înseamnă probabil |
|---|---|
| `stage`: `PENDING, QUEUED, IMPORT, CURATE, REFINE, RENDER, UPLOAD, COMPLETE, STALLED` + `stallCount` | coadă cu etape. `REFINE` e separat de `CURATE`: granițele și finisarea vin după ce se aleg momentele [ASM] |
| `clusterJobId`, `cluster`, `workerUrl` | workeri pe clustere dedicate, adresați per proiect [ASM] |
| id de clip `{project_id}.{curation_id}`, plus `runId` | un proiect are mai multe curări: reprompt, alt gen |
| `timeRanges`: „original time ranges of the clip” | clipul e o **listă** de intervale din sursă, deci poate fi lipit din bucăți |
| `keywords`, `promptName`, `genre`, `subgenre`, `title`, `description`, `hashtags`, `text` | clipul poartă metadatele curării: gen detectat, prompt, textul vorbit |
| `promptRecommendations` pe proiect | după analiză, propun prompturi pentru acel video |
| `sourcePlatform`: `YOUTUBE, UPLOADED, YTDLP_LINK, GDRIVE, ZOOM, STREAM_YARD` | linkurile generice trec prin yt-dlp, ca la noi |
| `uriForPreview/uriForExport`: „Google Cloud Storage URI” | infrastructură Google Cloud |
| **niciun câmp de scor** pe clipul exportabil | scorul de viralitate e doar în interfață (planurile Pro/Starter) |

Transcriptul exportat [OBS]:

- are paragrafe cu `speaker` opțional („SPEAKER_00”) și cuvinte cu timpi și `isFillerWord`;
- „ASR internals (seek, tokens, log-probabilities, etc.) are stripped”;
- detecția cuvintelor de umplutură e doar în engleză, iar modelele de transcriere și de umplutură au fost
  schimbate în ianuarie 2025.

## 3. Cum aleg momentele [OBS]

| | ClipBasic | ClipAnything |
|---|---|---|
| material | doar talking-head | orice: vlog, sport, TV, jocuri, video aproape fără vorbire |
| ce citește | doar transcriptul; imagine limitată la poziția vorbitorilor | fiecare cadru, prin indicii vizuale, audio și de sentiment: obiecte, scene, acțiuni, sunete, emoții, text pe ecran |
| prompt | cuvinte-cheie care trebuie să apară în vorbire | limbaj natural („găsește toate scenele cu râs”, „momentele cu opinii tari”) |
| structură | — | „narrative templates”, adică structuri de poveste, toate active implicit |
| „scene analysis” | — | fiecare scenă e descrisă ca într-un scenariu (transcript, timpi din sursă, rezumat vizual, sentiment), apoi primește o notă de viralitate |
| timp | — | 20–40 de minute. Pentru VOD-ul nostru de 64 de minute, OpusClip i-a estimat utilizatorului ~13 minute |
| limită declarată | — | nu dă rezultate pe video fără sunet sau aproape static |

Peste ambele se aplică:

- **genul**: `Auto, Q&A, Commentary, Marketing, Webinar, Motivational speech, Podcast, Academic, Listicle, Product
  reviews, How-to, Comedy, Sports commentary, Church, News, Vlog, Gaming, Others`. Blogul lansării 3.0 spune că
  fiecare gen are „propria versiune de momente virale”.
- **durata**: implicit „Auto (0–3 min)”. Pe API, găleți de 0–30, 30–60, 60–90 și 90–180 s.
- **intervalul** din sursă de analizat.

**Arhitectura probabilă a lui ClipAnything** [ASM, încredere medie]: descrieri dense ale scenelor (VLM + ASR +
evenimente audio + sentiment), transformate într-un „scenariu”. Un LLM notează scenele și le combină după o
structură de poveste. E aceeași arhitectură video → text → LLM ca judge-ul nostru. Diferența e că ei descriu și
imaginea, nu doar vorbirea.

## 4. Ce livrează [OBS]

- **Numărul de clipuri** crește cu lungimea video-ului:

  | lungimea video-ului | clipuri |
  |---|---|
  | 0–3 min | 1–2 |
  | 3–10 min | 3–14 |
  | 10–30 min | 5–21 |
  | 30–60 min | 23–32 |
  | 60–120 min | 32–42 |
  | peste 120 min | 42–55 |

  ClipAnything dă „puțin mai multe” decât ClipBasic. Limita e de 10 ore de video.
- **Scorul de viralitate:** 0–99, pe hook, flow, value și trend, plus relevanța față de prompt la ClipAnything.
  Rolul lui e să ordoneze lista. Cât de puțin prezice a fost deja documentat: 82% dintre clipuri au ≥ 80, iar la
  gaming chiar ei spun că „corelează slab” (SL doc).
- **Titlul automat** („Auto headline”) apare doar pe **primele 10** clipuri. E pornit implicit la încărcările
  manuale.
- **Montajul:** subiectul e urmărit automat după voce și mișcare. Pentru orice obiect există urmărire manuală.
  Speech Enhancement separă vocea pe o pistă proprie.
- **OpusSearch:** indexează biblioteca după transcript, subiecte, vorbitori, „mood” și momente-cheie, cu căutare în
  limbaj natural și filtre People/Topics/Mood/Duration.

## 5. Stack-ul probabil — indiciile și cât cântăresc [ASM]

| indiciu public | ce deducem | încredere |
|---|---|---|
| transcriptul API „strips seek, tokens, log-probabilities” | ASR din familia Whisper (acestea sunt câmpurile de segment Whisper) | mare |
| `speaker: "SPEAKER_00"`, „când diarizarea e disponibilă” | diarizare pyannote sau WhisperX (aceeași convenție de nume) | medie |
| URI-uri GCS, upload „to Google Cloud Storage” | Google Cloud | mare |
| `YTDLP_LINK` | import prin yt-dlp | mare |
| contul OpusClip pe X: șabloane animate „powered by Gemini Omni Flash” | Gemini pentru funcțiile generative. **Nu** dovedește că și curarea e pe Gemini | mare pentru generare, mică pentru curare |
| „we train our AI to find the best moments”; inimă și thumbs down; datele non-enterprise „may” antrena modele | buclă de feedback de la utilizatori | medie |
| anunțuri de angajare: Lead AI Researcher, AI Engineer („multimodal AI engineering, video ML” e un plus), Data Scientist | echipă mică de modele; nimic specific despre stack | — |

Un document de pe Scribd („OpusClip Technology and Backend”) spune „Whisper-based”. E urcat de un terț, neverificat,
deci nu îl folosim ca dovadă. Doar confirmă indiciul din API.

## 6. Ce înseamnă pentru ClipForge [ASM, cu CF acolo unde e verificat]

| la OpusClip | la ClipForge | ce merită luat |
|---|---|---|
| 30–40 de clipuri la o oră, sortate după scor; omul alege | un board de 10, plus 159 de alternative stocate [MĂS, testul] | alternativele se pot arăta ca listă sortată; nu e nevoie de alt model |
| clipuri lipite din mai multe intervale, „rearanjate” | `dead_air.py` taie pauzele dintr-o singură fereastră; n-am găsit o cale care să lipească momente depărtate [CF, după o căutare de `time_ranges`/`stitch`/`rearrange`] | un experiment „hook întâi” (CA16), prin review orb |
| gen detectat + modele pe gen | 10 profiluri de conținut [CF, SL doc] | — (există) |
| prompt în limbaj natural; OpusSearch | lipsă | CA12 (căutare pe atomi + rerank LLM) |
| inimă / thumbs down pe clip | approve/reject + `clip_feedback` [CF] | SL9 (perechi) folosește mai bine aceleași clickuri |
| descrieri vizuale ale scenelor | judge doar pe transcript | descrierea cadrelor-cheie pentru judge; cost de măsurat înainte |
| nimic despre chat sau clipurile spectatorilor | idem, deocamdată | aici poate ClipForge să fie diferit pe stream-uri (CA4, CA7, RS4–RS6) |

## 7. Comportamentul pe VOD-ul nostru — protocolul

Scriptul [`opus_blackbox.py`](clipper-cloud-test-2026-09-29/opus_blackbox.py) primește lista de clipuri OpusClip:
rang, început, sfârșit și, opțional, scor și titlu. Un clip lipit se dă ca mai multe intervale cu același rang.

Pentru fiecare clip și pentru ferestre aleatoare de aceleași lungimi măsoară:

- dacă începe și se termină pe o graniță de segment din transcript;
- cuvintele pe secundă;
- excitarea audio;
- ponderea râsului din chat, la decalajul măsurat (~10 s);
- suprapunerea cu board-ul ClipForge și cu cel mai apropiat candidat ClipForge.

Un semnal care desparte clipurile OpusClip de ferestrele aleatoare e unul pe care OpusClip îl urmează plauzibil. Cu
un singur VOD, rezultatul descrie, nu modelează.

**Autotest pe board-ul ClipForge** [MĂS], ca să se vadă ce măsoară scriptul:

| semnal | board ClipForge | ferestre aleatoare |
|---|---|---|
| începe pe o graniță din transcript | 6/10 | 69/400 |
| se termină pe o graniță | 7/10 | 58/400 |
| cuvinte pe secundă (mediană) | 2,61 | 1,45 |
| excitare audio (mediană) | 3,81 | 1,91 |
| râsul din chat, față de mediana VOD-ului | 2,41× | 1,37× |

- Board-ul nostru favorizează porțiunile vorbite și zgomotoase.
- Clipurile lui sunt urmate de mai mult râs decât ar fi la întâmplare, chiar dacă niciunul nu prinde cele 12 vârfuri
  cele mai mari (testul, §5).
- În interiorul board-ului, scorul ClipForge corelează cu râsul (ρ = +0,85) și cu excitarea audio (+0,77). Pe 10
  clipuri, cu un interval restrâns de scoruri, asta nu înseamnă mult.

**Ce trebuie pentru OpusClip:** lista clipurilor din contul utilizatorului. Intervalul din VOD apare în „scene
analysis” la ClipAnything și în editor. Dacă lipsește, prima frază a clipului e de ajuns ca să îl găsim în
transcript.

```
python opus_blackbox.py picks.json <audio>/speech.wav chat.json <data>/db/clipforge.db <project_id>
```

## Surse

- Indexul centrului de ajutor — https://help.opus.pro/llms.txt
- Specificația API — https://help.opus.pro/api-reference/openapi.json ;
  https://help.opus.pro/api-reference/endpoints/create-project ;
  https://help.opus.pro/api-reference/schemas/curation-preferences ;
  https://help.opus.pro/api-reference/endpoints/transcripts/get-transcript
- Scorul de viralitate — https://help.opus.pro/docs/article/virality-score
- ClipAnything — https://help.opus.pro/docs/article/9947095-clip-anything ;
  https://help.opus.pro/docs/article/clipanything-qa-2 (față de ClipBasic) ;
  https://help.opus.pro/docs/article/clipanything-qa-4 (timp) ; https://help.opus.pro/docs/article/clipanything-qa-7
  (narrative template) ; https://help.opus.pro/docs/article/clipanything-qa-10 (limite) ;
  https://help.opus.pro/docs/article/clipanything-qa-11 (scene analysis) ;
  https://help.opus.pro/docs/article/clip-anything-prompt-manual ; https://www.opus.pro/clipanything
- Rezultatul — https://help.opus.pro/docs/article/9442054-about-the-result-clips ;
  https://help.opus.pro/docs/article/how-many-clips ; https://help.opus.pro/docs/article/select-clip-length ;
  https://help.opus.pro/docs/article/auto-headline ; https://help.opus.pro/docs/article/video-length
- Genuri — https://www.opus.pro/blog/opusclip-clip-different
- Montaj și căutare — https://help.opus.pro/docs/article/subject-tracking ;
  https://help.opus.pro/docs/article/speech-enhancement ; https://help.opus.pro/docs/article/opussearch ;
  https://help.opus.pro/docs/article/filler-word-recognition ; https://help.opus.pro/docs/article/select-keywords
- Date și antrenare — https://help.opus.pro/docs/article/opusclip-data-training
- Pagina principală („rearranges them”) — https://www.opus.pro/
- Gemini pentru șabloanele generative — https://x.com/OpusClip
- Angajări — https://jobs.ashbyhq.com/opusclip
