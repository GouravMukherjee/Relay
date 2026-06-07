# Relay backend — single shared image for all three processes.
# The image is identical; the processes differ only by the start command
# (set per-service in deploy/deploy_*.py on TrueFoundry, or in docker-compose locally):
#
#   gateway  -> uvicorn relay.gateway.app:app --host 0.0.0.0 --port 8000   (public)
#   agent    -> python -m relay.agent.worker start                         (no inbound port)
#   ingest   -> arq relay.ingestion.worker.WorkerSettings                  (no inbound port)
#   migrate  -> alembic -c alembic.ini upgrade head                        (one-off job)

FROM python:3.11-slim AS base

# - PYTHONUNBUFFERED: logs stream immediately (TrueFoundry log tailing)
# - PYTHONDONTWRITEBYTECODE: no .pyc clutter
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

# Install Python deps first for layer caching (changes rarely vs. app code).
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code + packaging metadata. relay/migrations/ ships in the image so the
# migrate job can run `alembic upgrade head`.
COPY pyproject.toml alembic.ini ./
COPY relay/ ./relay/

# Register the package (deps already installed above, so skip re-resolving them).
RUN pip install -e . --no-deps

# Run as non-root.
RUN useradd --create-home --uid 10001 relay
USER relay

EXPOSE 8000

# Default command runs the gateway; overridden per-service in deploy/*.py.
CMD ["uvicorn", "relay.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
