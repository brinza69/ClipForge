# ClipForge — Deployment Guide

ClipForge is a **two-tier app**:

| Tier           | What it is                                    | Where it can run                                   |
| -------------- | --------------------------------------------- | -------------------------------------------------- |
| **Frontend**   | Next.js 15 (Turbopack)                        | Vercel, Netlify, Cloudflare Pages, any Node host   |
| **Backend**    | FastAPI + ffmpeg + yt-dlp + OpenCV + whisper  | Any Linux/macOS/Windows VPS, Fly.io, Railway, Docker, etc. |

The backend **cannot** run inside pure serverless / edge functions — it needs a persistent process, FFmpeg binaries on PATH, file-system storage for `data/`, and several hundred MB of Python deps (OpenCV, faster-whisper, etc.). Host it on anything that can run a long-lived Python process.

---

## Local development

```bash
# Frontend
cp .env.example .env.local
npm install
npm run dev                # http://localhost:3000

# Backend (separate terminal)
cd server
pip install -r requirements.txt
uvicorn main:app --port 8420 --reload
```

---

## Deploying the frontend

### Vercel

1. Import the repo.
2. Root directory: repo root (where `package.json` lives).
3. Add environment variables in the Vercel dashboard:
   - `NEXT_PUBLIC_WORKER_URL` = `https://your-backend.example.com` (the public backend URL)
   - `WORKER_URL_INTERNAL` = same (used by `/worker-api` proxy rewrites)
4. Deploy.

The included `vercel.json` already points the build to Next.js and wires both env vars through.

### Netlify / Cloudflare Pages / any Node host

Set the same two env vars. Build command `npm run build`, output `.next`.

---

## Deploying the backend

The backend needs:

- Python 3.10+ with everything in `server/requirements.txt`
- `ffmpeg` binary on PATH (or set `CLIPFORGE_FFMPEG_PATH`)
- Writable `data/` directory for SQLite, downloads, exports, thumbnails
- Network access for yt-dlp to fetch remote videos

### Recommended hosts

- **Fly.io / Railway / Render / DigitalOcean App Platform** — all support long-lived Python processes with a Dockerfile or requirements.txt auto-detect.
- **Any Linux VPS** with `systemd` or a process manager (`pm2`, `supervisor`).
- **Docker** — bring your own Dockerfile. Base on `python:3.11-slim` and `apt-get install ffmpeg`.

### Environment variables

| Var                            | Purpose                                          | Example                                        |
| ------------------------------ | ------------------------------------------------ | ---------------------------------------------- |
| `CLIPFORGE_ALLOWED_ORIGINS`    | CORS origins (comma-separated)                   | `https://clipforge.vercel.app,http://localhost:3000` |
| `CLIPFORGE_FFMPEG_PATH`        | Override ffmpeg bin dir if not on PATH           | `/usr/bin`                                     |

### Run command

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

(Do **not** use `--reload` in production.)

---

## Connecting frontend → backend

The frontend talks to the backend two ways:

1. **Via Next.js rewrites** — most API calls hit `/worker-api/*`, which is rewritten server-side to `${WORKER_URL_INTERNAL}/api/*`. Keeps the browser same-origin.
2. **Direct browser POST** — the Caption Eraser uploads video files directly to `${NEXT_PUBLIC_WORKER_URL}/api/utilities/erase` to bypass Next.js's 10 MB body limit.

Both env vars should point to the backend's public URL in production.

Make sure `CLIPFORGE_ALLOWED_ORIGINS` on the backend includes every frontend origin (Vercel preview URLs included) or direct upload will fail with CORS errors.

---

## Storage note

The backend writes to `data/` relative to the `server/` process. On ephemeral hosts (Fly, Railway) attach a persistent volume and mount it there, otherwise downloads and exports vanish on redeploy.
