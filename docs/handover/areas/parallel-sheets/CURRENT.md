# Handover — Parallel from Sheets

## Scop

Folosește o configurație Google Sheets pentru a alimenta procesarea paralelă pe rânduri.

## Frontend

- `src/app/parallel-sheets/page.tsx` — configurare, preview, pull, skip, commit și progress.

## Backend/API

- `server/routers/sheets.py`
  - get/delete/save config;
  - pull next row;
  - commit row;
  - skip row.

## Servicii și integrări

- config persistentă pentru Sheets;
- Drive/OAuth prin `server/routers/drive_auth.py` și serviciile Drive;
- procesarea efectivă folosește fluxurile Parallel/Remix.

## Output și stare

- configurație Sheets;
- rând curent;
- rezultat commit/skip;
- joburi de procesare asociate.

## Riscuri

- rândul trebuie rezervat înainte de procesare pentru a evita dublarea;
- commit-ul în Sheets trebuie tratat ca operație externă și retryabilă;
- configurația nu trebuie pierdută dacă aplicația se închide în timpul procesării;
- token-urile OAuth nu trebuie scrise în loguri.

