# Ban-safe multi-account auto-posting — research report (2026-06-25)

## TL;DR

- **Recommended posting engine:** **Upload-Post** (cheapest, official APIs, bundled n8n node, inherits the vendor's audited apps) for the pilot → graduate to **Ayrshare Business** (purpose-built multi-user API) for scale. **Blotato** is a strong RO-only alternative but caps out at ~40 connected accounts (Agency $499/mo) and posts from its own IP. **Self-host Postiz/Mixpost** only if you are willing to pass the TikTok audit + Meta App Review yourself.
- **Official-API feasibility for 10–50+ accounts PER platform: YES, confirmed.** One TikTok app can Direct-Post publicly to unlimited accounts via per-user OAuth; one Meta app can manage unlimited Pages via per-Page tokens. No per-account app registration is required on either platform.
- **The #1 blocker is the TikTok Direct Post audit** (one-time, app-level, ~2–4 weeks, multi-round, reviews a *compliant posting UI*). Until you pass it, every TikTok API post is forced to `SELF_ONLY` (private) and only 5 accounts can authorize per 24h — fatal for the goal. Using an aggregator that already holds an audited app removes this from your critical path. Meta App Review + Business Verification for `pages_manage_posts` is the second blocker.
- **Multi-country / geo verdict (corrected — see Verification):** Region is fixed at **account creation + human login**, not at API-post time. BUT the "post-IP is irrelevant" framing is **unsafe**. Even on official APIs, do **not** fire all 50+ accounts from one shared/datacenter IP — region-mismatched and shared IPs are documented account-linking and trust-score triggers. Get each account *born and OAuth-authorized* from a country-correct **residential/mobile** IP (native RO IP for RO accounts, that country's proxy for foreign), then post server-side.
- **Hard constraint honored:** every recommended tool uses **official TikTok Content Posting API + Meta Graph API only** — no browser automation, no headless bots, no reverse-engineered endpoints. Antidetect browsers/per-account proxies are needed **only** at the manual creation/login step, not for the API posting itself.
- **Recommended architecture in one line:** Keep n8n local as the *orchestrator only* (Sheet trigger → account-map lookup → Drive fetch → fan-out one API call per variant×account → write-back), and put an **already-audited aggregator** between n8n and TikTok/Meta so you never run the audits yourself.

---

## TikTok official posting (2026)

### Bottom line
A single registered TikTok developer app (`client_key`/`client_secret`) can officially **Direct-Post to many distinct creator accounts** — TikTok's model is one app + per-account OAuth tokens, with **no documented hard cap on linked accounts**. So 10–50+ accounts on one app is architecturally fine. The real constraints are (1) you must pass the **app audit** to post anything publicly, and (2) a **per-client "active publishing users in 24h" quota** you must get raised to your fleet size at audit time.

### The product: Content Posting API
Two endpoints, two scopes — this is the central decision:

| | **Direct Post** (`video.publish`) | **Upload-as-draft / Inbox** (`video.upload`) |
|---|---|---|
| Endpoint | `/v2/post/publish/video/init/` | `/v2/post/publish/inbox/video/init/` |
| Result | Posts straight to the creator's profile (per privacy setting) | Lands in the creator's TikTok **inbox/drafts**; a human must finish posting **inside the app** |
| Audit for public posts? | **Yes** — unaudited = SELF_ONLY only | Needs app approval but avoids the heavy Direct Post UX audit; can publish public because a human finalizes it |
| Fit for fully-automated 3-variant × 50-account flow | **This is the one you need** | **Defeats the use case** — every post needs a manual tap |

Both accept the file via `PULL_FROM_URL` (TikTok fetches your MP4 — domain must be verified/owned) or `FILE_UPLOAD` (you upload bytes). Flow: query creator info → `init` → poll `/v2/post/publish/status/fetch/` until `PUBLISH_COMPLETE`. Sources: [Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started), [Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [Upload reference](https://developers.tiktok.com/doc/content-posting-api-reference-upload-video).

### Audit is still required for public Direct Post (confirmed 2026)
- **Unaudited client is hard-restricted:** all posts forced to **`SELF_ONLY`** (private), every authorized account must itself be private at posting time, and **only up to 5 users may post per 24h**. ([Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines))
- **You must pass the app audit** to lift SELF_ONLY: *"your API client must undergo an audit to verify compliance with our Terms of Service."* ([Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post))
- **Correction (per Verification):** unaudited Direct Post posts go **live but private (SELF_ONLY)** — they are **not** silently converted to drafts. "Draft" is the separate `video.upload`/inbox flow, which needs no audit and *can* go public, but requires a human tap in-app. So the audit-free public route exists but is not unattended.

### Audit timeline + difficulty (solo dev, realistic)
- **Timeline:** roughly **2–4 weeks**, multiple feedback rounds; it reviews a **finished app**, not a prototype. No guaranteed SLA — budget more. ([PostPeer](https://www.postpeer.dev/blog/best-tiktok-posting-api))
- **It's a UX-compliance audit, not a security one.** A real rejection ([postiz issue #1362](https://github.com/gitroomhq/postiz-app/issues/1362)) shows what's mandatory and blocking:
  - Show the creator's nickname + account being posted to, plus posting limits
  - **Dynamic** privacy options pulled live from creator-info API (no hardcoding), no silent default
  - Separate **Comment / Duet / Stitch** toggles
  - **Commercial-content disclosure**: "Your Brand" vs "Branded Content" toggle with validation
  - Explicit **consent declaration + content preview + confirm step**, plus publish-status tracking
  - Support for the **AIGC / AI-generated-content flag** — directly relevant: your content is AI voiceover + avatar
- **Operator gotcha:** the audit reviews a **UI**, so n8n is not what TikTok wants to see. You either build a thin compliant "operator console" UI to screen-record/submit, or use an already-audited third-party provider. ([App Review Guidelines](https://developers.tiktok.com/doc/app-review-guidelines), [App Review FAQ](https://developers.tiktok.com/doc/getting-started-faq))

### Sandbox vs production
- **Sandbox** lets you build/test Direct Post **without** review: up to **5 sandboxes/app**, each shareable with **up to 10 accounts**. Validate the full init→status→publish pipeline pre-audit. ([Introducing Sandbox](https://developers.tiktok.com/blog/introducing-sandbox), [Add a Sandbox](https://developers.tiktok.com/doc/add-a-sandbox/))
- **Production (post-audit)** required to post publicly to real accounts at scale.

### Rate limits that bite at 50+ accounts (three stack)
1. **Per-creator daily post cap:** ~**15 posts/day/account**, **shared across all API clients** posting to that account. Error: `spam_risk_too_many_posts`. Your 3 variants/account/day is comfortably under. ([Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines))
2. **Per-client "active publishing users in 24h" quota** — THE multi-account ceiling. Unaudited = 5/24h; audited = a cap set from your **audit-form usage estimate**, raisable on request. Exceeded → `reached_active_user_cap`. **Action: declare your real fleet size (50+ each posting daily) in the audit application.** ([Rate Limit doc](https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit), [Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines))
3. **Endpoint rate limit:** ~**6 requests/minute per user access token** on `init` — irrelevant at your cadence but throttle bulk runs. ([Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post))

> Scaling note: limits aggregate per app/client. At **~100+ accounts** consider partitioning across **2–4 registered API clients**; below ~50 one client is fine. ([zernio](https://zernio.com/blog/tiktok-posting-api))

### The multi-account / OAuth model
- **One app, many tokens.** Each creator goes through **Login Kit** OAuth once, granting `video.publish` (+ `user.info.basic`); you store that account's access + refresh token and post on its behalf. Adding the 1st or the 50th account is the identical flow. **No published cap on authorized accounts per app.** There is **no agency/admin token** spanning multiple accounts — each needs its own token + ongoing refresh management (tokens expire).
- **Multi-country concern (not solved by the API alone):** see the dedicated Anti-ban section. The OAuth consent step is a browser login and should happen from the account's country IP; token refresh + posting are not treated as logins and can run server-side.

Sources: [Content Posting API product](https://developers.tiktok.com/products/content-posting-api/), [Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started), [Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [Upload/Inbox reference](https://developers.tiktok.com/doc/content-posting-api-reference-upload-video), [Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines), [Rate Limits](https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit), [Introducing Sandbox](https://developers.tiktok.com/blog/introducing-sandbox), [Add a Sandbox](https://developers.tiktok.com/doc/add-a-sandbox/), [App Review Guidelines](https://developers.tiktok.com/doc/app-review-guidelines), [postiz #1362](https://github.com/gitroomhq/postiz-app/issues/1362), [PostPeer](https://www.postpeer.dev/blog/best-tiktok-posting-api).

---

## Facebook / Meta official posting (2026)

### Bottom line
Meta's Graph API **can** automate posting video and Reels to Facebook **Pages**, and one app can manage 10–50+ Pages — but it **cannot post to personal profiles at all**, and the hardest gate is **App Review + Business Verification** for posting to Pages you don't own. The Graph API *is* the sanctioned channel, so risk shifts to account/Page integrity policy, not "automation detection."

### 1. Pages only — personal profiles are NOT allowed (hard, settled)
- `publish_actions` was deprecated in Graph API v3.0 (2018); publishing to personal profiles is unsupported. ([postproxy.dev](https://postproxy.dev/blog/facebook-graph-api-posting-guide/))
- `/user/posts` reference: Creating is **"not supported"** (read-only). ([User Posts ref](https://developers.facebook.com/docs/graph-api/reference/user/posts/))
- Video API: **"The Video API allows you to publish Videos and Reels on Facebook Pages."** Reels doc: **"You can only publish Reels to Facebook Pages."** ([Publishing](https://developers.facebook.com/docs/video-api/guides/publishing/), [Reels Publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/))

**Implication:** every Facebook target must be a **Page**. If current "Facebook accounts" are personal profiles, each needs a Page created + administered before any API automation. Architect around Pages.

### 2. Posting endpoints
Current Graph API version is **v25.0** (Feb 2026); ~2 versions/year, ~2 years support each. ([v25.0 changelog](https://developers.facebook.com/docs/graph-api/changelog/version25.0/))

| Content | Endpoint | Host |
|---|---|---|
| Text / link | `POST /{page-id}/feed` | graph.facebook.com |
| Photo | `POST /{page-id}/photos` | graph.facebook.com |
| Video (feed) | Resumable Upload: `POST /{app-id}/uploads` → `POST /upload:{session-id}` → `POST /{page-id}/videos` | graph.facebook.com (**`graph-video.facebook.com` is deprecated — use `graph.facebook.com`**) ([Page Videos ref](https://developers.facebook.com/docs/graph-api/reference/page/videos/)) |
| **Reels** | 3-phase: (1) `POST /{page-id}/video_reels` `upload_phase=start` → (2) upload to `rupload.facebook.com/video-upload/{video-id}` → (3) `POST /{page-id}/video_reels` `upload_phase=finish&video_state=PUBLISHED` | graph.facebook.com + rupload.facebook.com |

Reel status: `GET /{video-id}?fields=status`. You can pass a hosted `file_url` (your Drive direct-download link) instead of uploading bytes. ([Reels Publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/))

**Reels specs (match your pipeline output):** .mp4, 9:16, 1080×1920 recommended (min 540×960), **24–60 fps**, **duration 3–90s** (max 60s if also a story), H.264/H.265/VP9/AV1, audio ≥128 kbps 48 kHz stereo. Your "force 60fps" change sits inside the 24–60 fps window. The AI Romanian description maps directly to the `description`/`message` field. ([Reels Publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/))

### 3. Required permissions
All Page publishing needs three permissions via Facebook Login: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`. Reels additionally needs a **Page access token from a user with the `CREATE_CONTENT` task** on the Page. Every permission beyond `public_profile`/`email` requires **App Review**. ([Reels Publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/), [Permissions Reference](https://developers.facebook.com/docs/permissions/))

### 4. App Review + Business Verification — the real gate
- **App Review is mandatory** for `pages_manage_posts` + the two `pages_*` companions (you're accessing assets you don't own). Needs a use-case description, screencast, privacy policy, test credentials; turnaround a few days to a few weeks. ([Permissions](https://developers.facebook.com/docs/permissions/), [postproxy.dev](https://postproxy.dev/blog/facebook-graph-api-posting-guide/))
- **Business Verification is required for Advanced Access** — i.e., to publish on behalf of Pages you don't own/manage (exactly the 10–50+ scenario). Standard Access only covers Pages whose admins have a role on your app (fine for testing). ([Permissions](https://developers.facebook.com/docs/permissions/), [Access levels](https://developers.facebook.com/docs/graph-api/overview/access-levels/))
- **Business Verification practicalities (2026):** legal business address, a **non-VoIP** verifiable phone, a verified domain + **domain email (not gmail/yahoo)**, ≥2 documents. Typically **2–5 business days**. ([agrowth.io 2026](https://agrowth.io/blogs/facebook-ads/how-to-verify-your-business-on-meta), [ayrshare.com](https://www.ayrshare.com/facebook-reels-api-how-to-post-fb-reels-using-a-social-media-api/))

> **Caveat/flag:** the scope-by-scope "these three `pages_*` ⇒ Advanced Access ⇒ Business Verification" mapping is inferred from Meta's general Advanced-Access rule + practitioner guides rather than one scope-level Meta statement. Treat as near-certain; verify in the App Dashboard at submission.

### 5. Can one app manage 10–50+ Pages? Yes.
- **Page access tokens are unique per (Page, admin, app).** `/me/accounts` returns Pages + each Page's token. **No documented hard cap.** ([Access Token Guide](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/))
- For hands-off server automation, use a **Business Manager + System User access token**: System Users authenticate server software against BM-owned assets, support `pages_manage_posts` etc.; rotate tokens periodically. The System User and token owner must be in the same BM. ([System Users](https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens/))
- Fits n8n cleanly: store one long-lived Page (or System User) token per Page; each run picks the right token + Page ID and POSTs variant + description.

### 6. Rate limits — not a real constraint except the Reels cap
- **Reels (binding):** **30 API-published posts per Page per rolling 24h.** With 3 variants/source, ~10 source videos/Page/day via Reels. ([Reels Publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/), [ayrshare.com](https://www.ayrshare.com/facebook-reels-api-how-to-post-fb-reels-using-a-social-media-api/))
- **Pages BUC:** `Calls/24h = 4800 × engaged users`, tracked per Page via the `X-Business-Use-Case` header (`call_count`, `total_cputime`, `estimated_time_to_regain_access`). Limits accrue **per Page independently** — 50 Pages don't pool. **Failed calls also consume quota**; Meta tightened some hourly quotas in 2025, so build for tight rolling budgets. ([Rate Limiting](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/))
- **App-level:** `Calls/hour = 200 × users` for app-token calls — but publishing uses Page/System-User tokens governed by the per-Page BUC formula, not this pool. ([Rate Limiting](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/))
- **Plain video (non-Reels):** single-request up to ~1 GB / 20 min; resumable up to ~1.5 GB / 45 min; no documented daily-count cap like Reels. ([postproxy.dev](https://postproxy.dev/blog/facebook-graph-api-posting-guide/))

Net: the only number to engineer around is **30 Reels / Page / 24h**.

### 7. Ban-safety / longevity for this setup
- Graph API **is** the ban-safe channel — Meta's sanctioned method, so you avoid the reverse-engineered/headless-bot risk class entirely. Risk migrates to **content/account integrity** and **BM/Page health**.
- **IP/VPN:** Graph calls are server-to-server, token-authenticated, so the *posting* call's egress IP is not the primary signal. The higher-risk events are **account login/setup, token generation, BM actions** — those benefit from consistent geo (native RO for RO Pages; that country's proxy for foreign Pages) and consistent device/identity per BM. Avoid one BM straddling many countries with mismatched IPs.
- **Facebook checkpoint cascade:** Page tokens derive from a *user* login. If that user hits a logged-in checkpoint, **every Page token tied to it dies at once**. Keep the admin/system-user account behind each Page cluster healthy and logged in from a sane IP. ([SmarterQueue checkpoint error](https://help.smarterqueue.com/article/425-facebook-error-error-validating-access-token-the-user-is-enrolled-in-a-blocking-logged-in-checkpoint))
- **Concentration risk:** 50+ Pages under one app/BM means one strike or a verification rejection can cascade. **Spread across multiple verified Business Managers** to reduce blast radius (inference from BM-level enforcement, not a single-source Meta statement).

Key sources: [Reels Publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/), [Publishing](https://developers.facebook.com/docs/video-api/guides/publishing/), [User Posts ref](https://developers.facebook.com/docs/graph-api/reference/user/posts/), [Permissions](https://developers.facebook.com/docs/permissions/), [Access levels](https://developers.facebook.com/docs/graph-api/overview/access-levels/), [Rate Limiting](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/), [System Users](https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens/), [Access Token Guide](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/), [v25.0 changelog](https://developers.facebook.com/docs/graph-api/changelog/version25.0/), [postproxy.dev](https://postproxy.dev/blog/facebook-graph-api-posting-guide/), [ayrshare.com](https://www.ayrshare.com/facebook-reels-api-how-to-post-fb-reels-using-a-social-media-api/), [agrowth.io](https://agrowth.io/blogs/facebook-ads/how-to-verify-your-business-on-meta).

> **Staleness flags:** Page-level rate-limit mechanics partly cite a 2016 Meta blog, but the current rate-limiting doc's engaged-user formula supersedes it. The Advanced-Access⇒Business-Verification mapping for these `pages_*` scopes is inferred — verify at submission.

---

## Posting engines compared (2026)

### The decisive constraint that reshapes everything
**TikTok's official Content Posting API forces every post from an *unaudited* app to `SELF_ONLY` (private) and caps it at 5 users/24h.** To post publicly at scale you need an app that has **passed TikTok's audit** (~1–4 weeks, multiple rounds). Sources: [Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started), [Postiz TikTok docs](https://docs.postiz.com/providers/tiktok), [PostPeer](https://www.postpeer.dev/blog/best-tiktok-posting-api).

This splits the field:
- **SaaS engines (Blotato, Upload-Post, Ayrshare)** hold their *own* audited TikTok app → you inherit public posting on day one, no audit, no developer app. ([Blotato TikTok pricing](https://www.blotato.com/blog/tiktok-api-pricing): "removes the audit step from the critical path.")
- **Self-hosted engines (Postiz, Mixpost)** make *you* register and **get your own TikTok app audited**. Until then you're stuck private-only / 5 users. Mixpost gates the audit-request workflow behind its **Enterprise** license. ([Postiz TikTok docs](https://docs.postiz.com/providers/tiktok), [Mixpost Direct Post Audit](https://docs.mixpost.app/services/social/tik-tok/direct-post-audit/))

Same on Meta: the Graph API requires *your* app to pass App Review for `pages_manage_posts`; SaaS abstracts this, self-host means you own the review. **All four tools below use OFFICIAL APIs** — none use browser automation or reverse-engineered endpoints, so none are disqualified on the method constraint. "Real device" services (e.g., TokPortal-style) are a different, excluded category.

### The multi-country / VPN caveat (applies to all official-API tools)
With the official API, **every upload leaves from the engine's server IP** (or whatever proxy routes the HTTPS call), not a per-account mobile device.
- **SaaS tools post from THEIR cloud IP** — no per-account country knob. Usually fine for RO-native; the origin won't match a foreign account's country.
- **Self-hosted tools give you ONE controllable egress IP per instance.** Neither Postiz nor Mixpost documents per-*account* proxy routing. To route different accounts through different country IPs you'd run **separate self-hosted instances per country**, each behind that country's VPN/proxy.

### Side-by-side

| | **Blotato** | **Postiz** | **Mixpost** | **Upload-Post** |
|---|---|---|---|---|
| **Official API? Audit owner** | ✅ own audited apps | ✅ your own app + audit | ✅ your own app + audit | ✅ own audited apps |
| **Pricing 2026** | Starter $29 / Creator $97 / Agency $499 mo | OSS free self-host; cloud ~$29/$39/$99 | One-time: Lite free / **Pro $299** / Ent $1,199 | Free 10 uploads; paid ~$16–18/mo |
| **Max accounts** | 20 (Starter) / 40 (Creator & Agency) | **Unlimited** (self-host) | **Unlimited** (Pro+) | Per-profile; scale by buying profiles |
| **TikTok account cap** | **Starter 3 unique TikTok/24h** (×10 posts); **Creator/Agency lift it** | your app's limits (6 req/min/user) | your audited app's limits | per platform API limits |
| **Sheets/Drive ingestion** | ✅ native n8n templates | via n8n nodes | via n8n + Mixpost API | via n8n + Upload-Post node |
| **n8n integration** | ✅ official node + MCP; richest templates | ✅ official node (`n8n-nodes-postiz`) | REST API/webhooks (HTTP node) | ✅ official node, bundled by default |
| **Self-host vs SaaS** | SaaS only | **Self-host (AGPL) or cloud** | **Self-host one-time** | SaaS only |

Sources: [Blotato pricing](https://www.blotato.com/pricing) · [Postiz pricing](https://postiz.com/pricing) / [public API](https://docs.postiz.com/public-api) · [Mixpost pricing](https://mixpost.app/pricing) / [GitHub](https://github.com/inovector/mixpost) · [Upload-Post n8n node](https://github.com/Upload-Post/n8n-nodes-upload-post) · [Blotato FAQ](https://help.blotato.com/platforms/tiktok/faqs) · n8n templates [4227](https://n8n.io/workflows/4227-multi-platform-video-publishing-from-google-sheets-to-9-social-networks-via-blotato-api/), [8524](https://n8n.io/workflows/8524-automated-daily-posting-to-9-social-platforms-with-google-sheets-drive-and-blotato/).

### Reading the field for THIS operator
- **Blotato** — lowest friction, ships the exact "Sheet caption + Drive MP4 → 9 platforms" workflow, skips both audits. BUT even **$499 Agency tops out at ~40 accounts** and posts from its own IP. For 50+ TikTok *and* 50+ Facebook (100+ total) you'd exceed a single plan and can't control foreign geo. **Strong for the RO-native slice, weak for foreign + raw count.** (Verification confirms Starter's 3-TikTok/24h cap and that Creator $97 already lifts the *per-day* cap; Agency needed for the total-account ceiling.)
- **Mixpost** — scale/cost winner on paper: **$299 once, unlimited accounts**, self-hosted (control egress IP per instance). Real cost = the **TikTok audit + Meta App Review you pass yourself**, and the docs gate the TikTok audit workflow behind the **$1,199 Enterprise** license.
- **Postiz** — best *self-host + n8n-native* option: AGPL, unlimited channels, official n8n node, ~30k GitHub stars, active. Same own-audit requirement but **no Enterprise paywall** on the audit, and the same per-instance IP control.
- **Upload-Post** — credible cheaper SaaS alternative to Blotato: official API, n8n node bundled by default, free tier (10 uploads), paid from ~$16/mo. Scales by buying "profiles"; same no-per-country-IP limitation as any SaaS. Confirm high-profile-count pricing directly (published tiers top out low).

### Recommendation
- **Go live now on the Romania-native slice with a SaaS aggregator** (Blotato Creator/Agency, or Upload-Post) — fastest ban-safe, zero-audit path; posts from the vendor's IP, which is fine for RO accounts.
- **Graduate to self-hosted Postiz for scale + foreign accounts:** run **one Postiz instance per target country, each behind that country's VPN/proxy** so the official-API egress IP matches each account's region — the only ban-safe way to honor the multi-country requirement with official APIs. Budget 2–6 weeks to pass the TikTok audit + Meta App Review for your own app(s). **Skip Mixpost** unless you specifically want its one-time pricing *and* will buy Enterprise for the TikTok audit.

> Re-confirm before committing budget: Blotato's current per-plan TikTok account cap + total-account ceiling, and whether Postiz/Mixpost have added native per-account proxy routing (neither documents it as of June 2026).

---

## Anti-ban + multi-country (2026)

**Two layers answer "does IP matter" differently:**
1. **API transport layer** (OAuth token → HTTPS call → endpoint). Per primary docs, no IP/region/proxy requirement is imposed on the posting call itself. ([TikTok Direct Post ref](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [Meta Page Videos ref](https://developers.facebook.com/docs/graph-api/reference/page/videos/))
2. **Account-integrity / risk-engine layer** — mostly fed by account creation + human-login events, but it never fully stops watching. This is where geo and account-linking bite.

> ⚠️ **Verification correction (important):** the upstream "post-IP is irrelevant and not a ban trigger" framing is **overstated and partly refuted**. Primary docs are *silent* on request IP, so "IP doesn't matter" is an inference, not a documented fact. Distinguish (a) the account's **home region** — fixed at creation, effectively immutable, *not* changed by a foreign API call — from (b) per-video **initial distribution** and **account-linking risk**. Operator/algorithm sources consistently treat region-mismatched and **shared/datacenter IPs as account-linking and trust-score triggers, even through the official API**. So your per-account proxy/VPN plan **at creation/login** is correct and necessary; the unsafe move is firing all 50+ accounts from one shared/datacenter server IP. Most "you need a per-account residential proxy for every post" advice comes from antidetect-browser/proxy vendors describing **browser bots** and is largely mis-applied to official-API posting — but the "don't share one datacenter IP across the farm" warning still holds.

### (a) Where region is actually set
- TikTok derives home region from creation-time signals (signup IP, SIM/carrier MCC-MNC, device region/language, GPS) and **permanently logs the creation IP**; region is **sticky and hard to change** afterward (in-app "Switch Region" is a weak signal; real change needs ~90+ days of genuine relocation with a local SIM). ([vpntous region detection 2026](https://vpntous.com/blog/tiktok-region-detection-in-2026-ip-sim-card-and-language), [TikTok: region set at registration time/place](https://www.tiktok.com/discover/how-to-change-region-initially-set-based-on-the-time-and-place-of-registration), [megadigital](https://megadigital.ai/en/blog/how-to-change-region-on-tiktok/))
- **The geo battle is won at creation + human login, not in n8n.** A foreign-targeted account must be *born* foreign (created on a device using that country's IP, ideally SIM, device language, timezone). Posting the finished MP4 from your RO server via the API does not "move" the account; nor can a French proxy on the API call fix a Romanian-born account.

### (b) Ban-safe geo mapping (at creation/login, not in the API path)
- **Per-account, per-country at birth:** create each foreign account on a device with a **residential/mobile** IP of that country (match SIM/eSIM/language/timezone where possible). ([buytiktokaccount 2026](https://buytiktokaccount.com/ip-location-safety-for-tiktok-a-2026-guide-for-creators/))
- **Datacenter IPs are the real killer** — flagged on sight; residential/mobile pass. Never touch an account from a browser over a datacenter IP. ([roundproxies](https://roundproxies.com/blog/multiple-tiktok-accounts/), [todetect](https://www.todetect.net/article/multiple-account-management/tiktok-device/))
- **OAuth token interaction:** the OAuth consent step is a browser login → do it from the account's country IP + normal device, **not** your RO server. After you hold access+refresh tokens, store them server-side and **refresh/post freely** — refresh and posting are not logins. ([TikTok token management](https://developers.tiktok.com/doc/oauth-user-access-token-management), [manage tokens v2](https://developers.tiktok.com/doc/login-kit-manage-user-access-tokens/))
- **Facebook caveat:** if the *user* behind a Page cluster hits a checkpoint, every Page token tied to it dies at once — keep that admin account healthy and logged in from a sane IP. ([SmarterQueue checkpoint](https://help.smarterqueue.com/article/425-facebook-error-error-validating-access-token-the-user-is-enrolled-in-a-blocking-logged-in-checkpoint))

### (c) Realistic best practice for a RO operator running BOTH cohorts from one machine + n8n
Split into **identity plane** vs **publish plane**:
- **Identity plane (creation + OAuth login + manual fixes):** geo-correct, isolated *per country*, on phones/cloud-phones/per-country profiles — NOT on the n8n box. RO accounts: native RO residential/mobile IP (your home connection is an asset — no VPN). Foreign accounts: that country's residential/mobile IP; match SIM/language/timezone.
- **Publish plane (n8n + RO server):** once tokens exist, n8n can post all accounts — but per the verification correction, prefer giving each account a **sticky, country-appropriate egress** rather than blasting the whole farm through one shared datacenter IP. The honest answer to "do I need 50 proxies running constantly": **not for the posting transport per se**, but you do need (1) geo-correct creation/login per account and (2) to avoid a single shared/datacenter IP fingerprint across the fleet. Self-hosting one instance per country (each behind that country's residential/mobile proxy) is the clean way to satisfy both.

### (d) Antidetect browsers / per-account proxies — browser concern or API concern?
**Overwhelmingly a browser-automation concern.** Antidetect browsers (Multilogin, AdsPower, GoLogin) spoof fingerprints + isolate cookies + bind a proxy per profile so *manual/headless logins* across many accounts aren't fingerprint-linked. ([scrapingbee](https://www.scrapingbee.com/blog/anti-detect-browser/), [proxyway](https://proxyway.com/best/antidetect-browsers))
- **Official API posting needs none of this** — no browser, no cookie jar, no canvas fingerprint in a bearer-token POST. (The authenticated-Reddit-API analogy: with the OAuth API you authenticate as a user and rate limits are per-token, not per-IP.) ([BlackHatWorld](https://www.blackhatworld.com/seo/does-reddit-api-also-require-proxy-ip-management.1729035/))
- **Where isolation still matters:** the browser moments — account creation, the OAuth consent click, password resets, appeals, Creator/Business account setup, Meta Business Manager admin. Do those per-account in a clean, geo-correct context; an antidetect browser + per-account proxy is the standard tooling **for that step only**.
- **Compliance:** Meta's terms forbid *unauthorized* automation but explicitly carve out their **official APIs as the authorized path**; TikTok's developer guidelines mirror this. Staying on the API is itself your strongest ban-safety move. ([Meta account integrity](https://transparency.meta.com/policies/community-standards/account-integrity/), [TikTok developer guidelines](https://developers.tiktok.com/doc/our-guidelines-developer-guidelines))

### (e) Warm-up, cadence, content-fingerprint variation
**Hard API limits (primary):** TikTok — 6 req/min/user token, undisclosed per-user daily cap (`spam_risk_too_many_posts`), per-client active-user cap (`reached_active_user_cap`); unaudited = SELF_ONLY + 5 users/24h. ([TikTok Direct Post ref](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines)) Meta — 30 Reels/Page/24h; per-Page BUC budgets; **failed calls consume quota**. ([Meta rate-limiting](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/))

**Warm-up + cadence (vendor/community consensus — guidance, not platform-published, treat as ranges):**
- New-account warm-up **7–14 days** before aggressive automation; start **1 post/day**, ramp to **2–3/day** after ~day 8–10, **3–5/day for mature accounts only**, spaced **3–4h apart**. Do not jump a brand-new account into multi-post/day automation. ([JoinBrands](https://joinbrands.com/blog/how-often-to-post-on-tiktok/), [TokPortal](https://www.tokportal.com/learn/tiktok-posting-too-fast-restriction), [Followr](https://followr.ai/blog/how-to-warm-up-your-social-media-account))
- **Stagger across the fleet** — don't fire all 50 accounts at the same minute; randomize/jitter times. Identical timing is a coordination signal.

**Content fingerprinting — your 3-variant setup is an asset, with a caveat:**
- 3 role-variants (different AI voice + avatar + captions) differ in audio + visual overlay, defeating naive hash-dedup. Good.
- Trap: posting **the same variant to many accounts** still creates a duplicate cluster. **Map distinct variants to distinct accounts**, add cheap per-post entropy (re-encode so hashes differ, micro-vary crop/zoom/speed ±1–3%, vary first-frame/cover), and **vary the AI Romanian description per account** rather than reusing one caption verbatim. Identical captions reused across many accounts is one of the easiest spam-cluster signals. ([conbersa](https://www.conbersa.ai/learn/multi-account-tiktok-without-getting-banned))

— Confidence: API-layer facts (limits, audit gating, token model) = high (primary docs). Region-stickiness / creation-IP logging = medium-high (multi-source incl. TikTok help). Warm-up/cadence numbers = medium (vendor/community).

---

## Recommended architecture (2026)

### TL;DR
Keep n8n local as the **orchestrator**, but **do not** make n8n talk to TikTok/Meta directly. Put a **posting aggregator that already holds an audited TikTok Direct-Post app** (Upload-Post → Ayrshare at scale; Blotato for RO) between n8n and the platforms. That removes the biggest blocker (the TikTok audit), gives one uniform multi-account API for both platforms, and keeps you 100% on official OAuth. n8n's job shrinks to: watch the sheet → resolve the account map → fan out one API call per (variant × account). Multi-country is solved at OAuth-connect time, not at post time.

### Why an aggregator, not "n8n → TikTok API directly"
The single biggest blocker is the **TikTok Direct Post audit** (see TikTok section). Workarounds, in order:
1. **Use an aggregator's already-audited app** (recommended) — Upload-Post / Ayrshare / Blotato integrate via the official audited TikTok + Meta APIs, so you inherit their audit. ([upload-post.com](https://www.upload-post.com/), [Blotato](https://www.blotato.com/blog/tiktok-api-pricing))
2. **Draft mode on your own unaudited app** (fallback) — post lands in drafts, a human taps publish. No audit but not unattended; breaks the goal at 50 accounts.
3. **Pass the audit yourself + phased rollout** (only if you outgrow aggregator economics) — build the compliant UX, get `video.publish`, partition accounts across multiple Client Keys.

Facebook is easier and could be done direct from n8n, but routing it through the same aggregator keeps one code path. Direct-on-FB recap: 3-step `POST /{page_id}/video_reels` (start → `rupload.facebook.com` → finish `video_state=PUBLISHED`), with `pages_show_list` + `pages_read_engagement` + `pages_manage_posts`, hosted `file_url` allowed, 30 Reels/Page/24h, and **Advanced Access + Business Verification** for Pages you don't own. This App Review is the second blocker — another reason to let the aggregator own it.

### End-to-end glue (finished sheet row → posted everywhere)
```
[ClipForge pipeline] → MP4 in Google Drive  +  RO description written to Google Sheet row
        │
        ▼
[n8n LOCAL]  ── the orchestrator (NOT the poster) ──
  1. Google Sheets Trigger/poll: rows where status="ready" AND posted!=true
  2. For each role variant (narator / comentator / povestitor):
       - read Drive fileId for that variant's MP4
       - read the AI Romanian description (the caption)
       - look up the ACCOUNT MAP for this role → list of {platform, accountRef, country}
  3. Make the file fetchable:
       - Drive node → direct-download URL (uc?export=download&id=…) OR download bytes & re-host
  4. SplitInBatches loop: one HTTP Request per (variant × account)
       → AGGREGATOR API: { profileKey, video_url, caption, platforms:[tiktok|facebook] }
  5. Write per-account result (post URL/id/error) back to the Sheet row → mark posted=true
  6. Throttle with Wait nodes: respect caps + ban-safety pacing (stagger/jitter)
```

**Component split:**
- **n8n nodes:** Sheets trigger, Drive fetch, account-map lookup (second tab or small JSON/SQLite), fan-out loop, HTTP Request to aggregator, write-back, scheduling/retries. n8n has **no working first-party TikTok node**; use the generic HTTP Request node or a vendor's node (Upload-Post's official node, Blotato's node). ([n8n community](https://community.n8n.io/t/new-n8n-node-simplified-social-media-posting-is-live/153759), [Upload-Post n8n node](https://github.com/Upload-Post/n8n-nodes-upload-post), [Blotato template](https://n8n.io/workflows/7187-automate-content-publishing-to-tiktok-youtube-instagram-facebook-via-blotato/))
- **Aggregator:** holds the audited TikTok app + Meta app, stores each account's OAuth refresh token as a "profile," normalizes video to specs, executes publish, handles retries/token refresh. ([Ayrshare business plan](https://www.ayrshare.com/docs/multiple-users/business-plan-overview))
- **Custom code (small):** only the account-map resolver (role+country → profile keys) and the Drive→fetchable-URL helper. Don't rebuild OAuth.

### The account map (the heart of the system)
One sheet tab, one row per destination account:

| role | platform | country | aggregator_profile_key | needs_vpn |
|---|---|---|---|---|
| narator | tiktok | DE | prof_ttk_de_01 | yes |
| narator | facebook | RO | prof_fb_ro_07 | no |

n8n joins the finished row's `role` against this table to get the fan-out list. Country lives here; the difference between RO and foreign is handled once at connect time, not per post.

### Scaling math & cost (10–50+ each → up to ~100 profiles for 50 TikTok + 50 FB)
- **TikTok:** 6/min is per-user-token (scales linearly), but per-app aggregate daily ceilings mean partitioning across multiple Client Keys at ~100+. An aggregator abstracts this. ([zernio](https://zernio.com/blog/tiktok-posting-api), [TikTok rate limits](https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit))
- **Facebook:** 30 Reels/Page/24h — comfortably above 3 variants/day. ([Meta Reels](https://developers.facebook.com/docs/video-api/guides/reels-publishing/))
- **Aggregator pricing** (1 "profile" = 1 connected account):
  - **Ayrshare Business:** ~$599/mo for 30 profiles, then ~$8.99/extra → ~100 profiles ≈ **~$1,230/mo**. Purpose-built for this multi-user API model. ([pricing](https://www.ayrshare.com/pricing/), [business plan](https://www.ayrshare.com/docs/multiple-users/business-plan-overview))
  - **Upload-Post:** much cheaper, official n8n node, free tier (10/mo), paid from ~$16/mo for 5 profiles — **verify the 100-profile tier directly** (published tiers top out lower). ([upload-post.com](https://www.upload-post.com/), [n8n node](https://github.com/Upload-Post/n8n-nodes-upload-post))
  - **Blotato:** Agency $499/mo, ~40-account ceiling — short for 100 total. ([Blotato pricing](https://www.blotato.com/blog/tiktok-api-pricing))
  - **Self-host:** Mixpost/Postiz — cheaper in license, expensive in your time/risk (you pass the audits). ([Mixpost direct-post-audit](https://docs.mixpost.app/services/social/tik-tok/direct-post-audit/))

### Phased rollout (de-risk the blockers)
1. **Week 0–1 (pilot):** n8n → Upload-Post free/Basic; connect 2 RO + 2 foreign accounts per platform; prove sheet→post→write-back end-to-end.
2. **Week 1–2:** add the account-map tab, country-aware OAuth-connect procedure (VPN for foreign), throttling/Wait nodes, error write-back.
3. **Week 2–4:** scale connected profiles to target; monitor caps + account flags; only then evaluate bringing the TikTok audit + Meta App Review in-house for cost (graduate to Postiz per-country instances).

Sources: [TikTok get-started](https://developers.tiktok.com/doc/content-posting-api-get-started), [TikTok rate limits](https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit), [Meta Reels publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/), [Meta access levels](https://developers.facebook.com/docs/graph-api/overview/access-levels/), [Meta permissions](https://developers.facebook.com/docs/permissions/), [Ayrshare business plan](https://www.ayrshare.com/docs/multiple-users/business-plan-overview), [Ayrshare pricing](https://www.ayrshare.com/pricing/), [Upload-Post](https://www.upload-post.com/), [Upload-Post n8n node](https://github.com/Upload-Post/n8n-nodes-upload-post), [Blotato TikTok pricing](https://www.blotato.com/blog/tiktok-api-pricing), [Mixpost direct-post audit](https://docs.mixpost.app/services/social/tik-tok/direct-post-audit/), [PostPeer](https://www.postpeer.dev/blog/best-tiktok-posting-api), [Postproxy TikTok guide](https://postproxy.dev/blog/how-to-post-to-tiktok-via-api/), [n8n community node](https://community.n8n.io/t/new-n8n-node-simplified-social-media-posting-is-live/153759), [roundproxies](https://roundproxies.com/blog/multiple-tiktok-accounts/).

---

## Verification

Adversarially fact-checked claims. **Where a verdict corrects a research section, the verification wins** (corrections are folded inline above).

| # | Claim | Verdict | Corrected fact (short) | Key sources |
|---|---|---|---|---|
| 1 | One TikTok app can Direct-Post (not just draft) to 10–50+ distinct accounts without each needing its own approved app | **Confirmed** | True architecturally — per-USER OAuth, audit is once at app level, no hard cap on linked accounts. **Precondition:** must pass the one-time app audit; binding limits are then the 24h active-creator cap (set from your audit estimate) + ~15 posts/account/day. Each account still needs its own token + refresh management; no agency token. | [Content Sharing](https://developers.tiktok.com/doc/content-sharing-guidelines), [Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started), [Rate limits](https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit) |
| 2 | TikTok Direct Post still requires passing an app audit before production (unaudited = drafts only) | **Partly** | Audit requirement **confirmed**. But "unaudited = drafts" is **wrong**: unaudited Direct Post posts go **live but forced SELF_ONLY (private)**, not drafts. Drafts are the separate `video.upload`/inbox flow, which needs no audit and *can* go public — but requires a manual in-app tap per post. | [Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started), [Direct Post ref](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [Content Sharing](https://developers.tiktok.com/doc/content-sharing-guidelines) |
| 3 | Posting via official APIs: source IP/geo doesn't determine audience region and is not itself a ban trigger | **Partly (overstated/unsafe)** | Account **home region** is fixed at creation, not changed by a foreign API call — TRUE. But "IP doesn't matter / not a ban trigger" is **overstated**: primary docs are silent (so it's inference); upload IP still influences per-video distribution, and **region-mismatched + shared/datacenter IPs are documented account-linking/trust triggers even via the official API.** Keep the per-account, country-correct, residential/mobile IP plan; avoid one shared datacenter IP across the farm. | [Direct Post ref](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [TikTok Dev ToS](https://www.tiktok.com/legal/page/global/tik-tok-developer-terms-of-service/en), [region help](https://www.tiktok.com/discover/how-to-change-region-initially-set-based-on-the-time-and-place-of-registration), [tokportal](https://www.tokportal.com/learn/post-to-tiktok-via-api) |
| 4 | Blotato's Starter tier limits TikTok to ~3 accounts/24h, unsuitable for 10–50+ | **Confirmed** | Starter ($29) = 3 unique TikTok/24h × 10 posts (= 900/mo). The cap is **removed at Creator ($97)**, not only at a high tier. But the **total connected-account ceiling** (~40 at Creator/Agency) still forces **Agency $499** + possibly multiple workspaces for 20–100+ accounts. | [Blotato social-accounts](https://help.blotato.com/settings/social-accounts), [Blotato TikTok FAQ](https://help.blotato.com/platforms/tiktok/faqs), [pricing](https://www.blotato.com/pricing) |

---

## Decisions the operator must make

1. **Aggregator vs self-host (the fork everything hangs on):** start on a SaaS aggregator (inherits audits, live in days) or commit to self-hosting Postiz and passing the TikTok audit + Meta App Review yourself (weeks of work, ~zero marginal cost, full geo control). Recommended: aggregator now, self-host later for scale/foreign.
2. **Which aggregator:** Upload-Post (cheapest, verify 100-profile pricing) vs Ayrshare Business (~$1,230/mo at 100 profiles, purpose-built) vs Blotato (RO-only, ~40-account ceiling).
3. **Facebook account architecture:** confirm whether your "Facebook accounts" are personal profiles (must convert to **Pages** — API cannot post to profiles) and whether foreign Pages are **owned by you** (Standard Access) or others (**Advanced Access + Business Verification**).
4. **Business Verification readiness:** can you supply a legal business address, non-VoIP phone, verified domain + domain email, and ≥2 documents? Required for Meta Advanced Access (and de facto for scale).
5. **Per-country isolation model:** how many target countries, and will you run one self-host instance (or proxy egress) per country? This determines proxy/VPS spend and whether SaaS (single cloud IP) is acceptable for any cohort.
6. **Caption uniqueness:** generate the AI Romanian description **per account** (or add per-post entropy), not one verbatim caption reused across the fleet — to avoid spam-cluster signals.
7. **Account-creation pipeline:** who/what creates and OAuth-authorizes accounts from country-correct residential/mobile IPs (the only step that genuinely needs antidetect browser + per-account proxy).

## What I could NOT verify / uncertain

- **Upload-Post pricing at ~100 profiles** — published tiers top out around 5–25 profiles; the 100-profile cost needs a direct quote.
- **TikTok's undisclosed daily per-app/per-creator posting ceilings** — exact numbers aren't published and change without notice; confirm with TikTok/aggregator at build time.
- **The exact "`pages_manage_posts` ⇒ Advanced Access ⇒ Business Verification" mapping** — inferred from Meta's general Advanced-Access rule + practitioner guides, not one scope-level Meta statement; verify in the App Dashboard at submission.
- **Whether the Content Posting API request IP feeds per-video geo distribution** — no primary source either way; the safe assumption is to keep IPs country-consistent regardless.
- **Whether Postiz/Mixpost have added native per-account proxy routing** since June 2026 — neither documents it; re-check before committing to a single multi-country instance.
- **Mixpost Enterprise gating of the TikTok audit workflow** — stated in current docs; confirm it hasn't moved to a lower tier.
- **Audit/App-Review turnaround times** (TikTok ~2–4 weeks; Meta days–weeks) — no guaranteed SLA; treat as variable.
- **Warm-up/cadence numbers** (1→3 posts/day over ~2 weeks, 3–4h spacing) — vendor/community consensus, not platform-published; directional only.
