# StreamLadder ClipGPT — reverse engineering din surse publice (29 septembrie 2026)

Al patrulea clipper desfăcut, după [OpusClip](opusclip-reverse-engineering-2026-09-29.md),
[Eklipse](eklipse-reverse-engineering-2026-09-29.md) și [Klap](klap-reverse-engineering-2026-09-29.md). E cel mai
apropiat de ce face ClipForge: VOD-uri de stream transformate în clipuri verticale, cu facecam și gameplay.
StreamLadder B.V. e din Groningen (Olanda), fondat în 2021. Declară peste 1 milion de streameri, 11 milioane de clipuri
și 100 de milioane de vizualizări.

Sursele:

- **125 de pagini de pe `streamladder.com`,** alese din cele 864 din sitemap, din care 632 sunt pagini de emote-uri:
  - ClipGPT (13), comparații cu concurenții (23), politici (4), joburi (9), editorul și celelalte unelte;
  - 23 de articole de blog despre clipping, chat și AI, plus o pagină de emote (KEKW);
- **pagina publică de planuri a aplicației** (`app.streamladder.com/upgrade`), care nu cere cont;
- **Wayback Machine:** versiunile din ianuarie 2025 ale paginilor ClipGPT, ca să se vadă cum s-au schimbat promisiunile;
- **înregistrările DNS publice** (TXT, MX, CNAME) și antetele de răspuns ale paginilor publice;
- **surse terțe:** un articol FC Groningen (2022), pagina de comparație a Eklipse, blogul Twitch și fragmente din
  căutare de pe Reddit. Reddit blochează citirea directă, deci am doar fragmentele.

**Ce n-am făcut:** n-am creat cont și n-am apelat API-ul lor. N-am citit nici codul aplicației: termenii interzic să
„decompile, or reverse engineer any materials and software contained on this website”.

| etichetă | înseamnă |
|---|---|
| **[OBS]** | spus de StreamLadder într-o sursă publică, citat din ea |
| **[DNS]** | văzut în înregistrările DNS publice sau în antetele HTTP |
| **[TERȚ]** | spus de altcineva: un concurent, un utilizator, presa |
| **[ASM]** | dedus de noi; încrederea e scrisă lângă |
| **[CF]** | starea ClipForge, verificată în cod pe 29.09 |

---

## 1. Pe scurt

1. **Aceeași problemă ca ClipForge, rezolvată ca produs.**
   - Intrare: VOD Twitch sau Kick prin link, YouTube doar prin fișier încărcat.
   - Ieșire: clipuri verticale gata editate, 1080×1920 la 60 fps, cu facecam-ul urmărit cadru cu cadru, inclusiv la
     VTuberi.
2. **Politica de confidențialitate nu numește niciun furnizor de AI.** Numește doar Paddle pentru plăți, login-ul prin
   Google, Twitch și TikTok, și YouTube API. Spre deosebire de Klap, arhitectura trebuie reconstruită din bucăți.
3. **Scorul declarat are patru componente** [OBS]: „audio energy, visual action, reaction intensity, and how cleanly the
   moment stands on its own outside the context of the full stream”. Sunt exact patru din sub-scorurile ClipForge:
   `audio_energy`, `visual_energy`, `reaction` și `context_completeness` [CF].
4. **Chatul intră probabil în alegere** [ASM, încredere medie]:
   - paginile vorbesc de „funny interactions with chat” și „chat-driven chaos”;
   - ClipGPT „works especially well with ... any stream with active chat”;
   - un articol dă ca exemplu de motiv pentru un clip „High chat excitement”.

   Cum anume folosesc chatul (viteza mesajelor, emote-uri) nu scrie nicăieri.
5. **Alegerea pornește de la semnale, nu de la transcript** [ASM, încredere medie]. Pagina căutării în transcript spune
   că ea prinde ce AI-ul „might rank lower”: glume interne, replici anume, „quiet reactions”. Tot acolo scrie că „The AI
   scores based on broad engagement signals”. Momentele liniștite și verbale pierd, invers decât la Klap.
6. **Viteza vine din procesarea pe bucăți** [OBS, plus ASM]:
   - analiza începe cât încă se descarcă VOD-ul;
   - momentele apar pe rând, pe măsură ce sunt găsite;
   - 2 ore se analizează în ~5 minute;
   - VOD-urile au până la 10 ore, iar cele mai lungi se procesează pe segmente.
7. **Infrastructura** [DNS]:
   - domeniul e verificat la OpenAI (`openai-domain-verification`);
   - API-ul rulează pe Fly.io;
   - fișierele trec prin BunnyCDN, totul e în spatele Cloudflare, iar e-mailurile pleacă prin Amazon SES.

   Numele „ClipGPT” și textele generate (titluri, hashtag-uri, 3–5 hook-uri) sugerează un LLM OpenAI pentru texte
   [ASM, încredere medie].
8. **Produsul a crescut repede** [OBS, Wayback]:
   - octombrie 2024: „up to 10 key moments”;
   - ianuarie 2025: clipuri orizontale, încadrarea verticală se făcea de mână în editor;
   - 2026: „Up to 30 clips from each stream”, gata încadrate și subtitrate, nelimitat la 27 $ pe lună.
9. **Pentru ClipForge** (§7): chatul, cu un test care chiar poate răspunde (§8); „clip that” spus de streamer, căutat
   în transcript; rezultate progresive; căutarea în transcript ca plasă de siguranță; intensitatea emote-urilor.

## 2. Cât valorează sursele

Paginile de produs sunt scrise pentru SEO și se contrazic între ele și în timp. Politica de confidențialitate e un
șablon generic, fără nimic tehnic. Cele mai precise surse tehnice sunt FAQ-urile paginilor ClipGPT, tabelul de planuri
și DNS-ul.

| subiect | o sursă | altă sursă |
|---|---|---|
| clipuri pe stream | „up to 10 key moments” (blog, oct. 2024); „up to 10 clips” (ian. 2025) | „Up to 30 clips from each stream” (planuri, 2026); Eklipse: „around 10 vertical clips per stream” [TERȚ] |
| lungimea clipului | „They'll come in different lengths” (ian. 2025) | „the best 10-30 second moments” (comparația cu Crossclip) |
| limbi | subtitrările „recognize any language automatically”; „clip that” „understands any language” (blog, iul. 2026) | căutarea în transcript: „Currently English”; subtitrările: „highly accurate for English” |
| fișier încărcat | „Import from Upload (max 10 GB)” | „Upload file size ... 2GB” (același tabel) |
| lungimea VOD-ului | „Up to 10 hours on the ClipGPT plan” | „any stream length”; „Longer streams are processed in segments” |
| cât de bun e scorul | „Our AI doesn't guess—it provides real metrics” (ian. 2025); „Virality scores you can trust” | FAQ: „How accurate is the virality prediction? The AI identifies moments ... based on content patterns. You always review the scores.” Nicio cifră |
| utilizatori | „500k” (ian. 2025) | „1M+” (2026) |
| abonament | abonament separat, „Casual, Consistent, and Unlimited”, după numărul de stream-uri pe săptămână; „up to 3 streams a day” (oct. 2024) | „Unlimited use of ClipGPT” în planul Creator (2026) |

## 3. Pipeline-ul, reconstruit

1. **Importul** [OBS]:
   - link public de VOD Twitch sau Kick; contul Twitch se poate conecta ca să alegi dintre stream-urile recente;
   - YouTube doar ca fișier: „Download your YouTube stream and upload the file to ClipGPT”;
   - merg și clipurile Twitch individuale. Editorul importă și de pe Trovo.
2. **Descărcarea și analiza merg în paralel** [OBS]:
   - „While it downloads, the AI clipping is already analyzing”;
   - „You can start reviewing moments as they appear”;
   - 2 ore se analizează în ~5 minute.

   VOD-urile Twitch sunt HLS: segmentele vin în ordine, deci fiecare bucată se poate analiza cum ajunge [ASM, încredere
   mare]. Transcriptul e complet, altfel căutarea n-ar merge. De aici, 24× timp real înseamnă GPU-uri și bucăți
   procesate în paralel [ASM, încredere medie].
3. **Transcrierea** [OBS]: „ClipGPT transcribes your entire VOD”. Căutarea e doar în engleză, iar subtitrările sunt
   sincronizate pe cuvânt. Motorul nu e numit nicăieri.
4. **Detecția și scorul** [OBS]:
   - componentele scorului din §1.3;
   - tipuri de momente: interacțiuni amuzante cu chatul, momente intense sau înfricoșătoare, reacții haotice,
     „clutch plays, hilarious deaths”;
   - „The AI is trained on stream content” (comparația cu Vizard);
   - „a broader AI model that works across all game types and also finds the best moments from non-gaming content like
     Just Chatting and IRL” (comparația cu Eklipse, care are detecție pe evenimentele fiecărui joc);
   - momentele au un tip: „You can filter results by type after analysis”.
5. **„Clip that” spus de streamer** [OBS]: dacă streamerul spune „clip that” sau „clip this” în timpul live-ului,
   momentele apar editate lângă cele găsite automat, „when you process the VOD”. Deci comanda e căutată în sunetul
   VOD-ului, nu ascultată live. Blogul spune că merge în orice limbă.
6. **Ce primești pentru fiecare moment** [OBS]:
   - timpii și un motiv scurt (exemplul din blog: „High chat excitement”);
   - scorul 0–100, titlul, hashtag-urile și 3–5 variante de hook.
7. **Randarea** [OBS]:
   - facecam-ul sau avatarul e găsit „regardless of size, position, or overlay style” și urmărit cadru cu cadru;
   - layout-uri: facecam sus și gameplay jos, alăturate, sau doar gameplay. Fără facecam, gameplay-ul se centrează;
   - 1080×1920 la 60 fps, 4K la 60 fps pe planul Pro;
   - subtitrări animate cuvânt cu cuvânt;
   - editorul are și eliminarea pauzelor, titlu-hook, cenzurarea cuvintelor, text-to-speech și emote-uri
     Twitch/BTTV/FFZ/7TV.
8. **După** [OBS]:
   - programarea pe TikTok, YouTube, Instagram și X;
   - „Auto YouTube Video”: o compilație orizontală, cu clipurile ordonate „for engagement”;
   - „Auto Pilot” (găsește, editează și postează după stream), marcat „Soon” pe planul Pro.

## 4. Ce se poate deduce despre alegere [ASM]

- **E mai aproape de Eklipse decât de Klap.** Semnalele de energie și reacție vin primele. Transcriptul servește la
  subtitrări, căutare și texte. Dovada cea mai clară e felul în care își prezintă căutarea în transcript: ca soluție pentru
  ce scorul ratează.
- **Chatul contează probabil, dar nu știm cum.** Un motiv ca „High chat excitement” se calculează din viteza
  mesajelor [ASM, încredere medie]. Emote-urile pot intra și ele: au 632 de pagini de emote-uri, iar cea despre KEKW
  explică sensul și o scară a râsului, „LUL” < „KEKW” < „OMEGALUL”. Nimic public nu leagă însă aceste pagini de
  ClipGPT.
- **„Trained on stream content”** sugerează un model antrenat pe clipuri de stream [ASM, încredere medie]. Sursa ar fi
  cele 11 milioane de clipuri făcute la ei și, prin publicarea directă pe TikTok, YouTube și Instagram, performanța lor.
  Termenii le dau o licență largă pe conținutul utilizatorilor, dar nu pomenesc antrenarea.
- **Scorul nu are nicio validare publică.** Cele patru componente descriu ce măsoară, nu cât de bine prezice.
- **Pe VOD-ul nostru de test** e de așteptat să aleagă momentele zgomotoase și rapide. Riscul e același ca la
  ClipForge: trailerul însuși are energie audio și acțiune vizuală mari. Dacă „reaction intensity” nu cântărește mult,
  ClipGPT poate alege tot trailerul, adică greșeala clipului nostru #1.
- **Un utilizator** [TERȚ, Reddit, doar fragmentul din căutare]: „hit or miss finding funny moments some days, and it
  doesn't pay attention to stream markers”. Marcajele de stream Twitch nu par folosite.

## 5. Stack-ul, pe scurt

| funcție | furnizor | sursă |
|---|---|---|
| DNS, proxy, imagini | Cloudflare (`cf-ray`, `/cdn-cgi/image`, rapoartele DMARC) | [DNS] |
| API | Fly.io: `api.streamladder.com` → `streamladder-api.fly.dev` | [DNS] |
| fișiere | BunnyCDN: `cdn.streamladder.com` → `streamladder-blob.b-cdn.net` | [DNS] |
| site | Nuxt acum; Webflow cel puțin până în ianuarie 2025 (a rămas un TXT `proxy-ssl.webflow.com`) | `robots.txt`, Wayback, [DNS] |
| e-mail | Google Workspace; Amazon SES pentru e-mailurile automate | MX, SPF |
| plăți | Paddle | politica de confidențialitate |
| analitice de produs | PostHog, Google Analytics | anunțul pentru stagiarul de analiză |
| OpenAI | domeniu verificat; la ce folosește nu e declarat | [DNS] |
| publicare | verificări de domeniu TikTok for Developers și Meta | [DNS] |
| modelele de detecție și transcriere, GPU-urile | nedeclarate | — |

**Firma** [OBS, TERȚ]: fondatorul e Lolke Bouma, după un articol FC Groningen din 2022. Echipa e mică și lucrează 32
de ore pe săptămână. Anunțurile deschise sunt toate pentru stagiari și studenți (design, marketing, HR, analiză),
niciunul pentru inginerie sau ML.
Au și o firmă-soră, ClipGOAT, pentru video-uri YouTube lungi („up to 20 short, impactful clips”).

## 6. Contextul: Twitch Auto Clips

StreamLadder se poziționează față de funcția Twitch, care e încă în test.

- **Octombrie 2025** [TERȚ, blogul Twitch]: Auto Clips folosește „AI and a variety of signals from the broadcast — such
  as positive excitement from the stream or funny banter”. Promite și clipuri care „splice together portions of your
  stream to eliminate dead space”.
- **Mai 2026** [TERȚ, blogul Twitch, TwitchCon Rotterdam]: „Auto Clips automatically generates captioned clips from the
  best moments from your stream, by taking into account chat activity, vocal inflection, and on-screen events.”
  Fără Auto Clips, doar 50% dintre streameri au un clip după un stream; cu Auto Clips, până la 85%.
- **Platforma numește aceleași trei familii de semnale ca Eklipse:** chatul, vocea și evenimentele de pe ecran.
  ClipForge e cel mai slab la chat: 0 din 12 vârfuri de chat în board (testul din cloud).

## 7. Ce înseamnă pentru ClipForge

| la StreamLadder | la ClipForge | ce merită luat |
|---|---|---|
| scor din energia audio, acțiunea vizuală, intensitatea reacției, cât de bine stă singur momentul | aceleași patru, între cele 16 sub-scoruri [CF] | nimic de copiat; confirmă dimensiunile. Diferența lor, dacă există, vine din chat și din datele de antrenare |
| chatul, probabil; motive ca „High chat excitement” | mesajele din chat nu se citesc; „chat” în cod e caseta de pe ecran [CF] | RS4–RS6. Testul din §8 arată dacă merită |
| „clip that” / „clip this” spus de streamer, în orice limbă, găsit în VOD | lipsește [CF] | ieftin: căutarea frazelor în transcript, într-o listă pe limbi, ca propunere prin `candidate_proposals.py` |
| analiza începe în timpul descărcării; momentele apar pe rând; 2 ore în ~5 minute | pași în serie: descărcare, proxy, transcriere (27 min pentru 64 min pe 4 nuclee, fără GPU), analiză, scor [CF, testul din cloud] | un board progresiv, cu semnalele ieftine scorate pe bucăți. De făcut abia după ce alegerea e bună |
| căutarea în transcript, pentru ce scorul ratează | Clipper-ul nu are căutare în transcript; pagina `/transcript` e pentru curățarea textului [CF] | o căutare în transcriptul proiectului, care face din rezultat un candidat manual |
| scara râsului pe emote-uri (LUL < KEKW < OMEGALUL) | `analyze_chat.py` are categorii de emote-uri fără trepte [scriptul de cercetare] | RS5: ponderea emote-urilor de râs pe trepte. Dicționarul se ia din API-urile BTTV/FFZ/7TV, nu din paginile lor |
| clipuri de 10–30 s | benzi de la 15–45 s (TikTok) la 15–90 s (Reels), minimum 15 s [CF] | nimic; minimul de 15 s e măsurat |
| până la 30 de clipuri pe stream | 10 pe board, plus alternative [CF, testul din cloud] | — |
| VOD-uri de până la 10 ore, cele mai lungi pe segmente | `atoms.py` își socotește costul pe un stream de 12 ore [CF] | — |
| YouTube doar prin fișier încărcat | yt-dlp, cu cookie-uri și runtime JS (`CLIPFORGE_YTDLP_*`) [CF] | — |
| 27 $ pe lună, fără limită de stream-uri (~22,5 $ la plata anuală, cu două luni gratuite) | local, fără cost pe clip | referință de cost: la API-ul Klap, un VOD cu 10 clipuri exportate costă ~8,4 $ |

## 8. Cum se poate testa pe VOD-ul nostru

- **Contul și rularea le face utilizatorul.** Proba e gratuită 7 zile (FAQ-ul lor). Se lipește linkul VOD-ului Twitch
  2858049515.
- **Pentru fiecare clip se notează:**
  - începutul și sfârșitul, sau prima frază;
  - scorul, motivul, tipul (din filtru) și titlul.

  Se notează și cât durează analiza și dacă momentele apar pe rând.
- **Lista se dă lui** [`opus_blackbox.py`](clipper-cloud-test-2026-09-29/opus_blackbox.py), în același format ca la
  OpusClip.
- **Întrebarea principală, „citește chatul?”, nu se poate lămuri pe VOD-ul ăsta.** Reluarea chatului e plafonată la
  505–640 de mesaje pe minut ([RS §3.3](clipper-reaction-signals-2026-09-29.md)), deci viteza chatului aproape nu
  variază. Un semnal bazat pe viteză ar fi aproape plat aici. Rămân două căi:
  1. **motivele:** dacă apare „chat” în ele, e dovadă directă;
  2. **un al doilea VOD, de pe un canal mic,** unde viteza chatului variază. Momentele alese se compară cu vârfurile de
     chat, la decalajul măsurat cu `chat_lag.py`.
- **Ce așteptăm** [ASM]:
  - momente zgomotoase și rapide, poate chiar trailerul (§4);
  - mai puține cuvinte pe secundă decât la Klap;
  - un raport de râs peste ferestrele aleatoare, dacă folosește chatul.

## Surse

- ClipGPT — https://www.streamladder.com/clipgpt ; https://www.streamladder.com/clipgpt/ai-clipping ;
  https://www.streamladder.com/clipgpt/virality-score ; https://www.streamladder.com/clipgpt/transcript-search ;
  https://www.streamladder.com/clipgpt/facecam-detection ; https://www.streamladder.com/clipgpt/auto-crop-vertical ;
  https://www.streamladder.com/clipgpt/auto-captions-hashtags-hooks ;
  https://www.streamladder.com/clipgpt/import-from-twitch ; https://www.streamladder.com/clipgpt/import-from-kick ;
  https://www.streamladder.com/clipgpt/auto-clip/youtube ; https://www.streamladder.com/clipgpt/auto-youtube-video
- Comparații — https://www.streamladder.com/compare/eklipse ; https://www.streamladder.com/compare/vizard ;
  https://www.streamladder.com/compare/crossclip ; https://www.streamladder.com/compare/opus-clip
- Blog — https://www.streamladder.com/blog/introducing-clipgpt-quickly-create-clips-from-your-streams (18.10.2024) ;
  https://www.streamladder.com/blog/twitch-auto-clips-your-guide-to-automatically-capturing-stream-highlights ;
  https://www.streamladder.com/blog/twitch-clip-that-voice-command ;
  https://www.streamladder.com/blog/new-ai-tool-creates-viral-short-form-content-from-your-youtube-videos-try-clipgoat
- Emote-uri — https://www.streamladder.com/emotes/kekw ; editor — https://www.streamladder.com/clip-editor/twitch-emotes
- Planuri — https://app.streamladder.com/upgrade ; despre firmă — https://www.streamladder.com/about ;
  joburi — https://www.streamladder.com/jobs
- Politici — https://www.streamladder.com/policies/privacy ; https://www.streamladder.com/policies/terms
- Wayback (ian. 2025) — https://web.archive.org/web/20250118102049/https://streamladder.com/clipgpt-features/ai-clipping ;
  https://web.archive.org/web/20250118074744/https://streamladder.com/clipgpt-features/ai-virality-score
- DNS — înregistrările TXT, MX și CNAME pentru `streamladder.com`, citite prin https://dns.google/resolve
- Terți — https://www.fcgroningen.nl/nieuws/start-up-gro-streamladder-de-onmisbare-tool-voor-alle-streamers/ ;
  https://eklipse.gg/compare/eklipse-vs-streamladder/ ;
  https://www.reddit.com/r/SmallStreamers/comments/1t993ok/streamladder_is_it_worth_it/ (fragmentul din căutare) ;
  https://blog.twitch.tv/en/2025/10/17/ten-years-of-twitchcon-here-s-what-we-announced-in-san-diego/ ;
  https://blog.twitch.tv/en/2026/05/30/everything-we-announced-at-twitchcon-rotterdam-2026/
