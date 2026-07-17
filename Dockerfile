FROM node:22-slim AS frontend

WORKDIR /frontend

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.json vite.config.mjs postcss.config.js index.html ./
COPY src ./src

ARG VITE_API_BASE_URL=""
ARG VITE_API_KEY=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_API_KEY=${VITE_API_KEY}

RUN corepack enable \
    && pnpm install --frozen-lockfile \
    && pnpm build


FROM python:3.11-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY corpus ./corpus
COPY ingest.py configure_semantic_ranker.py compare_semantic_ranking.py evaluate.py ./
COPY meridian.db ./
COPY --from=frontend /frontend/dist ./dist

EXPOSE 8000

CMD ["gunicorn", "-k", "app.worker.NoServerHeaderUvicornWorker", "app.main:app", "--bind", "0.0.0.0:8000", "--timeout", "180", "--workers", "2"]
