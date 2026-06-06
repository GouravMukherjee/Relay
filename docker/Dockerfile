# Relay backend — single shared image for all three services.
# The image is identical; the three services differ only by the start command
# (set in docker-compose.yml locally and in deploy/*.py on TrueFoundry).
#
#   gateway    -> uvicorn app.gateway:app
#   retrieval  -> uvicorn app.retrieval:app
#   worker     -> python -m app.worker   (no inbound port)

FROM python:3.11-slim AS base

# - PYTHONUNBUFFERED: logs stream immediately (TrueFoundry/compose log tailing)
# - PIP_NO_CACHE_DIR: smaller image
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# System deps: libpq for asyncpg/pg tooling, curl for container healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for layer caching.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code.
COPY app/ ./app/

# Run as non-root.
RUN useradd --create-home --uid 10001 relay
USER relay

EXPOSE 8000

# Default command runs the gateway; overridden per-service.
CMD ["uvicorn", "app.gateway:app", "--host", "0.0.0.0", "--port", "8000"]
