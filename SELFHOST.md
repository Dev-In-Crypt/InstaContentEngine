# Self-Hosting on a $5 VPS

Run the public multi-tenant site on your own server with Docker Compose. This is
the cheapest path (~$5/mo for the VPS; Postgres + storage run in containers at no
extra cost). You manage the box; you get automatic HTTPS out of the box.

**Stack:** the app + Postgres + Caddy (reverse proxy that auto-provisions a
Let's Encrypt TLS certificate). All defined in `docker-compose.yml`.

## 1. Get a VPS + a domain

- Rent a small VPS (e.g. **Hetzner CX22 ~€4/mo**, or a $5–6 droplet). Ubuntu is fine.
- Point a domain (or subdomain) at it: add a **DNS A-record** → the VPS's public IP.
- Make sure ports **80** and **443** are open (needed for HTTPS certificates).

## 2. Install Docker on the VPS

```bash
curl -fsSL https://get.docker.com | sh
```

## 3. Get the code + configure

```bash
git clone https://github.com/Dev-In-Crypt/InstaContentEngine.git
cd InstaContentEngine
cp compose.env.example .env
nano .env        # fill in DOMAIN, SECRET_KEY, DB_PASSWORD, ADMIN_EMAILS, PUBLIC_BASE_URL
```

Generate a strong `SECRET_KEY`:

```bash
openssl rand -base64 48
```

> **⚠️ SECRET_KEY is permanent.** It signs logins **and** encrypts users' stored
> API keys. Never change it after go-live — rotating it logs everyone out and
> makes every stored credential undecryptable.

## 4. Launch

```bash
docker compose up -d --build
```

First boot builds the image, starts Postgres, runs migrations, and Caddy fetches
the TLS certificate (takes ~30s). Then:

```bash
curl https://your-domain.com/health      # -> {"status":"ok"}
```

Open `https://your-domain.com/` → the **login / register** screen.

## 5. Use it

- Register (email + password) → **⚙️ Settings** → paste your own keys (OpenRouter
  for generation; Instagram token + user id + imgbb for IG publishing; X keys for X).
- Generate, schedule, publish — the server does 24/7 scheduled publishing with each
  user's own keys, even when your PC is off.

## Operations

```bash
docker compose logs -f app          # app logs
docker compose pull && docker compose up -d --build   # update to latest code (git pull first)
docker compose down                 # stop (data survives in named volumes)
```

**Backups.** The database and uploads live in Docker named volumes (`pgdata`,
`uploads`). Back them up regularly:

```bash
# database dump
docker compose exec db pg_dump -U insta insta > insta_$(date +%F).sql
# uploaded media
docker run --rm -v instacontentengine_uploads:/u -v "$PWD":/b alpine tar czf /b/uploads_$(date +%F).tgz -C /u .
```

(Admins listed in `ADMIN_EMAILS` can also use the in-app **Backup/Restore** under ⚙️ Settings.)

## Notes

- **You maintain the server**: OS security updates, backups, uptime. That's the
  trade for the low cost — a managed host (see `DEPLOY.md` for Render) does this
  for you at higher price.
- Postgres runs as a container here (free); the app auto-normalizes its URL to the
  async driver. To use a managed Postgres instead, just point `DATABASE_URL` at it.
- Single VPS = single instance, which is exactly what the app expects.
