# Handover — Utilities

## Scop

Oferă operații independente pentru erase/inpaint, eliminarea tăcerilor și upscale video.

## Frontend

- `src/app/utilities/page.tsx` — intrarea în utilities.
- `src/app/utilities/caption-eraser/page.tsx` — selectare regiune și erase.
- `src/app/silence/page.tsx` — silence removal.
- `src/app/utilities/upscale/page.tsx` — AI upscale.

## Backend/API

- `server/routers/utilities.py`
  - erase upload/job/download;
  - silence-remove job/result/download;
  - upscale job/result/download.
- `server/routers/jobs.py` — status și cancel comun.

## Worker și servicii

- `server/workers/utility_jobs.py` — dispatch și subprocess jobs.
- `server/services/inpaint.py` — OpenCV/LaMa inpaint.
- `server/services/silence_remover.py` — detectare și eliminare silence.
- `server/services/upscaler.py` — cadre, Real-ESRGAN și re-encode.
- `server/services/ffmpeg_tools.py` sau helper-ele locale — probe și encode.

## Output

- workspace per utility job;
- rezultat video descărcabil;
- cleanup amânat după download.

## Riscuri

- unele uploaduri sunt citite integral în memorie;
- Real-ESRGAN trebuie să aibă timeout global;
- cadrele PNG pot ocupa zeci de GB;
- procesul tree trebuie omorât la cancel/timeout;
- result endpoint-ul trebuie să valideze existența și integritatea outputului.

