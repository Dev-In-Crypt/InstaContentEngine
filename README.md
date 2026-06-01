# InstaContentEngine

AI-powered Instagram & LinkedIn content engine for the **My Life My Game** brand
(running, fitness, healthy habits, productivity). Generates SEO-optimized posts,
brands them with a configurable portrait template, and turns trending competitor
Reels into adapted, on-brand ideas — all from a single desktop application.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/SQLAlchemy-async-d71f00.svg" alt="SQLAlchemy async">
  <img src="https://img.shields.io/badge/UI-pywebview-7c3aed.svg" alt="pywebview">
  <img src="https://img.shields.io/badge/tests-158%20passing-22c55e.svg" alt="158 tests">
</p>

---

## ✨ Features

- **Create wizard** — 4-step UI: topic → settings → SSE-progress generation → preview & edit.
  - Platform toggle (Instagram | LinkedIn) with platform-specific SEO prompts.
  - Length tier (Hook Zone ~125 chars · Sweet Spot 125–150+ · Deep Dive 300–900+).
  - Branded portrait template **1080×1350** (orange niche box + white translucent
    description box + optional logo + manual carousel page number) or square legacy.
  - 6-color niche-box palette, toggleable branding & logo.
  - Separate **SEO keywords** output (not mixed into hashtags).
  - Edit caption / hashtags / SEO keywords inline, export ZIP, publish to Instagram.

- **Trend Finder** — `find → adapt → publish → measure → repeat`.
  - Competitor accounts CRUD seeded by you.
  - **Refresh trends**: pulls recent Reels of every active competitor via Instagram
    Business Discovery API, streams progress over SSE.
  - Per-media metrics + extracted hook / hashtags / CTA / engagement score.
  - **Adapt to MLMG**: one LLM call generates an on-brand idea
    (hook / short script / shot list / caption / CTA / hashtags / SEO keywords).
  - **Use idea** → wizard pre-fills topic + script + shot list → generated post
    is linked back to its source trend (badge in the preview, FK in the DB).

- **Desktop application** — double-click `InstaContentEngine.pyw`:
  self-installs Python deps, bootstraps `.env`, prompts for the API key,
  starts FastAPI in the background, opens a native window via pywebview.
  No browser. No CMD. No second launcher.

- **Multi-brand ready** — `BrandConfig` lives in the database with palette,
  alpha, fonts, logo position, template style; preset rows are seeded on first
  boot (`My Life My Game` ships as the default).

- **Telegram bot** (optional) — conversational flow on top of the same engine.

---

## 🚀 Quick start (end user)

> Requires Python 3.11+ and Windows 10/11 (Edge WebView2 is pre-installed there).

1. **Download** [InstaContentEngine.zip](#) (or clone this repo).
2. **Unzip** to any folder, e.g. `C:\InstaContentEngine\`.
3. **Configure keys**: open `backend\.env` and set at minimum:
   ```ini
   OPENROUTER_API_KEY=sk-or-v1-...
   ```
   (other keys are optional — see [Configuration](#-configuration)).
4. **Double-click `InstaContentEngine.pyw`**.
   - First run only: installs Python packages (~1 minute, small progress window).
   - Then: opens a native desktop window with the full UI.

That's it. There is **no second launcher** and the browser is **not** opened.

If `.env` is missing or the API key is blank, the launcher opens `.env` in
Notepad with instructions and exits so you can paste your key.

---

## ⚙️ Configuration

Copy `backend/.env.example` → `backend/.env` and fill in what you need. Only
`OPENROUTER_API_KEY` is required for the Create wizard to work.

| Variable | Purpose | Required for |
|----------|---------|--------------|
| `OPENROUTER_API_KEY` | LLM calls (caption + trend adaptation) | Everything text-related |
| `DEFAULT_TEXT_MODEL` | OpenRouter model id | Caption generation |
| `DEFAULT_IMAGE_MODEL` | OpenRouter model id (image-capable) | AI-generated slide images |
| `UNSPLASH_ACCESS_KEY` | Unsplash API key | Stock photo slides |
| `PEXELS_API_KEY` | Pexels API key | Stock photo fallback |
| `INSTAGRAM_ACCESS_TOKEN` | Meta Graph long-lived token | Publishing **and** Trend Finder |
| `INSTAGRAM_USER_ID` | IG Business/Creator user id | Publishing **and** Trend Finder |
| `TELEGRAM_BOT_TOKEN` | Bot API token | Telegram bot (optional) |
| `TREND_PROVIDER` | `business_discovery` or `scraper` (stub) | Trend Finder |
| `API_TOKEN` | Bearer token for the local API | Optional auth |

See **`API_KEYS_GUIDE.md`** for where to obtain each key.

---

## 🎨 The branded card template

```
┌──────────────────────────────────────┐  1080 × 1350 portrait
│                                      │
│         <background photo>           │  ← Unsplash / Pexels / AI gen
│                                      │
│                                      │
│  ┌────────────────────────────────┐  │  ← orange niche-box (palette swatch)
│  │  Running                       │  │     bold, ~40px, font Klein (fallback ok)
│  └────────────────────────────────┘  │
│  ┌────────────────────────────────┐  │  ← white, alpha ~0.79
│  │  Run From Asia to Europe       │  │     description-box with the hook
│  │  Two Continents, One Finish    │  │     (1–2 rows)
│  └────────────────────────────────┘  │
│                                      │
│  1/3                          [logo] │  ← optional manual page number
└──────────────────────────────────────┘     + optional top-right logo
```

Niche-box palette (selectable per post):
`#ffbf00` · `#0076cb` · `#5e17eb` · `#00bf63` · `#000000` · `#ff751f`

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  InstaContentEngine.pyw  (entry-point)                       │
│  ├── deps check + .env bootstrap + key prompt                │
│  └── starts uvicorn in a daemon thread, opens pywebview      │
└────────────┬─────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────┐
│  FastAPI (backend/main.py)                                   │
│  ├── /api/posts   — generate / list / edit / export / publish │
│  ├── /api/trends  — competitors · refresh · media · adapt    │
│  │                  · ideas · generate-from-idea             │
│  ├── /api/models  — list available LLMs                      │
│  ├── /api/stock   — Unsplash / Pexels search                 │
│  └── /static/*    — single-page UI served at /              │
└────────────┬─────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────┐
│  Services                                                    │
│  ├── ContentEngine        orchestrates a post end-to-end     │
│  ├── CaptionGenerator     LLM caption + SEO + hashtags       │
│  ├── ImageRouter          stock / AI / Canva sources         │
│  ├── PillowBrandEngine    portrait card renderer             │
│  ├── TrendProvider        Business Discovery (+ stub)        │
│  ├── TrendExtractor       hook/hashtags/CTA heuristics       │
│  ├── TrendAdapter         LLM trend → MLMG idea              │
│  ├── InstagramPublisher   Meta Graph publish flow            │
│  └── TemplateExporter     ZIP package                        │
└────────────┬─────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────┐
│  SQLite (backend/insta.db) — SQLAlchemy async                │
│  Post · Slide · BrandConfig · CompetitorAccount ·            │
│  TrendingMedia · TrendIdea · InstagramToken · CanvaToken     │
└──────────────────────────────────────────────────────────────┘
```

### Data flow: generate-from-trend

```
Refresh trends ── Business Discovery ──► TrendingMedia (likes/comments/views)
                                                  │
                                          [User clicks Adapt]
                                                  ▼
                                  LLM ──► TrendIdea  (hook+script+shot_list+caption)
                                                  │
                                          [User clicks Use]
                                                  ▼
                              ContentEngine ──► Post  ── trend_idea_id ──► TrendIdea
                                                  │
                                                  └─► Slide (1080×1350 branded card)
```

---

## 📂 Project structure

```
.
├── InstaContentEngine.pyw      ← desktop entry-point (double-click)
├── README.md
├── API_KEYS_GUIDE.md           ← how to obtain each API key
├── instagram-content-engine-spec.md
├── test_live.py                ← manual end-to-end smoke script
└── backend/
    ├── main.py                 ← FastAPI app + lifespan migrations
    ├── config.py               ← pydantic-settings
    ├── requirements.txt
    ├── pytest.ini
    ├── .env.example
    ├── api/
    │   ├── deps.py             ← DI factories, auth, brand-config loader
    │   └── routes/
    │       ├── posts.py
    │       ├── trends.py
    │       ├── models.py
    │       └── stock.py
    ├── services/
    │   ├── content_engine.py
    │   ├── caption_generator.py
    │   ├── brand_engine.py     ← portrait branded card + legacy square
    │   ├── image_router.py
    │   ├── openrouter.py
    │   ├── stock.py            ← Unsplash + Pexels
    │   ├── instagram.py        ← Graph API publisher
    │   ├── exporter.py         ← ZIP packager
    │   ├── trend_provider.py   ← Business Discovery + stub
    │   ├── trend_extractor.py  ← hook/hashtags/CTA heuristics
    │   └── trend_adapter.py    ← LLM trend → MLMG idea
    ├── models/
    │   ├── database.py         ← SQLAlchemy tables
    │   └── schemas.py          ← Pydantic models + enums
    ├── tasks/                  ← Celery app + stubs
    ├── bot/                    ← Telegram bot (optional)
    ├── static/index.html       ← single-page UI (Tailwind CDN, vanilla JS)
    └── tests/                  ← pytest suite (16 files, 158 passing)
```

---

## 🧪 Development

### Run the server in dev mode (browser instead of native window)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate           # PowerShell
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# Open http://localhost:8000
```

### Run the tests

```bash
cd backend
python -m pytest -q
```

Expected: **158 passing**. There are 6 pre-existing failures and 21 errors in
`test_api.py`, `test_image_router.py`, and `test_openrouter.py` — these are
known-broken legacy tests (stale `_post_store` fixture, outdated
`images/generations` HTTPX mocks) that are unrelated to current features.

### Reset the dev database

```bash
del backend\insta.db        # PowerShell / cmd
# next launch recreates the schema and re-seeds the My Life My Game brand preset
```

### Adding a new brand preset

Insert a row into the `brand_configs` table (or extend the seed in
`main.py:_seed_brand_preset`). Set `is_default=True` on the one you want
selected when no `brand_config_id` is supplied to `/api/posts/generate`.

### Adding a new trend provider

Implement the `TrendProvider` Protocol in `services/trend_provider.py`
(`async def fetch_for_handles(...) -> list[FetchedMedia]`), register it in
`api/deps.py:make_trend_provider_for`, expose it via the `TREND_PROVIDER`
env var.

---

## 🔌 API overview

All endpoints are JSON, all are gated by `Bearer <API_TOKEN>` when `API_TOKEN`
is set in `.env` (off by default for local desktop use).

| Method | Path | Notes |
|--------|------|-------|
| `GET`  | `/health` | liveness probe |
| `POST` | `/api/posts/generate` | SSE stream — generates a post end-to-end |
| `GET`  | `/api/posts` | list summaries |
| `GET`  | `/api/posts/{id}` | full preview |
| `PUT`  | `/api/posts/{id}/caption` | edit caption / hashtags / SEO keywords |
| `POST` | `/api/posts/{id}/export` | ZIP download |
| `POST` | `/api/posts/{id}/publish` | publish to Instagram |
| `GET`  | `/api/posts/{id}/slides/{n}/image` | slide image (JPEG) |
| `GET`  | `/api/trends/competitors` | list competitor accounts |
| `POST` | `/api/trends/competitors` | add competitor (`{handle, niche, active}`) |
| `PUT`  | `/api/trends/competitors/{id}` | toggle active / change niche |
| `DELETE` | `/api/trends/competitors/{id}` | remove |
| `POST` | `/api/trends/refresh` | SSE — fetches Reels for every active competitor |
| `GET`  | `/api/trends/media` | list with `sort` / `media_type` filters |
| `POST` | `/api/trends/media/{id}/adapt` | LLM → `TrendIdea` |
| `GET`  | `/api/trends/ideas` | list ideas |
| `PUT`  | `/api/trends/ideas/{id}` | edit idea fields |
| `POST` | `/api/trends/ideas/{id}/generate` | SSE — make a post from the idea |
| `GET`  | `/api/models/text` & `/image` | available LLMs |
| `POST` | `/api/stock/search` | Unsplash / Pexels search |

---

## 🛣️ Roadmap

- **Insights collection** (Scope B): periodic snapshots of `impressions / reach /
  plays / saves / shares` per published post, fed back into the trend dashboard
  so we can rank ideas by *what actually worked*. The DB FK already exists.
- **3rd-party trend providers**: Apify Actor for hashtag/explore discovery (the
  `ScraperTrendProvider` stub is already in place).
- **Video understanding**: pass Reels URLs to a multimodal LLM to extract the
  visual hook + shot list automatically.
- **Auto-suggest**: nightly Celery job that creates top-N adapted ideas without
  user intervention.
- **Klein font**: drop `Klein-Bold.ttf` / `Klein-Regular.ttf` into
  `backend/static/fonts/` and wire the path into the `BrandConfig` preset.

---

## 📜 License

Proprietary — for the My Life My Game brand. Not for redistribution.

---

## 🤝 Contributing

This is a focused single-brand engine. Internal contributions follow the
conventional-commits style already used in the git history
(`feat:`, `fix:`, `chore:`, `test:`, `feat(trends):`, etc.).
