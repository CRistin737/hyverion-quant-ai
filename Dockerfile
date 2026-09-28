FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=180

RUN groupadd --system hyverion && useradd --system --gid hyverion --create-home hyverion
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[memory,embeddings]"

COPY config ./config
COPY agents ./agents
COPY spec ./spec
COPY docs ./docs
COPY AGENTS.md ./AGENTS.md
COPY alembic.ini ./
COPY migrations ./migrations
RUN mkdir -p /app/data/parquet /app/logs && chown -R hyverion:hyverion /app

USER hyverion
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD ["python", "-m", "trading_bot", "doctor"]

CMD ["python", "-m", "trading_bot", "run", "--interval-seconds", "60"]
