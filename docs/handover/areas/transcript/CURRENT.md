# Handover — Transcript Studio

## Scop

Oferă transcriere, selectarea engine-ului, configurarea Whisper și curățarea transcriptului prin engine AI.

## Frontend

- `src/app/transcript/page.tsx` — engine selection, upload/text input, job status și download.
- `src/components/settings/whisper-card.tsx` — device status și setări Whisper.

## Backend/API

- `server/routers/transcript.py`
  - list engines;
  - configure API keys;
  - device status/config;
  - clean text;
  - clean uploaded file;
  - get job/result/download.

## Servicii

- `server/services/transcriber.py` — faster-whisper și transcript.
- `server/services/transcript_cleaner.py` — cleaning multi-engine.
- `server/services/retry.py` — retry pentru provider-ele compatibile.

## Output

- transcript JSON/text;
- job metadata;
- fișier descărcabil.

## Riscuri

- Whisper CPU este lent, nu trebuie confundat cu hang;
- engine-urile externe au timeout și costuri potențiale;
- uploadul transcript/video trebuie făcut streaming;
- curățarea trebuie să păstreze punctuația când downstream-ul are nevoie de sentence boundaries;
- joburile vechi trebuie curățate fără a șterge rezultate active.

