# ClipForge → TikTok auto-poster (n8n + Upload-Post)

v1 = **TikTok only** (Facebook Pages = v2). Posts each finished row's 3 role
variants to the mapped TikTok accounts via the **official** TikTok API through the
**Upload-Post** aggregator (it owns the audited TikTok app, so you skip the 2–4
week audit). n8n is the orchestrator only — it never talks to TikTok directly.

Full rationale: `../RESEARCH-antiban-posting-2026-06-25.md`.

---

## The sheet contract (already wired)

`Sheet1` row 1 headers (row 1 was `NR | LINK | TRANSCRIPT | DESCRIERE`; I added the
last four — **no rows were shifted**, the dispatcher's absolute-row writes are intact):

| NR | LINK | TRANSCRIPT | DESCRIERE | narator_url | comentator_url | povestitor_url | status |
|----|------|------------|-----------|-------------|----------------|----------------|--------|

- **DESCRIERE** = the AI caption. **narator_url / comentator_url / povestitor_url** =
  the variant video download URLs (newline-separated if a video was split into parts).
- **status** flow: `ready` (written by ClipForge when a row finishes) → `posting`
  (n8n claimed it) → `posted` / `error: …` (n8n result).
- Rows are matched by the **NR** column (its value is NOT the sheet row number).

> The ClipForge side (`server/services/drive_upload.py` + `scripts/dual_dispatch.py`)
> already writes DESCRIERE + the 3 URLs + `status=ready`, and makes each uploaded MP4
> anyone-with-link so Upload-Post can fetch it by URL. (Set `CLIPFORGE_DRIVE_PUBLIC=0`
> to keep files private — then use the bytes-download fallback below.)

---

## What the workflow does

`Every 15 min` → `Read Sheet1` → `Only status = ready` → `Claim rows (posting)` →
`Build post jobs` (fan out role×part×account) → `Upload-Post → TikTok` (1 call per
job, throttled 90s apart) → `Reconcile per row` → `Mark row posted/error`.

- Claims rows (`posting`) **before** posting, so a re-poll never double-posts.
- A row becomes `posted` only if **all** its posts succeed; otherwise `error: …`.
- Throttle is the HTTP node's batching (1 call / 90s). Raise it for a slower cadence.

---

## One-time setup

### 1. Activate the ClipForge write-back (already coded — needs a rig restart)
The changes are live after the backends + dispatcher restart (the watchdog does this
on reboot, or kill the rig python procs and it respawns them with the new code).
Already done this session.

### 2. Upload-Post account + connect TikTok accounts
- Sign up at https://www.upload-post.com, get your **API key**.
- Connect each TikTok account as an Upload-Post **profile**; note each profile's
  `user` name for the ACCOUNT_MAP.
- **GEO (critical):** an account's country is fixed at creation + first human login,
  NOT at post time. Connect/authorize each **RO** account from a native RO IP (no
  VPN); connect each **foreign** account from that country's residential/mobile
  IP/VPN. Posting from the RO server afterward is fine. (Report §"Anti-ban + multi-country".)

### 3. Import + configure in n8n
1. n8n → Import from File → `clipforge-tiktok-poster.json`.
2. **Google Sheets credential:** create/select a Google Sheets OAuth2 credential
   (the Google account that owns the sheet) on the 3 Google Sheets nodes.
3. **Upload-Post credential:** create an **HTTP Header Auth** credential —
   Name `Authorization`, Value `Apikey YOUR_KEY` — and select it on the
   "Upload-Post → TikTok" node.
4. **Edit ACCOUNT_MAP** in the "Build post jobs" Code node: map each role to its
   TikTok profiles. Give each account a unique `caption_suffix` (hashtags) so the
   same video isn't posted with an identical caption across accounts.
5. (Optional) tune the schedule (15 min) and the throttle (`batchInterval`, 90000 ms).
6. Activate the workflow.

---

## Upload-Post request (what the HTTP node sends)

`POST https://api.upload-post.com/api/upload` — header `Authorization: Apikey <key>`,
multipart body:

| field | value |
|---|---|
| `user` | the account's Upload-Post profile |
| `platform[]` | `tiktok` |
| `video` | the Drive `download_url` (public URL) |
| `title` | the caption (DESCRIERE + caption_suffix), ≤2200 chars |
| `post_mode` | `DIRECT_POST` (posts publicly; Upload-Post's app is audited) |
| `privacy_level` | `PUBLIC_TO_EVERYONE` |
| `is_aigc` | `true` (required — videos are AI voice + avatar) |

Success: `{ "success": true, "results": { "tiktok": { "url": …, "video_id": … } } }`.
Error: `{ "success": false, "message": … }`.

---

## Troubleshooting
- **`video` URL rejected / files private:** add a **Google Drive → Download** node
  (by file id) before the HTTP node and send its binary in the `video` field instead.
- **Row stuck on `posting`:** a post failed mid-row, OR ACCOUNT_MAP is empty for all
  roles (so 0 jobs were built). Fix the cause, then set that row's `status` back to
  `ready` to retry.
- **Verify field names** against https://docs.upload-post.com/api/upload-video/ for
  your plan (names used here are from the current docs, 2026-06-25).
- **Caps:** ~15 posts/account/day; 3 variants/account/day is well under.
- **Big files:** the `uc?export=download` URL works under ~100 MB (your variants are
  ~30–40 MB); larger needs the bytes-download fallback.

## Backlog (v2)
- Facebook Pages (convert profiles → Pages, Meta App Review + Business Verification,
  add `platform[]=facebook`).
- Backfill: already-completed rows (D filled before this change) have no `*_url`/status,
  so they won't auto-post — would need a Drive-folder→row matcher to backfill.
- Per-account AI-rewritten captions; move ACCOUNT_MAP to a dedicated sheet tab.
