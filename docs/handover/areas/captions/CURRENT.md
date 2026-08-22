# Handover — Caption Studio

## Scop

Gestionează template-uri, fonturi, surse video, preview-uri, auto-transcription și arderea caption-urilor în video.

## Frontend

- `src/app/captions/page.tsx` — studioul complet.
- `src/components/captions/` — clonare stil, template-uri și controale.

## Backend/API

- `server/routers/captions.py`
  - list/save/get/delete templates;
  - list/upload/delete fonts;
  - upload source;
  - clone style;
  - preview frame;
  - auto-transcribe;
  - enqueue burn;
  - download burn result.

## Servicii

- `server/services/caption_overlays.py` — ASS și preview.
- `server/services/captioner_presets.py` — preseturi și safe zones.
- `server/services/caption_templates.py` — persistence template-uri.
- `server/services/transcriber.py` — auto-transcription.

## Output

- template/font storage;
- source per session;
- preview frame;
- burn job și video final.

## Riscuri

- uploadurile și fonturile trebuie validate și limitate;
- dimensiunile video trebuie normalizate pentru FFmpeg;
- fișierele temporare per session necesită TTL cleanup;
- burn job-ul trebuie să aibă timeout și output validation;
- culorile ASS folosesc formatul invers față de hex-ul UI.

