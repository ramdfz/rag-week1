# syntax=docker/dockerfile:1
# Meridian Health Partners RAG chatbot — single-image deploy for Azure App Service.
# FastAPI serves both the REST API and the built React SPA from one origin.

# ------------------------------------------------------------------ #
# Stage 1 — build the React / Vite frontend
# ------------------------------------------------------------------ #
FROM node:22-slim AS frontend

WORKDIR /frontend

# Install dependencies first so this layer caches unless the lockfile changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

# Build inputs.
COPY tsconfig.json vite.config.mjs postcss.config.js index.html ./
COPY src ./src

# IMPORTANT: Vite bakes VITE_* values into the bundle at BUILD time.
# Pass the REAL employee key here (--build-arg VITE_API_KEY=...) so the deployed
# frontend authenticates. It MUST match the runtime API_KEY_AUTH_SECRET app setting.
# Leave VITE_API_BASE_URL empty for same-origin (FastAPI serves the SPA).
ARG VITE_API_BASE_URL=""
ARG VITE_API_KEY=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_API_KEY=${VITE_API_KEY}

RUN pnpm build

# ------------------------------------------------------------------ #
# Stage 2 — Python runtime
# ------------------------------------------------------------------ #
FROM python:3.11-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app ./app
COPY corpus ./corpus
COPY ingest.py configure_semantic_ranker.py compare_semantic_ranking.py evaluate.py ./
# Pre-built index metadata. NOTE: the container filesystem is ephemeral on App
# Service — conversation/feedback writes and uploaded corpus files reset on
# restart/scale. Mount Azure Files or move to Postgres for persistence (design §13).
COPY meridian.db ./
# Built SPA from stage 1.
COPY --from=frontend /frontend/dist ./dist

EXPOSE 8000

# App Service ignores this, but it makes local `docker run` self-reporting.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health').status==200 else 1)"

# Bind to $PORT (Azure injects it; falls back to 8000). `exec` makes gunicorn PID 1
# so it receives SIGTERM directly for graceful shutdown.
CMD ["sh", "-c", "exec gunicorn -k app.worker.NoServerHeaderUvicornWorker app.main:app --bind 0.0.0.0:${PORT:-8000} --timeout 180 --workers ${WEB_CONCURRENCY:-2}"]
