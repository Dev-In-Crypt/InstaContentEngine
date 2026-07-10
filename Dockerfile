# InstaContentEngine — cloud backend image (Railway / Render / Fly).
# Runs the FastAPI app 24/7 with APScheduler so scheduled posts publish
# even when the user's PC is off. Local desktop use does NOT need this file.
FROM python:3.11-slim

# Pillow / fonts runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

ENV APP_MODE=cloud
# Platforms inject $PORT; default to 8000 locally.
ENV PORT=8000

# Cloud DB should be Postgres via DATABASE_URL env (see DEPLOY.md).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
