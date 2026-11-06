# SwarmGuard Stage 1 — local-development image.
# Runs without API keys or network access (only the API container exposes a port).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SWARMGUARD_DB_PATH=/data/swarmguard_demo.db

WORKDIR /app

# Unprivileged runtime user; /data holds the SQLite database (mounted as a volume).
RUN groupadd --system swarmguard \
 && useradd --system --gid swarmguard --home-dir /app --shell /usr/sbin/nologin swarmguard \
 && mkdir -p /data \
 && chown swarmguard:swarmguard /data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=swarmguard:swarmguard . .

USER swarmguard
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# Bind to all interfaces *inside* the container; docker-compose publishes it on 127.0.0.1 only.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
