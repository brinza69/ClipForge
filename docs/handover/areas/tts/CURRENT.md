# Handover — TTS Studio

## Scop

Gestionează engine-urile locale/externe, voice packs, ElevenLabs și sinteza audio.

## Frontend

- `src/app/tts/page.tsx` — engine, voices, API key, synthesize, status și download.
- `src/components/settings/api-keys-card.tsx` — starea cheilor ElevenLabs și transcript.

## Backend/API

- `server/routers/tts.py`
  - health și engines;
  - ElevenLabs status/key;
  - list/upload/delete voices;
  - synthesize;
  - get job și download rezultat.

## Servicii

- `server/services/elevenlabs.py` — integrare ElevenLabs.
- `server/services/doodle/kokoro_service.py` — engine local folosit și de Doodle.
- `server/services/retry.py` — retry controlat pentru request-uri retryable.

## Output

- fișier audio rezultat;
- job metadata;
- voice packs persistente.

## Riscuri

- cheia API trebuie protejată și niciodată logată;
- retry-ul poate dubla costul dacă operația nu este idempotentă;
- request-urile externe trebuie să aibă timeout și limită de durată;
- fișierele audio parțiale trebuie șterse la failure;
- job-ul nu trebuie să rămână activ când provider-ul este indisponibil.

