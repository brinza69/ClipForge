# ClipForge — API Reference

ClipForge's backend is a local FastAPI server. Everything below is for
integrators automating the pipeline (Google Sheets, cron, Zapier/n8n/Make,
Apps Script).

- **Base URL:** `http://localhost:8420`
- **Auth:** none — the server is local-only by default.
- **Interactive docs:** FastAPI auto-generates them at
  `http://localhost:8420/docs` (Swagger) and `/redoc` when the backend runs.
- **From the frontend**, call through the Next.js proxy `/worker-api/*`
  (rewrites to `:8420/api/*`) — never `localhost:8420` directly (browser
  extensions can kill cross-port fetches).

---

## Automation: `POST /api/auto`

The whole parallel pipeline from one POST. No UI involvement.

### Body

| field | type | default | notes |
|---|---|---|---|
| `url` | string\|null | — | required when `from_sheets` is false |
| `variant_preset_ids` | string[] | — | 1–4 ids from `data/variant_presets/` |
| `from_sheets` | bool | `false` | pull the next row's URL + number from the configured Sheet |
| `auto_detect_zones` | bool | `true` | refine erase/caption zones per-frame |
| `erase_method` | string | `"lama"` | `lama` \| `ns` \| `blur` |
| `transcript_engine` | string | `"ollama"` | `ollama` \| `openai` \| `anthropic` |
| `transcript_target_lang` | string\|null | `null` | e.g. `"ro"`; null keeps source language |
| `erase_zone` | object\|null | `null` | `{x,y,w,h,src_w,src_h}` — overrides the default top band |
| `caption_zone` | object\|null | `null` | same shape — overrides the default bottom band |

### Returns

```json
{
  "job_id": "abc123…",
  "project_id": "def456…",
  "url": "https://…",
  "variants": ["Grinch", "Narrator"],
  "sheets_row": 5,
  "sheets_number": "42",
  "src_dims": {"w": 1080, "h": 1920},
  "zones": {"erase": {...}, "caption": {...}}
}
```

Poll `GET /api/jobs/{job_id}` until `status` is `done` / `failed` /
`cancelled`, then fetch results via `GET /api/parallel/{job_id}/result`.

### Errors

| code | meaning |
|---|---|
| 400 | bad input (missing url, bad preset id format) |
| 401 | Sheets scope missing — reconnect Drive |
| 404 | a `variant_preset_id` doesn't exist |
| 409 | `from_sheets` but Sheets not configured / row empty |
| 422 | yt-dlp metadata fetch failed (bad/unreachable URL) |

### Examples

Explicit URL, two saved presets:
```bash
curl -X POST http://localhost:8420/api/auto \
  -H 'Content-Type: application/json' \
  -d '{
        "url": "https://www.tiktok.com/@user/video/123",
        "variant_preset_ids": ["grinch", "narrator"],
        "auto_detect_zones": true
      }'
```

Pull the next row from the configured Sheet:
```bash
curl -X POST http://localhost:8420/api/auto \
  -H 'Content-Type: application/json' \
  -d '{ "from_sheets": true, "variant_preset_ids": ["grinch"] }'
```

---

## Google Sheets: `/api/sheets/*`

One config per install, stored in `data/sheets_config.json` with a
`next_row` cursor. Requires Drive connected with the `spreadsheets` scope.

### `GET /api/sheets/config`
Returns the saved config or `{"configured": false}`.

### `POST /api/sheets/config`
```json
{
  "spreadsheet_url": "https://docs.google.com/spreadsheets/d/<ID>/edit",
  "tab": "Sheet1",
  "col_url": "B",
  "col_number": "A",
  "col_description": "C",
  "start_row": 2
}
```
Validates access (opens the sheet, checks the tab exists), sets
`next_row = start_row`.

### `POST /api/sheets/pull-next`
Reads `<col_url><next_row>` + `<col_number><next_row>`. Does NOT advance
`next_row`. Returns `{row, url, number}` or `{empty: true, row, message}`.

### `POST /api/sheets/commit`
```json
{ "row": 5, "description": "…" }
```
Writes the description into `<col_description><row>` and advances
`next_row` to `row+1`.

### `POST /api/sheets/skip-row`
Bumps `next_row` by 1 without writing (for empty rows).

### `DELETE /api/sheets/config`
Wipes the config (forces re-setup).

---

## Google Drive OAuth: `/api/drive-auth/*`

3-legged user OAuth (files use the user's 15 GB quota; service accounts
have 0 GB and fail on personal My Drive).

| endpoint | does |
|---|---|
| `GET /status` | `{connected, client_configured, email}` |
| `POST /connect` | returns `{auth_url}`; open it, a loopback server (port 8421) catches the redirect and saves the token. Poll `/status` until connected. |
| `POST /disconnect` | forget the saved token (keeps the client JSON) |
| `POST /client` | upload the OAuth Client JSON (multipart `file`, or JSON body `{content}`). Must be a Desktop client; 50KB cap. Clears any stale token. |
| `DELETE /client` | remove client JSON + token (full reset) |

**Consent must include both `drive.file` AND `spreadsheets` scopes** for
the Sheets integration. If only Drive appears in the consent screen, add
the `spreadsheets` scope under Google Auth Platform → Data Access, then
Disconnect + Connect again.

---

## Whisper transcription: `/api/transcript/device`

### `GET /api/transcript/device?verify=false`
Reports configured + actual model/device. With `?verify=true` it
force-loads the model (~10s) to confirm CUDA actually works (vs a silent
CPU fallback). Returns `configured_model/device`, `actual_*`,
`fell_back_to_cpu`, `cuda_available`, `cuda_device_name`, `models[]`,
`devices[]`.

### `POST /api/transcript/device`
```json
{ "whisper_model": "large-v3", "whisper_device": "cuda" }
```
Persists to `data/whisper_config.json` and drops the cached model so the
next transcription reloads with the new settings. Models: `tiny`, `base`,
`small`, `medium`, `large-v3`. Devices: `auto`, `cuda`, `cpu`.

---

## Variant presets: `/api/variant-presets`

A preset is a saved voice + caption + commentator bundle (the building
block `POST /api/auto` references by id).

| endpoint | does |
|---|---|
| `GET /` | list all presets |
| `POST /` | create/overwrite (`{name, preset_id?, tts_*, caption_*, commentator_preset_id, …}`) |
| `DELETE /{id}` | delete one |

---

## Parallel pipeline (UI-driven): `/api/parallel/*`

| endpoint | does |
|---|---|
| `POST /start` | enqueue a multi-variant job (2–4 variants). Body documented in `routers/parallel.py:StartRequest`. |
| `GET /{job}/result` | per-variant results once done (+ `sheets_commit` when Sheets-linked) |
| `GET /{job}/download/{i}` | stream variant i's mp4 |
| `GET /{job}/download/{i}/part/{p}` | stream a split part |
| `GET /recent?limit=&` | last N completed runs |

---

## Jobs: `/api/jobs/*`

| endpoint | does |
|---|---|
| `GET /?status=&project_id=` | list jobs; `status` accepts a comma list (e.g. `queued,running`) |
| `GET /{id}` | one job's status + progress (poll this) |
| `POST /{id}/cancel` | cancel a queued/running job |

Job status flows: `queued → running → done` (or `failed` / `cancelled`).
`progress` is 0.0–1.0; `progress_message` is human-readable.

---

## Remix pipeline (single output): `/api/remix/*`

| endpoint | does |
|---|---|
| `POST /preview` | `{url}` → title, thumbnail, width, height, duration |
| `POST /start` | enqueue a single-output remix |
| `GET /{job}/result` | result metadata |
| `GET /{job}/download` | stream the mp4 |
| `GET /recent?limit=&offset=` | paginated past runs (+ `total`) |
| `DELETE /{job}` | delete a run (files + DB row) |
