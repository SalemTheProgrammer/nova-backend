FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
# Migrations et seeds (ex. `docker compose exec nova-backend python -m scripts.migrate_mqtt`).
COPY scripts ./scripts

# Run as a non-root user. /data holds the SQLite databases and uploaded PDFs
# (mount a volume there; see the root docker-compose.yml).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/documents \
    && chown -R appuser /data
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
