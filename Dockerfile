# InstaContentEngine — cloud backend image (Railway / Render / Fly).
# Runs the FastAPI app 24/7 with APScheduler so scheduled posts publish
# even when the user's PC is off. Local desktop use does NOT need this file.
FROM python:3.11-slim

# Pillow / fonts runtime deps + postgresql-client (pg_dump/psql for backup/restore).
# gosu lets the entrypoint start as root just long enough to chown the mounted
# uploads volume, then drop to a non-root user for the app process.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo zlib1g postgresql-client gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Without this, Python block-buffers stdout/stderr when they are pipes, so
# `docker compose logs app` showed the startup banner and then nothing —
# tracebacks from failed generations sat in the buffer for hours.
ENV PYTHONUNBUFFERED=1

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# Non-root runtime user; owns the app tree (incl. the uploads mount point).
RUN useradd -m -u 10001 appuser \
    && mkdir -p /app/uploads \
    && chown -R appuser:appuser /app

ENV APP_MODE=cloud
# Platforms inject $PORT; default to 8000 locally.
ENV PORT=8000

# Cloud DB should be Postgres via DATABASE_URL env (see DEPLOY.md).
# Enter as root only to fix ownership of the mounted uploads volume (named
# volumes are created root-owned), then exec the app as the non-root appuser.
CMD ["sh", "-c", "chown -R appuser:appuser /app/uploads 2>/dev/null || true; exec gosu appuser uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
