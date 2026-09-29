# ---------- 1. Build the web UI (Node is used only here, not on the server) ----------
FROM node:22-alpine AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# vite outDir is ../app/web  ->  /src/app/web
RUN npm run build

# ---------- 2. Runtime: slim Python image, unprivileged user ----------
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data

WORKDIR /app
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin jarvis \
    && mkdir -p /data && chown jarvis:jarvis /data

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY app ./app
COPY --from=web /src/app/web ./app/web

USER jarvis
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1

CMD ["python", "-m", "app.main"]
