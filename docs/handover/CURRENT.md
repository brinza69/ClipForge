# ClipForge — Current Application Handover

**Scope:** aplicația întreagă, nu doar AI Stream Clipper.  
**Data hărții:** 22 august 2026.  
**TikTok:** exclus din această hartă, conform cerinței proiectului.

## Stare, 22 august 2026

Reasoning v2 al Clipper-ului este implementat până la Batch 6 inclusiv, rulează în
`story_v2_shadow` — calculează ordinea nouă și o înregistrează, dar livrează în continuare ordinea
legacy — și **nu este aprobat implicit**: `story_v2` este refuzat de API până la comparația oarbă pe
corpus. Detaliile și ce a rămas deschis sunt în [`areas/clipper/CURRENT.md`](areas/clipper/CURRENT.md).

Teste backend: **916 trec**, 2 pică — ambele din `test_tiktok_transform.py`, cu 404, pentru că
routerul TikTok nu este montat. TypeScript curat.

## Cum folosești acest document

Acest fișier oferă imaginea de ansamblu. Pentru implementare sau debugging, deschide handover-ul modulului respectiv din [`INDEX.md`](INDEX.md).

## Structura produsului

ClipForge este un studio local de procesare video AI cu:

- Next.js frontend în `src/`;
- FastAPI backend în `server/`;
- SQLite + SQLAlchemy async pentru entitățile persistente;
- job queue comun pentru operații lungi;
- FFmpeg și servicii AI locale/externe pentru procesare;
- fișiere pe disc pentru media, proiecte, cache și rezultate.

## Fluxurile principale

```text
Remix:
input → preview → download/upload → transcript → TTS → speed match → captions → export → descriptions

Parallel:
input + variante → reutilizare Remix → procesare concurentă → rezultate separate

Doodle:
project → script → voiceover → images → storyboard → render

Captions:
template/font/source → preview → optional transcription → FFmpeg burn → download

Transcript:
text/upload → engine selectat → clean job → result/download

TTS:
engine/voice → synthesize job → audio result/download

Utilities:
upload → erase/silence/upscale job → result/download

Clipper:
source → ingest → transcribe → analyze → score → candidate clips → preview/export
```

## Reguli globale de lucru

- Frontend-ul trebuie să apeleze backend-ul prin proxy-ul Next `/worker-api/...`.
- Job-urile lungi trebuie urmărite prin status persistent, nu doar prin state local în browser.
- Orice proces extern trebuie să aibă timeout și cleanup.
- Orice output final trebuie verificat înainte să fie raportat ca finalizat.
- Modificările de DB trebuie documentate și migrate atât în model, cât și în schema existentă.
- TikTok nu este inclus în această hartă.

## Priorități globale

1. Stabilizarea job queue-ului și a anulării.
2. Eliminarea job-urilor care pot rămâne blocate.
3. Consistență între DB și filesystem.
4. Upload streaming și limite reale de memorie/disk.
5. Readiness checks pentru backend și dependențe.
6. Test harness stabil și teste de reziliență.

## Handover pe module

- [`Clipper`](areas/clipper/CURRENT.md)
- [`Remix`](areas/remix/CURRENT.md)
- [`Parallel`](areas/parallel/CURRENT.md)
- [`Parallel from Sheets`](areas/parallel-sheets/CURRENT.md)
- [`Doodle`](areas/doodle/CURRENT.md)
- [`Captions`](areas/captions/CURRENT.md)
- [`Transcript`](areas/transcript/CURRENT.md)
- [`TTS`](areas/tts/CURRENT.md)
- [`Utilities`](areas/utilities/CURRENT.md)
- [`Infrastructure`](areas/infrastructure/CURRENT.md)

