# Handover — Shared Infrastructure

## Scop

Componentele comune tuturor modulelor: startup, API, DB, modele, job queue, storage, configurare și integrări.

## Frontend comun

- `src/app/layout.tsx` — layout global.
- `src/app/page.tsx` — redirect către pagina principală.
- `src/components/layout/sidebar.tsx` — navigare module.
- `src/components/layout/running-jobs-badge.tsx` — job status global.
- `src/lib/api.ts` — client legacy minimal.
- `src/lib/api-error.ts` — normalizare erori API.

## Backend comun

- `server/main.py` — startup/shutdown, mounts, routers și exception handling.
- `server/config.py` — settings, paths și limite.
- `server/database.py` — engine SQLite, WAL și init/migrations.
- `server/models.py` — SQLAlchemy models și enums.
- `server/job_queue.py` — queue, lanes, claim, progress, cancel, recovery.
- `server/routers/jobs.py` — list/get/SSE/cancel job.

## Integrări și module suport

- `server/routers/drive_auth.py` — Google Drive OAuth/client.
- `server/routers/commentators.py` — preseturi de commentator, video, thumb, AI processing și chroma.
- `server/routers/variant_presets.py` — preseturi pentru Parallel.
- `server/routers/auto.py` — quick auto run și transformarea presetului în variantă.
- `server/services/downloader.py` — URL guard, metadata și download.
- `server/services/retry.py` — retry cu backoff/jitter.
- `server/services/cleanup.py` — cleanup media și workspace.
- `server/services/secret_storage.py` — storage pentru secrete/config.
- `server/services/drive_oauth.py` — OAuth persistence și tokens.

## Persistență

- SQLite pentru proiecte, clips, jobs, transcript și feedback.
- JSON/directories per project pentru modulele cu storage pe disc.
- `data/media`, `data/exports`, `data/cache`, `data/temp`, `data/thumbnails`, `data/db` pentru fișiere.

## Riscuri globale

- două backend-uri pe aceeași SQLite necesită ownership/lease corect în queue;
- migrațiile nu trebuie să ascundă erori reale;
- cleanup-ul trebuie să fie per-job și verificabil;
- health check-ul trebuie separat în liveness/readiness;
- toate request-urile frontend trebuie să folosească proxy-ul și un client comun;
- toate procesele externe și integrările HTTP trebuie să aibă deadline;
- config-ul și secretele trebuie scrise atomic și fără expunere în loguri;
- **`test_job_claim::test_heartbeat_renews_lease_and_fences_old_owner` este intermitent.**
  Observat picând la suita completă pe 2026-08-22 și trecând 10 din 10 rulări izolat, în două medii
  diferite. `job_queue.py` nu a fost modificat. Tratează-l ca instabilitate a testului sub încărcare,
  nu ca dovadă că lease-ul e rupt — dar nu îl marca stabil până nu se reproduce și se explică.

