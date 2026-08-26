FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY ingestion ./ingestion
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/buzzbot/.cache/huggingface \
    XDG_CACHE_HOME=/home/buzzbot/.cache \
    USAGE_FILE=/var/lib/buzzbot/usage.json

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system buzzbot \
    && useradd --system --gid buzzbot --create-home buzzbot \
    && mkdir -p /app /var/lib/buzzbot /home/buzzbot/.cache/huggingface \
    && chown -R buzzbot:buzzbot /app /var/lib/buzzbot /home/buzzbot/.cache

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels buzzbot-backend \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=buzzbot:buzzbot alembic.ini ./
COPY --chown=buzzbot:buzzbot migrations ./migrations

USER buzzbot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/live', timeout=2)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
