# Handoff — dynamic multi-cam edit

Paste the block in section 0 into a fresh Claude Code session (any account) to
continue this work. Everything after it is the reference material that block
points at.

---

## 0. The prompt

```
Continui o funcție din ClipForge: montajul dinamic multi-cam pentru AI Stream
Clipper. Lucrează exact în maniera din sesiunea anterioară: instalezi și
folosești skill-uri, folosești workflow-uri de agenți pentru fan-out, măsori
înainte să scrii cod, și spui explicit ce n-ai putut verifica.

SETUP
1. git clone https://github.com/brinza69/ClipForge.git && cd ClipForge
   git checkout claude/ai-stream-clipper && git pull
2. Citește, în ordine: CLAUDE.md, docs/dynamic-edit-recipe.md (mai ales
   secțiunea 10 „Still open"), docs/handoff-dynamic-edit.md.
3. Instalează skill-ul de descoperire și folosește-l ca atare:
   npx -y skills@latest add vercel-labs/skills --skill find-skills --agent claude-code -y
4. Backend venv: server/.venv. În Git Bash `python` NU e pe PATH — folosește
   D:/clipforge/server/.venv/Scripts/python.exe (sau echivalentul de pe mașina
   ta). ffmpeg 8.1 e pe PATH.

CE NU VINE CU REPO-UL (data/, .claude/ și server/.venv/ sunt în .gitignore)
- VOD-ul de 6h (11.6 GB) și artefactele de analiză din
  data/clipper/bbdf781b1064/. Fără ele nu poți randa. Ori le copiezi de pe rig,
  ori rulezi pipeline-ul clipper pe alt VOD și folosești acel project id.
- .claude/launch.json — recreează configurația de preview (vezi secțiunea 3 din
  docs/handoff-dynamic-edit.md).
- Cele 9 Shorts de referință — se descarcă cu yt-dlp, id-urile sunt în
  docs/dynamic-edit-recipe.md.

CE E DEJA FĂCUT
Rețeta măsurată, planificatorul de cadre, renderer-ul într-un singur encode,
subtitrări cu highlight pe cuvânt, 23 de teste, pagina de review. Detalii în
mesajul commit-ului fe304c7.

CE AI DE FĂCUT, în ordinea valorii
1. Termină profilarea referințelor. Workflow-ul e salvat la
   docs/workflows/ref-style-extraction.js — rulează-l cu
   Workflow({scriptPath: "docs/workflows/ref-style-extraction.js"}).
   Are nevoie de cele 9 fișiere descărcate întâi. Rulările anterioare au picat
   pe cotă de sesiune după 2 din 9 profile; dacă pică iar, reia cu
   resumeFromRunId ca să nu re-plătești agenții terminați.
2. Implementează pop-ul de intrare al subtitrării: 0.91x -> 1.05x la ~83ms ->
   1.00x la ~130ms, ancorat în centrul textului. Măsurat pe _LQ379ZhspI, încă
   neimplementat. Se face cu un transform ASS \t per eveniment, în
   services/caption_overlays.py, tot opt-in ca highlight-ul pe cuvânt.
3. Re-calibrează services/clipper/dynamic_edit.py DEFAULT_STYLE pe toate cele
   nouă profile, nu pe două. Nu media referințele — vezi de ce în secțiunea 1
   din recipe.
4. Rezolvă cadrele slabe rămase: 1-2 pe clip prind ecran de loading sau bara de
   stream. Vezi secțiunea 4 din handoff.

REGULI
- CLAUDE.md are prioritate: fișiere sub 500 de linii, un commit per lot,
  fără abstracții speculative.
- Măsoară înainte să afirmi. Fiecare număr din recipe are o comandă în spate.
- Verifică vizual: randează preview, construiește contact sheets, uită-te la
  ele. Nu declara „arată bine" fără să te fi uitat.
- Spune explicit ce n-ai acoperit.
```

---

## 1. Ce s-a livrat

Commit `fe304c7` pe `claude/ai-stream-clipper`.

| fișier | ce e |
|---|---|
| `server/services/clipper/dynamic_edit.py` | planificatorul: unde tai, ce cameră, unde e subiectul |
| `server/services/clipper/dynamic_render.py` | EDL -> un singur `-filter_complex` |
| `server/services/clipper/captions.py` | atașează `words` pe fiecare chunk |
| `server/services/caption_overlays.py` | highlight pe cuvântul activ (opt-in) |
| `server/tests/test_clipper_dynamic.py` | 23 de teste, toate pure |
| `scripts/render_dynamic_clip.py` | driverul |
| `scripts/build_dynamic_review.py` | pagina de review |
| `docs/dynamic-edit-recipe.md` | rețeta măsurată |
| `docs/workflows/ref-style-extraction.js` | workflow-ul de profilare |

Suita: 236 passed. Cele 2 eșecuri din `test_tiktok_transform.py` sunt
preexistente și documentate în CLAUDE.md (routerul TikTok nu e montat).

## 2. Comenzi

```bash
# randare (preview rapid: 540x960, crf 28)
python scripts/render_dynamic_clip.py bbdf781b1064 --rank 0 --preview

# randare finală, primele 3 candidate
python scripts/render_dynamic_clip.py bbdf781b1064 --top 3

# cu mișcare în cadru repusă (implicit e statică — vezi recipe §8)
python scripts/render_dynamic_clip.py bbdf781b1064 --top 3 \
  --style '{"push_amount":0.07,"shake_px":6}'

# pagina de review
python scripts/build_dynamic_review.py bbdf781b1064
```

## 3. Preview în browser

`.claude/` e în `.gitignore`, deci `launch.json` nu vine cu repo-ul. Adaugă:

```json
{
  "name": "ClipForge Dynamic Review",
  "runtimeExecutable": "D:\\clipforge\\server\\.venv\\Scripts\\python.exe",
  "runtimeArgs": ["-m", "http.server", "8777",
                  "--directory", "D:\\clipforge\\data\\clipper\\bbdf781b1064\\dynamic"],
  "port": 8777
}
```

Apoi `preview_start` cu numele configurației. Pagina arată playerul plus banda
de montaj: fiecare cadru colorat după camera activă, marcaje pe flash-uri,
hover pentru energie/acțiune/replică.

## 4. Ce ştiu că e încă imperfect

- **1-2 cadre slabe pe clip.** Camera de gameplay prinde uneori un ecran de
  loading negru sau bara de jos a stream-ului. Garda `game_dead_below` prinde
  regiunile complet moarte, nu și pe cele vizual plictisitoare. O măsurătoare de
  varianță de luminanță pe bandă, alături de mișcare, ar rezolva-o.
- **Nu există diarizare.** Tăieturile cad pe pauze de vorbire, nu pe schimbarea
  vorbitorului, care e semnalul real din referințe.
- **`ACTION_BAND` în `scripts/render_dynamic_clip.py` e hardcodat** pentru
  layout-ul acestui stream (facecam stânga-jos, chat dreapta). Alt stream cere
  alte fracții. Ar trebui derivat din poziția detectată a facecam-ului.
- **`regions.json` al pipeline-ului a ratat facecam-ul** (`webcam: null`) și a
  clasificat VOD-ul drept `talking_head` cu încredere 0.48. Motorul dinamic îl
  ocolește complet, dar detecția din upstream e în continuare greșită pentru
  stream-urile de gameplay.
- **`candidates.json` s-a rescris singur** în timpul lucrului (scorurile au
  scăzut de la 72.2 la 61.9); ceva din backend re-scorează. N-am investigat.

## 5. Instrumente folosite şi de ce

- **`find-skills`** (vercel-labs) — instalat şi rulat pe 4 interogări. Concluzie
  onestă: nimic în registry nu face montaj local multi-cam. Top hit-ul,
  `skills-collective/skills@video-edit`, are 129.5K instalări dar e un router
  către API-ul cloud RunComfy, cu 12 stele pe GitHub.
- **Workflow de agenţi** — un agent per referinţă, cu schemă structurată, plus
  un agent de sinteză. Barieră înainte de sinteză, pentru că sinteza chiar are
  nevoie de toate profilele deodată.
- **Verificare empirică înainte de cod** — tehnica `sendcmd` + `crop` a fost
  testată pe VOD-ul real şi confirmată vizual pe un contact sheet ÎNAINTE să
  scriu vreo linie din `dynamic_render.py`.
