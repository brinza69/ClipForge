# ClipForge → Postiz scheduled poster (Level B)

Posts each finished row's role variants to your TikTok accounts at **preset
daily hours**, via **self-hosted Postiz** using TikTok's **official OAuth API**,
with each country's Postiz stack routed through **that country's residential
proxy** so a genuinely-localized account's traffic stays consistent.

**Scope / boundary:** this is official-API posting for real, localized accounts.
The proxy is per-country network consistency, NOT fingerprint/device spoofing.
No antidetect browser is involved and none is needed.

Difference vs the Upload-Post poster (`README.md`):
- **B gives per-country egress IP** (self-hosted) — A cannot (shared aggregator IP).
- **B costs the TikTok app audit** (~2–4 wks; posts are SELF_ONLY until approved).
  A skips the audit because Upload-Post owns an audited app. Per your own research
  the posting IP is not the main signal — so choose B only if you specifically
  want the country egress. Cost-wise A is cheaper/faster.

---

## Pieces in this folder

| File | What |
|---|---|
| `lib/schedule.js` | The scheduling core — assigns each variant to the next free daily slot per account, in the account's timezone. Pure, dependency-free. |
| `lib/schedule.test.js` | `node n8n/lib/schedule.test.js` → 9 tests (tz math, slot assignment, day-roll, no double-book, role mapping). All pass. |
| `build-workflow.js` | Generates the n8n workflow, embedding the tested scheduler verbatim. |
| `clipforge-postiz-poster.json` | The importable n8n workflow (scheduled, targets Postiz). |
| `postiz/docker-compose.yml` + `.env.example` | One Postiz stack per country, egress via that country's proxy. |

Regenerate the workflow after editing the scheduler: `node n8n/build-workflow.js`.

---

## Sheet: add an `Accounts` tab

`Sheet1` is unchanged (the rig writes DESCRIERE + `*_url` + `status=ready`).
Add a second tab **`Accounts`** — one row per TikTok account:

| account_id | role | country | postiz_integration_id | slots | timezone | caption_suffix |
|---|---|---|---|---|---|---|
| ro_naratorul | narator | RO | 3f2a… | 09:00,13:00,19:00 | Europe/Bucharest | #poveste |
| fr_conteur | narator | FR | 9b1c… | 10:00,18:00 | Europe/Paris | #histoire |

- **role** ∈ narator / comentator / povestitor (matches the rig's variants).
- **slots** = that account's daily post times, in **timezone** (the country's TZ).
- **postiz_integration_id** = the connected TikTok integration's id in that
  country's Postiz (copy it from Postiz after you connect the account).
- **caption_suffix** = per-account hashtags → keeps captions non-identical
  across the fleet (spam-cluster avoidance).

---

## One Postiz stack per country (proxy egress)

```
cp n8n/postiz/.env.example n8n/postiz/.env        # edit per country
# RO stack: COUNTRY_PROXY empty (native), POSTIZ_PORT=5000
# FR stack: COUNTRY_PROXY=http://user:pass@fr-residential:port, POSTIZ_PORT=5001
docker compose -f n8n/postiz/docker-compose.yml --env-file n8n/postiz/.env up -d
```

Repeat per country with a distinct port + that country's proxy. In each Postiz:
1. Create a TikTok app (TikTok developer portal) → submit for **Content Posting
   audit**. Until approved, posts are private (SELF_ONLY).
2. Connect each account: **log in / authorize from that country's IP** (the
   proxy, or do it once from a real device there). Region is fixed here.
3. Copy each connected TikTok integration's id → the `Accounts` tab.

---

## n8n

1. Import `clipforge-postiz-poster.json`.
2. Env (n8n): `CLIPFORGE_SHEET_ID`, `POSTIZ_URL` (the country stack's URL — for
   multi-country, run one workflow per country pointing at its Postiz, or fan
   by country in the Code node).
3. Credentials: Google Sheets OAuth2 (owns the sheet) on both Sheets nodes;
   HTTP Header Auth `Authorization: <Postiz API key>` on the Postiz node.
4. The **Plan schedule** Code node needs no editing — it's the tested core.
   Optional: `JITTER_MIN` (default 12) randomizes the exact post minute.
5. Activate. Every 15 min it reads `ready` rows + the Accounts tab, computes
   each variant's next free slot per account, and calls Postiz to **schedule**
   the post at that time (`type:"schedule"`, `date:<slot ISO>`).

> Verify the Postiz create-post payload against your Postiz version's public API
> (`POST /public/v1/posts`). Field names (`integrations`, `content`, `date`)
> follow the current docs; adjust the HTTP node's `jsonBody` if yours differs.

---

## Honest caveats

- **Audit gate:** nothing posts publicly until each country's TikTok app passes
  audit. Budget 2–4 weeks. (A/Upload-Post avoids this.)
- **Posting IP is a minor signal** (per your research) — B's per-country egress
  is belt-and-suspenders, not a magic ban shield.
- **The real ban/RPM risk is content originality**, not the poster. Reclipped
  content gets suppressed regardless of how cleanly it's posted.
- **Caps:** keep ≤ a few posts/account/day; the scheduler only fills the slots
  you define, so set 2–4 slots/account.
