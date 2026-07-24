# Publishing to X and Instagram — setup & first-post runbook

Content Engine publishes to **X (Twitter)** and **Instagram** with **your own**
API credentials — you pay the networks directly, and your keys are stored
encrypted and used only for your posts. This guide gets you from zero to a
verified first post.

> **Reels (video) are Instagram-only.** X publishing is text + up to 4 images.

---

## 1. Get your credentials

### X (Twitter) — needs a paid developer tier

X's API requires a **paid plan** (Basic tier and up) for posting. On the free
tier you can only read.

1. Create an app in the [X Developer Portal](https://developer.x.com/) on a
   **Basic** (or higher) plan.
2. Set the app's **User authentication** to **OAuth 1.0a** with **Read and
   Write** permissions.
3. Generate the four credentials:
   - **API Key** (`X_API_KEY`)
   - **API Secret** (`X_API_SECRET`)
   - **Access Token** (`X_ACCESS_TOKEN`)
   - **Access Token Secret** (`X_ACCESS_TOKEN_SECRET`)
   - Make sure the access token is generated **after** setting Read+Write, or it
     will be read-only.
4. **Long posts (over ~250 chars, uncut):** only work on an **X Premium**
   account. Turn on the **X Premium** toggle in Account → X settings so the app
   sends them uncut; without it, long single posts are trimmed to fit.

### Instagram — business/creator account

1. You need an **Instagram Business or Creator** account and an
   **Instagram-Login access token** with the publishing permissions
   (`instagram_business_content_publish`).
2. Collect:
   - **Access token** (`INSTAGRAM_ACCESS_TOKEN`)
   - **Instagram user id** (`INSTAGRAM_USER_ID`) — the numeric IG user id.
3. **imgbb key** (`IMGBB_API_KEY`) — free from [imgbb.com](https://api.imgbb.com/).
   Instagram fetches images from a public URL, so slides are uploaded to imgbb
   first. (X does not need this — it takes image bytes directly.)
4. **Reels only:** the app must be reachable on a public HTTPS URL
   (`PUBLIC_BASE_URL`, cloud mode) so Instagram can fetch the MP4. Locally, reels
   render and download but publish manually.

---

## 2. Paste them in

- **Cloud (multi-tenant):** Account → the network's **keys** page → paste →
  **Save keys**. Stored encrypted per user.
- **Local / self-host:** put them in `backend/.env`
  (`X_API_KEY=…`, `INSTAGRAM_ACCESS_TOKEN=…`, etc.) — see `compose.env.example`.

Never paste keys into chat or commit them.

## 3. Test the connection (do this first)

On each network's keys page, click **🔌 Test connection**. This makes a
**read-only** call — it never posts — and reports:

- **X** → `✅ Connected as @yourhandle` (via `GET /2/users/me`)
- **Instagram** → `✅ Connected as @yourhandle` (via `GET /{user_id}`)

A red ❌ shows the API's own reason (bad key, wrong scope, expired token). Fix
that **before** trying to publish — it saves you a half-published mess.

## 4. Publish your first post

1. Generate a post (1 slide is enough for a smoke test).
2. Pick the network, review the caption/preview.
3. **Publish now.**
4. Open the returned permalink and confirm it's live on the network.

## 5. Things to know

- **X threads have no rollback.** If tweet 4 of 7 fails, tweets 1–3 are already
  live — the error tells you how many went out and links the first so you can
  finish or delete by hand. Hashtags ride the **last** tweet; the image rides the
  **first**.
- **Accessibility:** a post's alt text is sent to X (image description) and to
  Instagram automatically.
- **Insights:** after publishing, **Refresh insights** pulls reach/likes/etc.
  using your own token.
- **Scheduling** while your PC is off needs the cloud deployment (24/7 backend).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Test connection ❌ `401` (X) | Wrong keys, or token generated before Read+Write was set |
| Test connection ❌ `403` (X) | App not on a paid tier / no write permission |
| Test connection ❌ (Instagram) | Expired token, or token/user-id mismatch |
| Publish works, no image on IG | Missing/invalid `IMGBB_API_KEY` |
| Long X post got trimmed | Owner isn't X Premium — enable the Premium toggle |
| Reel publish 409 | `PUBLIC_BASE_URL` not set (cloud only) |
