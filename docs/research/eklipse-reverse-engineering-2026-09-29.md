# Eklipse — reverse engineering din surse publice (29 septembrie 2026)

Eklipse e concurentul cel mai apropiat de AI Stream Clipper: face clipuri automat din VOD-urile de pe Twitch, Kick și
YouTube, pentru streameri. E al doilea desfăcut, după
[OpusClip](opusclip-reverse-engineering-2026-09-29.md).

Sursele, doar publice:

- **centrul de ajutor:** 238 de articole în engleză, dintre care am citit cele ~40 tehnice;
- **site-ul:** ~190 de pagini (funcții, pagini pe jocuri, comparații), plus sitemap-urile, politica de
  confidențialitate și paginile din magazinele de aplicații.

Nimic din aplicația lor, nicio autentificare, niciun endpoint privat.

| etichetă | înseamnă |
|---|---|
| **[OBS]** | spus de Eklipse într-o sursă publică, citat din ea |
| **[ASM]** | dedus de noi; încrederea e scrisă lângă |
| **[MĂS]** | verificat de noi |
| **[CF]** | starea ClipForge, verificată în cod pe 29.09 |

---

## 1. Pe scurt

1. **E făcut pentru streamerul care își clipuiește propriul canal** [OBS]. Canalul se conectează prin OAuth, iar
   Eklipse detectează când intră live și procesează stream-ul după ce se termină sau în timp real. Clipurile ajung
   editate vertical și programate pe TikTok, Shorts și Reels. ClipForge clipuiește și canalele altora, ceea ce e o
   poziție diferită.
2. **Motorul „Gameplay Intelligence” (1 iunie 2026) are trei straturi** [OBS]:
   - detecția momentelor: evenimente din joc, compoziția audio, densitatea acțiunii pe ecran, continuitatea;
   - granițe citite din scenă: începe la acțiunea care pregătește momentul, se termină când se rezolvă;
   - rutare pe gen de joc, după categoria trimisă de platformă.

   Motorul vechi tăia o fereastră fixă în jurul unui singur declanșator, iar scorul lui venea din vârfurile de sunet.
3. **Semnalele numite explicit** [OBS]:
   - **HUD-ul jocului:** kill feed, eliminări, runde, ecrane de victorie. Cer 1080p și poziția implicită a
     elementelor din interfață „ca AI-ul să citească textul din joc”.
   - **Sunetul, împărțit în trei:** vocea streamerului, sunetul jocului, fundalul.
   - **Densitatea acțiunii** vizuale și **continuitatea**: serii de kill-uri, comeback-uri.
   - **Chatul:** „emote spikes, message velocity”, de exemplu un val de „POG” sau „KEKW”.
   - **Comenzi:** una vocală („clip it / this / that”) și una în chat (`!eklipse`).
4. **Lungimea clipului depinde de tipul momentului** [OBS]: 5–8 s pentru un singur kill, 20–30 s pentru un clutch,
   15–35 s pentru un moment de reacție.
5. **Limite scrise negru pe alb** [OBS]:
   - un singur joc pe VOD: primul joc detectat blochează modelul;
   - dependență de categoria platformei;
   - maximum 6 ore pe Twitch și Kick (12 pe YouTube), iar VOD-ul trebuie să fie public;
   - un VOD mutat pentru DMCA pierde semnalele audio;
   - partea audio e reglată doar pe voce în engleză.
6. **Scorul „viral” amestecă momentul cu calitatea fișierului** [OBS]: relevanța momentului, semnalele de
   engagement (chat, intensitatea vocii, mișcarea) și rezoluția/bitrate-ul sursei. E recalculat după editare.
7. **Pentru ClipForge** (§7), trei idei de luat:
   - **rutarea pe segmente:** ClipForge alege deja profilul pe segment [CF]. Capitolele Twitch (schimbările de joc),
     pe care yt-dlp le dă deja [MĂS], ar fi o a doua sursă pentru tipul segmentului. Tocmai asta le lipsește lor
     („un joc pe VOD”);
   - **conjuncția de semnale:** o voce care urcă în timp ce jocul e zgomotos și acțiunea e densă valorează mai mult
     decât aceeași voce într-un meniu;
   - **lungimea după tipul momentului**, nu doar după platformă.

## 2. Cât valorează sursele

Centrul de ajutor e specific, datat (iunie–iulie 2026) și consecvent. Paginile de marketing se contrazic între ele.
Unele au artefacte de generare automată: „[cite: 4]” în text și un paragraf în indoneziană pe pagina în engleză.
Le folosim doar unde confirmă centrul de ajutor.

| afirmație | variante găsite pe site |
|---|---|
| jocuri suportate | „100+” · „1,000+” · „3,000+” |
| utilizatori | „50,000+ streamers” · „900K active streamers” · „1M+” · „1.5M+” · „3M+ creators” |
| timp până la clipuri | „60 seconds” · „20–60 minutes after a stream ends” · „within a few hours” (mai repede cu Premium) |
| limite | „no restrictions on stream length” (marketing) vs 6 h Twitch/Kick, 12 h YouTube (centrul de ajutor) |
| acuratețe | „over 95% accuracy” — fără metrică și fără set de test; nu o folosim |

## 3. Arhitectura, pas cu pas [OBS, cu deducțiile marcate]

1. **Conectarea:** OAuth pe Twitch, YouTube, Kick, Facebook sau Rumble. Drepturile cerute sunt citirea VOD-urilor și
   publicarea. „Pull Manually” aduce VOD-urile din ultimele 14 zile, între 15 minute și 5 ore.
2. **Detecția de live:** sistemul „ascultă” starea canalului. Probabil prin EventSub/webhooks sau polling [ASM,
   încredere medie].
3. **Două moduri de procesare:**
   - **live** („Live Processing”): monitorizare în timp real, clipurile gata „la scurt timp după” final. Comanda
     vocală funcționează live fără niciun plugin, deci ascultă fluxul public al stream-ului pe serverele lor [ASM,
     încredere mare];
   - **după VOD:** la ~30 de minute după stream, cât durează până e gata VOD-ul pe platformă.
4. **Ingest propriu:** în Premium poți face stream direct către Eklipse („Private Stream”, 1440p) fără să-l publici,
   deci au servere proprii de ingest RTMP și procesează în cloud [OBS].
5. **Identificarea jocului:** din metadatele de categorie ale platformei. Primul joc „blochează” modelul pentru tot
   VOD-ul. Detecția multi-joc e „în lucru”. Cu categoria greșită se aplică modelul greșit, iar „Resubmit” cu jocul
   corect reprocesează totul de la zero.
6. **Detecția momentelor**, rutată pe gen:

   | gen | ce cântărește |
   |---|---|
   | FPS | densitatea kill-urilor, recul/spray, citirea kill feed-ului |
   | battle royale | zone de drop, închiderea zonei, lupte cu terți, victorie |
   | MOBA | teamfight-uri, obiective, presiune din junglă, momentul ultimate-urilor |
   | tactical | economia rundei, clutch, plant/defuse |
   | strategie / non-acțiune | arcul narativ, escaladarea vocii și a chatului |

   Un joc nesuportat trece pe „general action detection”: fără declanșatori din interfață, doar sunet și mișcare.
7. **Granițele și lungimea:** „scene-aware context” găsește începutul (acțiunea care pregătește) și sfârșitul
   (rezolvarea) și taie timpul mort de la capete. Lungimile tipice pe tipul momentului:

   | moment | lungime |
   |---|---|
   | one-tap / no-scope | 5–8 s |
   | kill obișnuit | 10–15 s |
   | multi-kill | 15–25 s |
   | clutch 1v3–1v4 | 20–30 s |
   | rundă decisivă, cu pregătire | 25–40 s |
   | narațiune / reacție | 15–35 s |

8. **Clasarea:** o listă ordonată după scor. „Fiecare stream are 8–14 momente de postat”, iar sub 10 clipuri e
   tratat ca problemă („Inaccurate AI or Fewer Than 10 Clips” → Resubmit). Titlul clipului e numele momentului:
   „Double Kill”, „Victory Royale”, „Chat reaction”, „Hot take”.
9. **Content Agent:** după fiecare stream pune automat **primele 3** clipuri în coadă, cu descrieri scrise pe clip.
   Publicarea cere o singură aprobare.
10. **AI Edit:** tăiere, crop vertical, facecam + gameplay pe ecran împărțit, subtitrări, efecte și meme-uri. Durează
    5–10 minute pe clip.

## 4. Semnalele, unul câte unul

### 4.1 Starea jocului, citită din interfață [OBS → ASM]

- **Ce spun ei:** AI-ul „citește interfața jocului” ca un spectator: kill feed, eliminări, doborâri, capturi de
  obiective, runde câștigate, schimbări de scor, tranziții de meci.
- **Ce recomandă streamerilor:** să nu acopere kill feed-ul, minimapa sau notificările cu facecam-ul, să păstreze
  interfața în poziția implicită și să streameze la 1080p „ca AI-ul să citească textul din joc”.
- **La jocurile nesuportate,** „sare peste declanșatorii vizuali” (text ca „Headshot” sau „Victory Royale”).
- **Deducția** [ASM, încredere mare]: pentru fiecare joc, zone fixe ale HUD-ului, citite cu OCR sau clasificatoare
  pe regiune. De aici vin dependența de poziția implicită și de rezoluție. „Modele antrenate pe date din peste 1.000
  de jocuri” înseamnă o configurație pe joc, nu un model general de video.

### 4.2 Compoziția audio [OBS → ASM]

- **Ce spun ei:** sunetul e separat în vocea streamerului, sunetul jocului și fundal (muzică, alerte de chat,
  ambient). Un vârf al vocii cât timp jocul e zgomotos și acțiunea e densă cântărește mai mult decât același vârf
  într-un meniu. Asta scade alarmele false pe stream-urile de reacție.
- **Limite spuse de ei:**
  - stratul audio e reglat pe voce în engleză;
  - muzica tare la nivelul vocii strică separarea;
  - un VOD mutat pentru DMCA pierde tot semnalul audio, iar pe un joc nesuportat asta înseamnă zero clipuri;
  - recomandă „VOD Track” în OBS, adică muzica exclusă din VOD.
- **Deducția** [ASM, încredere medie]: un model de separare pe trei piste, plus detectoare de râs, țipăt și energie
  a vocii. Cerința „mic-ul să se audă peste joc” arată că lucrează pe mixul final, ca noi.

### 4.3 Densitatea acțiunii și continuitatea [OBS]

- Cât se întâmplă vizual în secundele din jurul candidatului.
- Seriile, comeback-urile și angajamentele susținute devin un singur clip mai lung, nu fragmente.

### 4.4 Chatul [OBS]

- **Pe prima pagină:** „chat sentiment (emote spikes, message velocity)”, combinat cu jocul și sunetul într-un scor
  de „viral potential”.
- **În centrul de ajutor:** „monitors chat velocity (like a sudden wave of POG or KEKW emotes)”.
- La strategie și Just Chatting, chatul primește pondere mai mare, pentru că n-ai kill feed.
- Comanda `!eklipse` din chat marchează un moment în timp real, deci citesc chatul live [ASM, încredere mare].
  ClipForge are doar reluarea, plafonată pe canalele mari (testul, §5).

### 4.5 Comanda vocală [OBS]

- Trei fraze („clip it / this / that”), doar în engleză, cu Premium, fără plugin.
- Ascultă sunetul stream-ului, nu textul din chat. Recunoaște un tipar de frază, nu cuvântul „clip” singur, ca „this
  clip is crazy” să nu declanșeze nimic.
- Clipul are până la 180 s: 90 s înainte și 90 s după comandă.
- Momentul se construiește din secundele dinainte, deci o comandă spusă în avans „deschide fereastra pe nimic”.

### 4.6 Just Chatting și IRL [OBS]

- Intră în detecție de la actualizarea din iulie 2026: „laughter spikes, voice-energy jumps and chat reactions”.
- „Hot takes” sunt tăiate până la replica care contează. Momentele de chat includ donații, raid-uri și răspunsuri
  la chat.

### 4.7 Scorul „viral” [OBS]

- **Ce intră în el:**
  - relevanța momentului: un vârf recognoscibil;
  - semnalele de engagement: activitatea chatului, intensitatea vocii, mișcarea;
  - calitatea sursei: rezoluție și bitrate.
- Nu poate fi modificat manual și e recalculat după editarea clipului în Studio.
- Unealta lor gratuită „AI Clip Analyzer” notează 0–100 cinci factori: hook, lungime, format, subtitrare, payoff
  emoțional.

## 5. Limitele declarate

| limită | ce înseamnă |
|---|---|
| un joc pe VOD | stream-urile de variety trebuie tăiate manual sau repornite între jocuri |
| categoria platformei decide modelul | categorie uitată pe „Just Chatting” → modelul greșit → Resubmit |
| 6 h Twitch/Kick, 12 h YouTube; auto-import 15 min–5 h | recomandă repornirea stream-ului la 4–6 h |
| VOD public | archive doar pentru abonați, private sau șterse → nimic |
| DMCA / VOD mutat | fără audio, mult mai puține clipuri; pe un joc nesuportat, zero |
| engleză | comanda vocală și stratul audio sunt reglate pe engleză |
| credite | 3 VOD-uri gratuite; Premium: 20 pe lună, 120 pe 6 luni, 240 pe an |

## 6. Stack-ul probabil și firma [ASM, cu indiciile]

- **Firma:** „Main Spring Technology Pte Ltd” (Singapore), din politica de confidențialitate. Textul indonezian scăpat
  pe o pagină în engleză sugerează o echipă în Indonezia [încredere medie]. Aplicația e în App Store din 2023.
- **Cloud nenumit:** „third-party cloud-based service provider”, plus Google Analytics. Pentru datele YouTube
  declară o retenție de 30 de zile.
- **Procesarea:** în cloud („cloud nodes”), cu ingest RTMP propriu, monitorizare live a stream-urilor publice,
  detectoare pe joc (HUD/OCR), separare audio, un detector de fraze (comanda vocală) și un scor combinat.
- **Anunțuri de angajare tehnice publice:** n-am găsit, deci nu știm modelele concrete.

## 7. Ce înseamnă pentru ClipForge [ASM, cu CF acolo unde e verificat]

| la Eklipse | la ClipForge | ce merită luat |
|---|---|---|
| categoria platformei alege modelul, dar doar primul joc pe VOD | profilul se alege deja pe segment, după tipul de conținut detectat (`segment_types`, luat la mijlocul candidatului; `workers/clipper_scoring.py`) [CF] | **capitolele Twitch** ca a doua sursă, independentă, pentru tipul segmentului: yt-dlp le dă deja pentru VOD-ul de test (0–581 s „Just Chatting”, 581–3828 s „Grand Theft Auto VI”) [MĂS]. Pe segment facem deja ce lor le lipsește; le-ar lipsi doar verificarea |
| HUD citit pe joc (kill feed, victorie) | cunoaște panourile de UI, dar doar pentru încadrare (`dynamic_cameras.py`, `dynamic_window.py`) [CF]; niciun eveniment de joc | un semnal nou doar pentru VOD-urile de gaming. E scump: o configurație pe joc. De cântărit numai dacă sursele sunt mult gaming competitiv |
| vocea × jocul zgomotos × acțiunea densă | sub-scoruri separate, însumate cu ponderile profilului (`scoring.py`: `overall = Σ sub_score × weight`) [CF] | o trăsătură de **conjuncție**, de testat prin poarta SL6 |
| audio pe trei piste | nimic | se leagă de RS1–RS3 (vocea streamerului). Separarea lor e pe surse, nu pe identitatea vocii |
| chat live: viteză, emote-uri, `!eklipse` | doar reluarea (plafonată), deocamdată în scripturi de cercetare | CA7, RS4–RS6. Lecția lor: la non-gaming, chatul contează mai mult |
| lungimea după tipul momentului | banda de durată a platformei [CF] | lungimea țintă derivată din tipul momentului, înainte de banda platformei |
| primele 3 în coadă automat, o aprobare | board de 10 + review | — (produs, nu motor) |
| scorul include calitatea sursei, recalculat după editare | scorul descrie momentul | **nu** amestecăm calitatea fișierului în scor, altfel nu mai știm ce măsoară |
| „95% accuracy” fără metrică | — | nu comparăm cu cifre de marketing |

## 8. Cum se poate testa pe VOD-ul nostru

- Eklipse acceptă un link de VOD Twitch public („Create > AI Highlight → paste a public VOD link”). Contul gratuit
  are 3 credite.
- VOD-ul de test (`2858049515`, 64 min, public) intră în limitele lor.
- După documentație, primul capitol („Just Chatting”) ar bloca modelul pentru tot VOD-ul. Ar fi deci un test al
  scorării conversaționale, nu al celei de gaming.
- Lista lor de clipuri (numele momentului, scorul, intervalul) se dă ca atare lui
  [`opus_blackbox.py`](clipper-cloud-test-2026-09-29/opus_blackbox.py), care merge pentru orice clipper.
- Cu OpusClip deja rulat, rezultă trei răspunsuri pe același VOD: ClipForge, OpusClip, Eklipse. Pe lângă ele avem
  vârfurile din chat ca referință de la public.

## Surse

- Centrul de ajutor: https://eklipse.gg/help/ — în special
  https://eklipse.gg/help/how-gameplay-intelligence-picks-moments/ ;
  https://eklipse.gg/help/how-does-eklipse-ai-choose-highlights/ ;
  https://eklipse.gg/help/how-does-eklipse-automatically-create-clips/ ;
  https://eklipse.gg/help/why-did-the-ai-only-capture-clips-from-my-first-game/ ;
  https://eklipse.gg/help/how-to-fix-ai-detected-wrong-game/ ;
  https://eklipse.gg/help/what-to-do-if-you-play-an-unsupported-game/ ;
  https://eklipse.gg/help/does-a-muted-vod-prevent-ai-highlights/ ;
  https://eklipse.gg/help/are-there-any-stream-layout-best-practices-e-g-webcam-size-placement-overlays-to-improve-ai-detection-accuracy/ ;
  https://eklipse.gg/help/rank-clips-viral-score/ ; https://eklipse.gg/help/how-does-voice-command-work/ ;
  https://eklipse.gg/help/how-much-of-the-stream-is-clipped-per-command/ ;
  https://eklipse.gg/help/can-ai-process-past-streams-vods/ ;
  https://eklipse.gg/help/is-there-a-maximum-vod-length-the-ai-can-process/ ;
  https://eklipse.gg/help/my-vod-is-public-but-the-ai-highlight-process-failed-or-seems-stuck-what-are-the-common-causes/ ;
  https://eklipse.gg/help/how-do-i-import-my-streams-vods/ ;
  https://eklipse.gg/help/what-is-the-twitch-vod-trial-limit-and-how-to-get-more-credits/ ;
  https://eklipse.gg/help/how-to-do-a-private-stream-to-eklipse/ ;
  https://eklipse.gg/help/is-it-safe-to-connect-my-streaming-account/ ;
  https://eklipse.gg/help/how-do-i-give-feedback-on-a-clip-if-the-ai-got-it-wrong-does-this-help-improve-the-ai/
- Pagini de produs: https://eklipse.gg/ ; https://eklipse.gg/features/ai-highlights/ ;
  https://eklipse.gg/features/voice-command/ ; https://eklipse.gg/features/ultra-highlights/ ;
  https://eklipse.gg/features/content-agent/ ; https://eklipse.gg/use-case/just-chatting/ ;
  https://eklipse.gg/use-case/valorant-highlights/ ; https://eklipse.gg/use-case/dead-by-daylight-highlights/ ;
  https://eklipse.gg/tool/ai-clip-analyzer/ ; https://eklipse.gg/compare/eklipse-vs-opusclip/ ;
  https://eklipse.gg/about-us/
- Politica de confidențialitate (entitatea juridică) — https://eklipse.gg/privacy-policy/
- Sitemap-uri — https://eklipse.gg/sitemap_index.xml
