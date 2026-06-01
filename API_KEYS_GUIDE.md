# API Keys Setup Guide — InstaContentEngine

Copy `.env.example` to `.env` and fill in the keys below.
Only **OpenRouter** is required to run the app. Everything else is optional.

---

## 1. OpenRouter (Required — AI text + image generation)

Used for: caption generation, AI image generation.

1. Go to **https://openrouter.ai**
2. Sign up / log in
3. Navigate to **Keys** → **Create Key**
4. Copy the key (starts with `sk-or-v1-...`)
5. Add credits under **Credits** tab (a few dollars is enough to start)

```env
OPENROUTER_API_KEY=sk-or-v1-...
```

**Choosing models** — any model ID from https://openrouter.ai/models works:

```env
DEFAULT_TEXT_MODEL=openai/gpt-4o          # or google/gemini-2.5-flash, meta-llama/llama-3.3-70b-instruct
DEFAULT_IMAGE_MODEL=google/gemini-2.5-flash-image  # or openai/dall-e-3, black-forest-labs/flux-1.1-pro
```

---

## 2. Unsplash (Optional — stock photos, free)

Used for: stock photo slides when image source = "stock".

1. Go to **https://unsplash.com/developers**
2. Click **New Application**
3. Accept terms → fill in app name/description
4. Copy the **Access Key** (not the Secret Key)

```env
UNSPLASH_ACCESS_KEY=your-access-key
```

Free tier: 50 requests/hour. Sufficient for normal use.

---

## 3. Pexels (Optional — stock photos, free)

Used for: stock photo fallback if Unsplash fails.

1. Go to **https://www.pexels.com/api**
2. Click **Get Started** → log in
3. Copy your API key from the dashboard

```env
PEXELS_API_KEY=your-pexels-key
```

Free tier: 200 requests/hour.

---

## 4. Instagram / Meta (Optional — publishing to Instagram)

Used for: publishing posts directly to Instagram.

**Requires a Meta Developer account and an Instagram Professional account.**

1. Go to **https://developers.facebook.com**
2. Create an app → choose **Business** type
3. Add **Instagram Graph API** product
4. Under **Instagram** → **API setup with Instagram Business Login**
5. Generate a long-lived access token (valid 60 days, then must refresh)
6. Find your Instagram User ID in the token debug tool

```env
INSTAGRAM_ACCESS_TOKEN=your-long-lived-token
INSTAGRAM_USER_ID=your-ig-user-id
META_APP_ID=your-app-id
META_APP_SECRET=your-app-secret
```

> Note: Instagram publishing requires the server to be publicly accessible
> (the API fetches images by URL). Use ngrok for local testing.

---

## 5. Canva (Optional — Canva template slides)

Used for: generating slides from Canva templates.

1. Go to **https://www.canva.com/developers**
2. Create an integration → OAuth2 app
3. Set redirect URI to `http://localhost:8000/auth/canva/callback`
4. Copy Client ID and Client Secret

```env
CANVA_CLIENT_ID=your-client-id
CANVA_CLIENT_SECRET=your-client-secret
CANVA_REDIRECT_URI=http://localhost:8000/auth/canva/callback
```

---

## 6. Telegram Bot (Optional — Telegram bot interface)

Used for: controlling the engine via Telegram chat.

1. Open Telegram, search for **@BotFather**
2. Send `/newbot` → follow instructions
3. Copy the token

```env
TELEGRAM_BOT_TOKEN=123456:ABC-your-token
```

---

## 7. API Token (Optional — password-protect the web UI)

If set, the web UI will show a password prompt on first open.
Leave empty to disable auth (fine for local use).

```env
API_TOKEN=your-secret-password
```

---

## Quick Start (minimum config)

```env
OPENROUTER_API_KEY=sk-or-v1-...
DEFAULT_TEXT_MODEL=openai/gpt-4o
DEFAULT_IMAGE_MODEL=google/gemini-2.5-flash-image
UNSPLASH_ACCESS_KEY=your-unsplash-key
```

### Running the app

**Desktop GUI** (recommended — no browser needed):
```
double-click  start.bat
```
Or:  `python backend/gui.py`

**Web server** (browser-based UI):
```
double-click  start_server.bat
```
Then open http://localhost:8000 in your browser.

### Requirements

Install dependencies once:
```
pip install -r backend/requirements.txt
```
PyQt6 is required for the desktop GUI. It is included in `requirements.txt`.
