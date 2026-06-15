FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/kafuuchino-s/fireworks2api" \
    org.opencontainers.image.description="Fireworks API proxy with OpenAI-compatible routes and an Admin dashboard"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
COPY data/.gitkeep ./data/.gitkeep
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN pip install --no-cache-dir ".[tokenizer]"

# Pre-download HuggingFace tokenizers so the image does not need to call HF Hub
# at runtime.
RUN python scripts/predownload_tokenizers.py

RUN useradd --uid 1000 --create-home --shell /bin/sh appuser \
    && chown -R appuser:appuser /app \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}"]
