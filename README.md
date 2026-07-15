# InstaContentEngine

AI-powered Instagram & LinkedIn content engine for the **My Life My Game** brand
(running, fitness, healthy habits, productivity). It runs the whole content
loop — **generate → schedule → publish → measure → repeat** — from a single
desktop application, and can also run 24/7 in the cloud for scheduled posting.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/SQLAlchemy-async-d71f00.svg" alt="SQLAlchemy async">
  <img src="https://img.shields.io/badge/UI-pywebview-7c3aed.svg" alt="pywebview">
  <a href="https://github.com/Dev-In-Crypt/InstaContentEngine/actions/workflows/ci.yml">
    <img src="https://github.com/Dev-In-Crypt/InstaContentEngine/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
</p>

---

## ✨ Features

### 📝 Create
- 4-step wizard: topic → settings → SSE-progress generation → preview & edit.
- Platform toggle (Instagram | LinkedIn) with platform-specific SEO prompts.
- Length tier (Hook Zone ~125 chars · Sweet Spot 125–150+ · Deep Dive 300–900+).
- Branded portrait template **1080×1350** (orange niche box + white translucent
  description box + optional logo + manual carousel page number) or square legacy.
- Per-slide overlay text is **editable in place** (Apply / Reset) without a full
  regenerate; slides 2..N of a carousel each get their own unique overlay.
- Separate **SEO keywords** output (never mixed into hashtags).
- **Web-grounded text** (OpenRouter `:online`) — captions can cite real sources,
  shown in a **References** panel alongside stock-photo credits.
- **↻ Variations** on caption / hook / hashtags / SEO (cheap targeted regen).
- **Emoji picker** (sport / fitness / lifestyle pools) that inserts into any field.
- **Per-slide Replace / Upload** — swap one image (stock or AI) or upload your own
  without touching the rest of the post.
- **↶ Undo** (last 5 edits) and **autosave drafts** (localStorage), plus
  **Save ZIP to Downloads** with an Open-folder button.

### 🔥 Trend Finder
- Competitor accounts CRUD, then **Refresh trends** pulls recent Reels of every
  active competitor via the Instagram Business Discovery API (SSE progress).
- Per-media metrics + extracted hook / hashtags / CTA / engagement score.
- **Adapt to MLMG** → one LLM call produces an on-brand idea
  (hook / short script / shot list / caption / CTA / hashtags / SEO keywords).
- **Use idea** pre-fills the wizard; the generated post is linked back to its
  source trend (badge + FK), closing the loop.
- **Hashtag intelligence** — 📊 Analyze ranks tags (🔥 hot / ✅ good / 🆕 niche /
  ⚠️ saturated + trend arrows) from collected competitor media and the IG
  Hashtag Search API.

### 📅 Calendar & Scheduling
- Month grid places posts by scheduled/published date with status dots.
- **Content pillars** — a fixed 5-pillar MLMG mix (Educational / Inspirational /
  Behind-the-scenes / Community / Product) with actual-vs-target bars and a
  “what to post today” suggestion.
- **Schedule** a post (10 min – 75 days). Publishing at the scheduled time uses
  APScheduler; in the cloud it fires even when your PC is off (see below).

### ▦ Grid
- 3-column feed-style preview (first slide of each post) with status rings,
  plus a 📱 Mobile / 🖥 Desktop width toggle.

### 📊 Insights & 💰 Cost
- **Performance** panel on published posts: on-demand refresh of reach / likes /
  comments / saves / shares from the Graph API, stored as snapshots.
- **Cost badge** in the header — live OpenRouter spend today (with a soft limit
  warning) and a month + by-model breakdown; full dashboard in ⚙️ Admin.

### 🎬 Reels
- **Make Reel** renders a vertical 1080×1920 video from the post's slides
  (Ken Burns zoom/pan + text overlays, local ffmpeg via `imageio-ffmpeg`).
  Inline preview + Download MP4 + Publish Reel (cloud). An AI text-to-video
  provider is stubbed behind the same interface for later.

### ⚙️ Admin
- **Backup / Restore** — download a ZIP of the database + generated media, and
  restore it later (local sqlite swap or cloud `pg_dump`/`psql`).

### Platform
- **Desktop app** — double-click `InstaContentEngine.pyw`: self-installs deps,
  bootstraps `.env`, prompts for the API key, starts FastAPI, opens a native
  pywebview window. No browser, no CMD, no second launcher.
- **Cloud mode** — the same code deploys to Render/Railway (Postgres) so
  scheduled posts publish 24/7. See **[DEPLOY.md](DEPLOY.md)**.
- **Multi-brand ready** — `BrandConfig` lives in the DB (palette, alpha, fonts,
  logo, template); `My Life My Game` ships seeded as the default.
- **Telegram bot** (optional) — conversational flow on the same engine.

---

## 🚀 Quick start (end user)

> Requires Python 3.11+ and Windows 10/11 (Edge WebView2 is pre-installed there).

1. **Unzip** `InstaContentEngine.zip` to any folder, e.g. `C:\InstaContentEngine\`.
2. **Configure keys**: open `backend\.env` and set at minimum:
   ```ini
   OPENROUTER_API_KEY=sk-or-v1-...
   ```
   Add `IMGBB_API_KEY` + `INSTAGRAM_*` if you want to publish (see below).
3. **Double-click `InstaContentEngine.pyw`**.
   - First run: installs Python packages (~1–2 min, small progress window).
   - Then: opens a native desktop window with the full UI.

If `.env` is missing or the API key is blank, the launcher opens `.env` in
Notepad with instructions and exits so you can paste your key.

---

## ⚙️ Configuration

Copy `backend/.env.example` → `backend/.env`. Only `OPENROUTER_API_KEY` is
required for the Create wizard.

| Variable | Purpose | Required for |
|----------|---------|--------------|
| `OPENROUTER_API_KEY` | LLM calls (caption, adapt, variations) | Everything text-related |
| `DEFAULT_TEXT_MODEL` | OpenRouter model id | Caption generation |
| `DEFAULT_IMAGE_MODEL` | OpenRouter image-capable model id | AI-generated slide images |
| `UNSPLASH_ACCESS_KEY` / `PEXELS_API_KEY` | Stock photo APIs | Stock photo slides |
| `IMGBB_API_KEY` | Public image hosting (imgbb) | **Publishing / scheduling** (IG can't fetch localhost) |
| `INSTAGRAM_ACCESS_TOKEN` / `INSTAGRAM_USER_ID` | Meta Graph creds | Publishing, Trend Finder, Insights, Hashtag API |
| `TREND_PROVIDER` | `business_discovery` \| `scraper` (stub) | Trend Finder |
| `VIDEO_PROVIDER` | `kenburns` \| `ai` (stub) | Reels |
| `APP_MODE` | `local` \| `cloud` | 24/7 scheduling |
| `PUBLIC_BASE_URL` | Public URL of the backend | Reel publishing (cloud) |
| `DATABASE_URL` | sqlite (local) or Postgres (cloud) | Cloud deploy |
| `API_TOKEN` | Bearer token for the API | Optional auth (required in cloud) |
| `TELEGRAM_BOT_TOKEN` | Bot API token | Telegram bot (optional) |

See **`API_KEYS_GUIDE.md`** for where to obtain each key.

---

## ☁️ Local vs Cloud

Instagram's API has **no native scheduled publish** — something must call the
publish endpoint at the scheduled time. Locally that only happens while the app
is open. For posting when your PC is off, run the same backend in the cloud.

| | Local (`.pyw`) | Cloud (Render) |
|---|---|---|
| Generate / edit / export / Reel render | ✅ | ✅ |
| Publish now | ✅ (needs `IMGBB_API_KEY`) | ✅ |
| Scheduled publish, PC on | ✅ | ✅ |
| Scheduled publish, **PC off** | ❌ | ✅ |
| Reel auto-publish | ❌ (export MP4 only) | ✅ |

Full walkthrough in **[DEPLOY.md](DEPLOY.md)** (`Dockerfile` + `render.yaml`
one-click blueprint, Postgres, imgbb, IG token).

---

## 🎨 The branded card template

```
┌──────────────────────────────────────┐  1080 × 1350 portrait
│         <background photo>           │  ← Unsplash / Pexels / AI gen / upload
│                                      │
│  ┌──────────┐                        │  ← orange niche-box (palette swatch)
│  │ Running  │                        │     bold, ~40px, font Klein (fallback ok)
│  └──────────┘                        │
│  ┌────────────────────────────────┐  │  ← white, alpha ~0.79, aligned across slides
│  │ Run From Asia to Europe.       │  │     description-box = editable overlay (≤2 lines)
│  └────────────────────────────────┘  │
│  1/3                          [logo] │  ← optional manual page number + top-right logo
└──────────────────────────────────────┘
```

Niche-box palette: `#ffbf00` · `#0076cb` · `#5e17eb` · `#00bf63` · `#000000` · `#ff751f`

---

## 🏗️ Architecture

```
InstaContentEngine.pyw  ── deps + .env + key prompt → uvicorn (daemon thread) → pywebview window
        │  (cloud: Docker → uvicorn 24/7 + Postgres + APScheduler)
        ▼
FastAPI (backend/main.py)  — lifespan migrations, brand seed, APScheduler
  /api/posts    generate · list · edit · slides(replace/upload/overlay) ·
                export · export-to-disk · schedule · publish · publish-reel ·
                reel · insights · regenerate-field · pillars/mix
  /api/trends   competitors · refresh · media · adapt · ideas · generate ·
                hashtags/rank
  /api/usage    LLM cost aggregates       /api/admin/backup|restore
  /api/models   /api/stock                /static/*  (single-page UI)
        │
        ▼
Services
  ContentEngine · CaptionGenerator · ImageRouter · PillowBrandEngine
  ImgbbUploader · InstagramPublisher · TemplateExporter
  TrendProvider · TrendExtractor · TrendAdapter · HashtagIntel
  scheduler (APScheduler) · publisher_flow · video/ (KenBurns + AI stub)
  pillars · openrouter (usage capture)
        │
        ▼
DB (sqlite local / Postgres cloud) — SQLAlchemy async
  Post · Slide · BrandConfig · CompetitorAccount · TrendingMedia · TrendIdea
  PostInsight · HashtagStat · LLMUsage · Canva/Instagram tokens
```

### Data flow: generate-from-trend

```
Refresh trends → TrendingMedia → [Adapt] → LLM → TrendIdea
      → [Use] → ContentEngine → Post (trend_idea_id) → Slides (1080×1350)
      → [Schedule/Publish] → imgbb → Instagram → [Refresh insights] → PostInsight
```

---

## 📂 Project structure

```
.
├── InstaContentEngine.pyw      ← desktop entry-point (double-click)
├── Dockerfile · render.yaml · DEPLOY.md   ← cloud deploy
├── README.md · API_KEYS_GUIDE.md · instagram-content-engine-spec.md
└── backend/
    ├── main.py                 ← FastAPI app + lifespan migrations + APScheduler
    ├── config.py · requirements.txt · pytest.ini · .env.example
    ├── api/
    │   ├── deps.py             ← DI factories, auth, brand-config loader
    │   └── routes/             ← posts · trends · admin · models · stock
    ├── services/
    │   ├── content_engine.py · caption_generator.py · brand_engine.py
    │   ├── image_router.py · openrouter.py · stock.py · exporter.py
    │   ├── instagram.py · image_host.py · publisher_flow.py · scheduler.py
    │   ├── trend_provider.py · trend_extractor.py · trend_adapter.py
    │   ├── hashtag_intel.py · pillars.py
    │   └── video/             ← base (Protocol) · kenburns · ai_provider (stub)
    ├── models/                ← database.py (tables) · schemas.py (Pydantic + enums)
    ├── bot/                   ← Telegram bot (optional)
    ├── static/index.html      ← single-page UI (Tailwind CDN, vanilla JS)
    └── tests/                 ← pytest suite (244 passing)
```

---

## 🧪 Development

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # PowerShell
pip install -r requirements.txt
uvicorn main:app --reload --port 8000               # open http://localhost:8000
python -m pytest -q                                 # run tests
del insta.db                                        # reset DB (re-seeds brand preset)
```

**Tests:** **244 passing**, 0 failing. **Lint:** `ruff check backend
InstaContentEngine.pyw` from the repo root (ruff is CI-only — install it with
`pip install ruff`, it is not in `requirements.txt`).

Both run on every push and pull request via [GitHub
Actions](.github/workflows/ci.yml).

### Extending

- **New brand preset** — insert a `brand_configs` row (or extend
  `main.py:_seed_brand_preset`); set `is_default=True`.
- **New trend provider** — implement the `TrendProvider` Protocol in
  `services/trend_provider.py`, register in `api/deps.py:make_trend_provider_for`,
  select via `TREND_PROVIDER`.
- **New DB column** — add it in `models/database.py` and to `_MIGRATIONS` in
  `main.py` (dialect-safe: sqlite PRAGMA / Postgres `ADD COLUMN IF NOT EXISTS`).
- **AI video** — implement `services/video/ai_provider.py:AIVideoProvider`
  (Runway/Kling/Luma) and select via `VIDEO_PROVIDER=ai`.

---

## 🔌 API overview

JSON everywhere; gated by `Bearer <API_TOKEN>` when `API_TOKEN` is set.

| Method | Path | Notes |
|--------|------|-------|
| `GET`  | `/health` | liveness |
| `POST` | `/api/posts/generate` | SSE — generate a post |
| `GET`  | `/api/posts` · `/api/posts/{id}` | list (thumb/status) · full preview |
| `PUT`  | `/api/posts/{id}/caption` | edit caption / hashtags / SEO |
| `POST` | `/api/posts/{id}/regenerate-field` | N variants for one field |
| `POST` | `/api/posts/{id}/slides/{n}/regenerate` · `/upload` | replace / upload one slide |
| `PUT`  | `/api/posts/{id}/slides/{n}/overlay` | edit overlay text in place |
| `POST` | `/api/posts/{id}/export` · `/export-to-disk` | ZIP download / save to Downloads |
| `POST` | `/api/posts/{id}/schedule` · `DELETE` same | schedule / cancel |
| `POST` | `/api/posts/{id}/publish` · `/publish-reel` | publish now / publish Reel |
| `POST` | `/api/posts/{id}/reel` · `GET` `/reel/video` | render / serve Reel MP4 |
| `POST` | `/api/posts/{id}/insights/refresh` · `GET` `/insights` | metrics snapshot / history |
| `GET`  | `/api/posts/pillars/mix` | content-pillar mix + suggestion |
| `POST` | `/api/trends/refresh` | SSE — fetch competitor Reels |
| `GET`/`POST`/`PUT`/`DELETE` | `/api/trends/competitors...` | competitor CRUD |
| `GET`  | `/api/trends/media` · `POST` `/media/{id}/adapt` | browse · adapt to idea |
| `GET`/`PUT`/`DELETE` | `/api/trends/ideas...` · `POST` `/ideas/{id}/generate` | idea CRUD · make post |
| `POST` | `/api/trends/hashtags/rank` | hashtag intelligence |
| `GET`  | `/api/usage` | LLM cost aggregates |
| `GET`  | `/api/admin/backup` · `POST` `/api/admin/restore` | backup / restore |
| `GET`  | `/api/models/*` · `POST` `/api/stock/search` | LLM list · stock search |

---

## 🛣️ Roadmap

- **AI text-to-video** — real Runway/Kling/Luma integration behind the existing
  `AIVideoProvider` stub (async polling, cost).
- **3rd-party trend provider** — Apify Actor for hashtag/explore discovery
  (`ScraperTrendProvider` stub in place).
- **Video understanding** — multimodal LLM to extract the visual hook/shot list
  from a competitor Reel automatically.
- **Multi-platform crossposting** — LinkedIn / Threads / TikTok publish.
- **Klein font** — drop `Klein-*.ttf` into `backend/static/fonts/` and point the
  `BrandConfig` preset at it.

---

## 📜 License

Proprietary — for the My Life My Game brand. Not for redistribution.

## 🤝 Contributing

Internal, single-brand engine. Follow the conventional-commits style used in the
git history (`feat:`, `fix:`, `chore:`, `test:`, `feat(trends):`, …).
