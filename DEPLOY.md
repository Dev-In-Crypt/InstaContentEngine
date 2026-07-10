# Cloud Deployment — 24/7 Scheduling

The desktop app (`InstaContentEngine.pyw`) runs locally and is perfect for
**generating and editing** content. But **scheduled posts only publish while
the app is open** — because Instagram's API has no native "publish at time X"
(that's a Facebook Pages feature, not Instagram). To publish on a schedule
**even when your PC is off**, run the same backend in the cloud.

This guide uses **Render** (has a persistent Postgres + always-on web service).
Railway / Fly.io work the same way with the included `Dockerfile`.

## What you need

- This repo on GitHub (already there).
- A free **imgbb** API key — https://api.imgbb.com (slides are uploaded here so
  Instagram can fetch them by public URL).
- An **Instagram Business/Creator** account + a long-lived **Graph API token**
  and your **IG user id** (`INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`).
- An **OpenRouter** API key.

## Steps (Render)

1. Push to GitHub (done).
2. Render dashboard → **New → Blueprint** → select this repo. Render reads
   `render.yaml` and creates a **web service + Postgres**.
3. In the service **Environment** tab fill the `sync:false` vars:
   `OPENROUTER_API_KEY`, `IMGBB_API_KEY`, `INSTAGRAM_ACCESS_TOKEN`,
   `INSTAGRAM_USER_ID`, `UNSPLASH_ACCESS_KEY`, `PEXELS_API_KEY`,
   and an `API_TOKEN` (any long random string — protects your public URL).
4. Deploy. When live, open `https://<your-app>.onrender.com/health` → `{"status":"ok"}`.

`DATABASE_URL` is injected automatically from the Postgres addon. The app
normalizes it to the async driver on startup, and APScheduler stores its jobs
in the same Postgres so scheduled publishes survive restarts.

## Using it

- Open `https://<your-app>.onrender.com/` in a browser (or point the desktop
  window at it — set `APP_URL=https://<your-app>.onrender.com` before launching
  `InstaContentEngine.pyw`).
- Generate a post, hit **📅 Schedule**, pick a time.
- The cloud backend uploads the slides to imgbb and publishes to Instagram at
  the scheduled moment — **your PC can be off**.

## Local vs Cloud — quick reference

| | Local (`.pyw`) | Cloud (Render) |
|---|---|---|
| Generate / edit / export | ✅ | ✅ |
| Publish now | ✅ (needs imgbb key) | ✅ |
| Scheduled publish, PC on | ✅ | ✅ |
| Scheduled publish, **PC off** | ❌ | ✅ |
| Cost | free | ~$7/mo (Render starter) |

## Notes

- The free Render tier **sleeps** after inactivity and would miss scheduled
  jobs — use the **starter** plan (in `render.yaml`) for reliable scheduling.
- imgbb images are public (anyone with the URL can view). That's fine for post
  content, which becomes public on Instagram anyway.
- Rotate `INSTAGRAM_ACCESS_TOKEN` before it expires (long-lived tokens last ~60
  days; refresh via the Graph API).
