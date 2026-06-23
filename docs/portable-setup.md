# ClipForge — Portable Setup & Autonomous Sheet Runner

How to get ClipForge running on a **fresh machine / new GPU** and process a
Google Sheet of links fully autonomously. The LaMa eraser **auto-tunes its
batch size to the card's VRAM** (6 GB → 8, 8 GB → 16, 12 GB → 24), so no manual
tuning is needed when you swap GPUs.

## 1. Install

```powershell
# Frontend deps
npm install

# Python venv + base requirements
.\scripts\setup.ps1

# GPU stack (torch CUDA + LaMa neural eraser + audioop-lts for Py3.13)
.\scripts\setup-gpu.ps1
```

(Linux/macOS: use the matching `scripts/*.sh`.)

## 2. Load the role presets

```powershell
.\scripts\seed-presets.ps1
```

This restores the **narator / comentator / povestitor** presets (voice + caption
style + avatar + 1-min split). They are **redacted** — no Drive folder links — so
you re-add those in step 3.

## 3. Re-add the private bits (never committed)

In the running app (`.\scripts\start.ps1`, then http://localhost:3000):

- **API keys** — Settings → paste your OpenAI + ElevenLabs keys.
- **Google** — Settings → Connect Google Drive (Drive + Sheets consent).
- **Avatars** — Commentators → upload the 3 avatar videos as `narator`,
  `comentator`, `povestitor` (green-screen; set chroma key to the bg colour).
- **Drive folder per role** — open each preset, set its destination Drive folder.
- **Sheet** — Parallel-from-Sheets → configure spreadsheet + columns
  (number / url / description) + start row.

## 4. Run autonomously

```powershell
.\scripts\run-sheet.ps1
```

Processes every remaining sheet row with **zero interaction**: pull row →
translate to RO → GPU erase (autodetect) → for each role: RO voice + big RO
captions + avatar, split into 1-min parts → upload to that role's Drive folder →
write the AI description back to the sheet → advance → repeat.

For **continuous** processing that never stops — it drains the sheet, then waits
and picks up any new rows you add later — run `.\scripts\watch-sheet.ps1`
instead (Ctrl+C to stop).

Watch progress live at **http://localhost:8420/exports/live.html**.
