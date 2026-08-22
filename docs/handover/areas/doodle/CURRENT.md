# Handover — Auto Story Doodle

## Scop

Construiește un videoclip de tip story din script, voiceover, imagini și scene.

## Flux funcțional

```text
new project → script → voiceover → image generation/upload → storyboard → render → backup/download
```

## Frontend

- `src/app/doodle/page.tsx` — listă și creare proiect.
- `src/app/doodle/[id]/page.tsx` — wizard-ul proiectului.
- `src/components/doodle/` — scene, actions, uploads, generator local și status.

## Backend/API

- `server/routers/doodle.py`
  - voices și image providers;
  - Comfy status;
  - list/create/get/delete project;
  - script, voiceover, generate images, render;
  - backup images, patch settings, prompts CSV/JSON.
- `server/routers/doodle_images.py`
  - upload bulk/single;
  - delete image;
  - reorder scenes.

## Worker și servicii

- `server/workers/doodle_pipeline.py` — job orchestration.
- `server/services/doodle/storage.py` — storyboard și fișiere proiect.
- `server/services/doodle/renderer.py` / `renderer_ffmpeg.py` — render.
- `server/services/doodle/script_generator.py` — script AI.
- `server/services/doodle/kokoro_service.py` — TTS local.
- `server/services/doodle/comfy_client.py` — imagini prin ComfyUI.

## Persistență și output

Proiectul folosește `storyboard.json` și directoare per proiect, nu un model SQL complet.

## Riscuri

- scrierile storyboard-ului trebuie să fie atomice;
- upload bulk poate depăși limitele proxy-ului;
- ComfyUI și provider-ele externe au nevoie de timeout/reconnect;
- scenele reordonate trebuie să își actualizeze și fișierele asociate;
- cleanup-ul proiectului trebuie să fie complet și verificabil.

