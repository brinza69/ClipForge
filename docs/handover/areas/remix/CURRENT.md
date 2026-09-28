# Handover — Remix Pipeline

## Scop

Transformă o sursă video într-un video vertical final, cu transcript, voiceover/TTS, speed matching, captions și descrieri.

## Flux funcțional

```text
source → preview → download/upload → transcribe → clean transcript → TTS → speed match → caption burn → descriptions → result
```

## Frontend

- `src/app/remix/page.tsx` — wizard-ul principal Remix.
- `src/components/remix/` — preseturi, comentator, configurare și runs anterioare.
- `src/lib/api-error.ts` — normalizarea erorilor API.

## Backend/API

- `server/routers/remix.py`
  - preview source;
  - start pipeline;
  - list recent jobs;
  - get/download/delete result.
- `server/routers/jobs.py` — status și cancel pentru job.

## Worker și servicii

- `server/workers/remix_pipeline.py` — orchestration principală.
- `server/services/downloader.py` — URL validation, metadata și download.
- `server/services/transcriber.py` — transcript Whisper/faster-whisper.
- `server/services/transcript_cleaner.py` — curățare transcript cu engine AI.
- `server/services/elevenlabs.py` — TTS ElevenLabs.
- `server/services/speed_match.py` — sincronizare voice/video.
- `server/services/caption_overlays.py` — ASS și caption render.
- `server/services/descriptions.py` — generare descrieri.
- `server/services/commentator_overlay.py` — comentator video.

## Output

- job metadata în DB;
- video final în exports;
- transcript, audio, captions și fișiere temporare în workspace-ul job-ului.

## Riscuri

- pipeline lung și sensibil la timeout-uri externe;
- un retry complet poate repeta operații costisitoare;
- FFmpeg trebuie să primească dimensiuni pare;
- transcript cleaner poate elimina punctuația dacă nu este configurat corect;
- procesarea trebuie să curețe toate artefactele intermediare la failure.

