# Handover — Parallel Processing

## Scop

Rulează mai multe variante ale aceluiași material folosind pipeline-ul Remix.

## Frontend

- `src/app/parallel/page.tsx` — configurare, variante, start și rezultate.
- `src/components/parallel/` — variant cards, preseturi, Drive și procesare.

## Backend/API

- `server/routers/parallel.py`
  - start;
  - result;
  - download variant;
  - download variant part;
  - recent runs.
- `server/routers/variant_presets.py`
  - list/create/delete preseturi.

## Worker și servicii

- `server/workers/parallel_pipeline.py` — fan-out către variante.
- Reutilizează etapele din `remix_pipeline.py`.
- Folosește transcript, TTS, captions, comentatori și descrieri comune cu Remix.

## Output

- job principal;
- rezultate separate pentru fiecare variantă;
- descărcare individuală sau pe părți.

## Riscuri

- o variantă eșuată nu trebuie să invalideze automat toate variantele;
- job-urile paralele pot suprasolicita GPU-ul și disk-ul;
- presetul folosit trebuie salvat în metadata pentru reproducere;
- rezultatele trebuie să rămână disponibile dacă o variantă vecină eșuează.

