# Klap — reverse engineering din surse publice (29 septembrie 2026)

Al treilea clipper desfăcut, după [OpusClip](opusclip-reverse-engineering-2026-09-29.md) și
[Eklipse](eklipse-reverse-engineering-2026-09-29.md). Klap e printre cele mai populare: declară 3,5 milioane de
creatori și 8,5 milioane de clipuri. Are un API public, fără aprobare prealabilă.

Sursele:

- documentația API (`docs.klap.app`): task-uri, proiecte, exporturi, formate de obiect, stiluri, prețuri,
  utilizatori gestionați;
- cele 60 de pagini din sitemap-ul `klap.app`: unelte, comparații, prețuri, API;
- **politica de confidențialitate** (actualizată pe 25.09.2026), DPA-ul și contractul-cadru pentru API.

Nimic din aplicație, nicio autentificare.

| etichetă | înseamnă |
|---|---|
| **[OBS]** | spus de Klap într-o sursă publică, citat din ea |
| **[ASM]** | dedus de noi; încrederea e scrisă lângă |
| **[CF]** | starea ClipForge, verificată în cod pe 29.09 |

---

## 1. Pe scurt

1. **Aici arhitectura nu se ghicește, e declarată** [OBS, politica de confidențialitate]:

   > „RunPod hosts the open-source models we use to transcribe your videos (WhisperX) and to follow the people in
   > them; OpenRouter, the model providers it routes to, and OpenAI receive your transcripts and clipping
   > instructions to find the best moments, score them and write titles, emojis and translations; Google (Gemini)
   > receives the video when it has no speech to transcribe.”

   Pe scurt: transcrierea e WhisperX, pe GPU-uri RunPod. Alegerea, scorarea și titlurile le face un LLM (prin
   OpenRouter și OpenAI) care primește **doar transcriptul** și instrucțiunile de tăiere. Gemini primește video-ul
   numai când nu există vorbire. Oamenii din cadru sunt urmăriți cu modele open-source, tot pe RunPod.
2. **E deci un clipper „transcript → LLM”,** din aceeași familie cu calea LLM din ClipForge [ASM, încredere mare].
   Sunetul, imaginea și chatul nu intră în alegere, cu excepția video-urilor fără vorbire. Marketingul spune
   altceva („scores every second of your video against patterns from millions of viral clips”, „trained
   specifically on viral short-form video data”), dar politica numește modele terțe. Un fine-tuning pe modelele
   OpenAI nu e exclus, doar nu e declarat nicăieri.
3. **Scorul:** în API e între 0 și 1 și vine cu o **explicație în text** (`virality_score_explanation`), deci e scris
   de LLM [ASM, încredere mare]. În marketing e 0–100, „după hook strength, emotional peak, dialogue clarity,
   pacing”.
4. **Contractul API** [OBS]:
   - `target_clip_count` și `max_clip_count`, implicit 10;
   - durate în secunde: `min_duration` 1, `max_duration` 180, `target_duration` 60;
   - `transcription_context`: nume, jargon, acronime, maximum 1.000 de caractere;
   - opțiuni de editare: subtitrări, reîncadrare, emoji, titlu-hook, eliminarea pauzelor;
   - un video de intrare are maximum 5 ore.
5. **Proiectul întors de API nu are timpii din sursă** [OBS], spre deosebire de `timeRanges` la OpusClip. Un client
   al API-ului nu poate lega clipul de VOD-ul original.
6. **Prețul pe operație** [OBS]: 0,44 $ un video de intrare, 0,32 $ un short generat, 0,48 $ un export. În aplicație,
   planurile sunt pe clipuri: 100 / 300 / 1.000 de clipuri pe lună, pentru 14 / 39 / 94 $ (prețul lunar la plata anuală).
7. **Pentru ClipForge** (§6):
   - WhisperX ar aduce alinierea pe cuvinte și diarizarea într-un singur pachet (RS2);
   - `transcription_context` confirmă propunerea CA5 (vocabular);
   - un fallback vizual pentru porțiunile fără vorbire lipsește și la noi.

## 2. Cât valorează sursele

Politica de confidențialitate e aici **cea mai precisă sursă tehnică**: numește furnizorii pe funcție. Marketingul
se contrazice cu ea și cu documentația API:

| subiect | marketing | API / politică |
|---|---|---|
| cum se aleg momentele | „trained on millions of viral clips”, „scores every second” | LLM-uri terțe pe transcript (politica) |
| scala scorului | 0–100 | 0–1 + explicație (API) |
| limbi | „52 languages”, urmat de o listă de 57 de nume | — |
| clipuri pe video | „a one-minute long video produces about 5 video clips” (prima pagină) · „10-15 short clips per hour of long-form video” · podcast de 30 de minute: „6-8 clips”, interviu de două ore: „20+” | implicit 10 pe task (API) |
| lungimea sursei | „up to 4 hours” (pagina de sport) | 5 ore (API) |
| Twitch | „Klap accepts MP4, MOV, and URLs from YouTube, Twitch, and Google Drive” (pagina de sport) | „More integrations (Google Drive, Twitch) coming soon” (API) |
| planul Pro | „upgrade to Klap Pro for just $29/month” (prima pagină) | 39 $ pe lună la plata anuală (pagina de prețuri) |
| unde se procesează | — | „primary data processing infrastructure” în SUA (DPA); contractul-cadru spune că firma e în Franța și procesează „în general” în SEE |

## 3. Pipeline-ul, reconstruit

1. **Intrarea** [OBS]: link YouTube, link de stocare (S3, GCP) sau URL public. Aplicația web acceptă și Twitch și
   Google Drive.
2. **Transcrierea** [OBS]: WhisperX pe RunPod. WhisperX înseamnă Whisper, plus aliniere pe cuvinte cu wav2vec2,
   plus diarizare cu pyannote, dacă e activată [ASM, componentele standard ale proiectului].
   `transcription_context` (≤ 1.000 de caractere) se comportă ca promptul inițial al lui Whisper [ASM, încredere
   mare].
3. **Fără vorbire** [OBS]: video-ul merge la Gemini, un model care înțelege direct video.
   - Paginile de sport promit „scoring plays, defensive stops, big hits, emotional reactions”.
   - Pagina de highlight-uri promite „audio energy peaks, motion intensity, speaker reactions, key events”.

   Pe calea cu transcript, politica nu pomenește niciun semnal de sunet sau de mișcare [ASM, încredere medie].
4. **Alegerea și scorarea** [OBS]: LLM-urile primesc transcriptul și „clipping instructions”. Acestea sunt probabil
   parametrii task-ului: numărul și durata clipurilor [ASM]. Din același pas ies momentele, scorul cu explicația,
   titlurile, emoji-urile și traducerile. Prin OpenRouter, modelul poate fi schimbat fără ca utilizatorul să afle
   [ASM].
5. **Începutul clipului** [OBS, marketing]: „Klap's AI finds the strongest opening line in any segment and starts the
   clip there — not 8 seconds in. Your viewer hears the punchline first, then the setup.” Nu e clar dacă doar mută
   începutul pe replica cea mai tare sau chiar reordonează replicile. API-ul întoarce un singur proiect pe clip,
   fără intervale.
6. **Reîncadrarea** [OBS]:
   - „AI Reframe 2” detectează zonele de conținut (facecam, grafică de broadcast, terenul de joc, reluări) și
     alege un layout: split screen, screencast, gaming;
   - are detecție a vorbitorului activ;
   - contractori care îmbunătățesc reîncadrarea „by reviewing samples of video frames”, adică etichete umane.
7. **Finisarea și exportul** [OBS]: subtitrări, emoji, stil salvat, eliminarea pauzelor (opțional), watermark.
   Fișierele stau pe Google Cloud. Baza de date și autentificarea sunt pe Supabase.

**Restul infrastructurii** [OBS]:

- site-ul pe Netlify;
- analitice: PostHog, Google, Meta, TikTok;
- plăți: Stripe, Churnkey;
- suport și e-mail: Intercom, Resend, Postmark, OneSignal;
- firma: ZIGG SAS, Franța;
- există și o aplicație Klap în ChatGPT, care acționează prin API-ul lor.

## 4. Ce se poate deduce despre alegere [ASM]

- **Momentele fără cuvinte nu se văd.** Pe un stream cu vorbire, calea e cea de transcript. O reacție fără cuvinte,
  un moment de gameplay sau o poantă vizuală nu există pentru LLM. Gemini intră doar când **nu există** vorbire
  deloc.
- **Pe VOD-ul nostru de test, riscul e același ca la ClipForge în modul euristic.** Transcriptul amestecă naratorul
  trailerului cu streamerul (testul, §4), iar un LLM care citește doar textul nu le poate deosebi fără marcaje de
  vorbitor. Diarizarea din WhisperX le-ar despărți în grupuri anonime, dar nu știe care e streamerul (RS doc, §2).
- **Cunoștințele din SL se aplică direct scorului lor:** sensibilitatea la ordinea din listă, compresia,
  reproductibil ≠ valid. Critica lor la adresa OpusClip („decorative”) li se aplică la fel de bine.

## 5. Stack-ul, pe scurt

| funcție | furnizor | sursă |
|---|---|---|
| transcriere | WhisperX (open-source) pe RunPod | politica de confidențialitate |
| urmărirea oamenilor / reîncadrare | modele open-source pe RunPod; etichete umane pe cadre | politica de confidențialitate |
| alegere, scor, titluri, emoji, traduceri | LLM-uri prin OpenRouter + OpenAI | politica de confidențialitate |
| video fără vorbire | Google Gemini | politica de confidențialitate |
| stocare video, servere de procesare, loguri | Google Cloud (SUA) | politica de confidențialitate + DPA |
| bază de date, autentificare | Supabase | politica de confidențialitate + DPA |

## 6. Ce înseamnă pentru ClipForge

| la Klap | la ClipForge | ce merită luat |
|---|---|---|
| WhisperX: aliniere pe cuvinte + diarizare | faster-whisper, fără diarizare [CF] | WhisperX e drumul cel mai scurt spre marcajele de vorbitor din RS2–RS3. Tot trebuie aflat care grup e streamerul, prin auto-înrolare |
| `transcription_context` (nume, jargon) | propunerea CA5 (`hotwords`) | CA5 e confirmat de un concurent: un câmp de context pe proiect, trecut la transcriere |
| Gemini pentru video fără vorbire | nimic pentru porțiunile fără vorbire | un fallback vizual pentru gameplay sau reacții fără cuvinte; costul se măsoară înainte (SL) |
| scor LLM 0–1 cu explicație | verdict de judge + euristică + motive | avem deja motive; diferența la noi e lucrul pe bias-ul de poziție și pe fiabilitate (SL1–SL4) |
| numărul și durata clipurilor ca parametri | profil + banda platformei [CF] | — |
| niciun timp din sursă în API | start/end păstrate [CF] | — |
| 0,44 $ + 0,32 $ pe short + 0,48 $ pe export | local | referință de cost: un VOD cu 10 clipuri exportate costă ~8,4 $ prin API-ul lor |

## 7. Cum se poate testa pe VOD-ul nostru

- Contul gratuit dă un video. Aplicația web acceptă linkuri Twitch de până la 4 ore, iar VOD-ul de test are 64 de
  minute.
- Lista clipurilor (titlul, scorul, intervalul sau prima frază) se dă ca atare lui
  [`opus_blackbox.py`](clipper-cloud-test-2026-09-29/opus_blackbox.py).
- **Ce așteptăm, după arhitectura declarată** [ASM]: porțiuni vorbite și coerente, deci aproape de board-ul
  ClipForge în modul euristic. Fără legătură cu vârfurile din chat, pe care Klap nu le citește. Dacă rezultatul arată
  altfel, înseamnă că arhitectura declarată nu e toată povestea.

## Surse

- Politica de confidențialitate (arhitectura și furnizorii) — https://klap.app/privacy-policy
- DPA (sub-procesatori) — https://klap.app/data-protection-agreement ; contractul-cadru pentru API —
  https://klap.app/master-service-agreement
- Documentația API — https://docs.klap.app/ ; https://docs.klap.app/endpoints/tasks ;
  https://docs.klap.app/endpoints/projects ; https://docs.klap.app/endpoints/exports ;
  https://docs.klap.app/object-formats ; https://docs.klap.app/pricing ; https://docs.klap.app/styling ;
  https://docs.klap.app/usecases/generate-shorts ; https://docs.klap.app/usecases/managed-users
- Pagini de produs — https://klap.app/ ; https://klap.app/api ; https://klap.app/pricing ;
  https://klap.app/tools/viral-clip-generator ; https://klap.app/tools/highlight-video-maker ;
  https://klap.app/tools/sports-highlight-video-maker ; https://klap.app/alternatives/opus-clip
- Sitemap — https://klap.app/sitemap.xml
