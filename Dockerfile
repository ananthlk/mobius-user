# syntax=docker/dockerfile:1.7
#
# mobius-user — Cloud Run image
#
# Self-contained service. No sibling repos to vendor.
# Multi-stage so the final image doesn't carry build deps.

# ── builder ────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install into a venv we copy into the final stage.
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY requirements.txt /build/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r /build/requirements.txt

# ── runtime ────────────────────────────────────────────────────────
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH" \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        && rm -rf /var/lib/apt/lists/* && \
    useradd --system --no-create-home --uid 1000 mobiususer

WORKDIR /app

COPY --from=builder /venv /venv
COPY --chown=mobiususer:mobiususer mobius_user /app/mobius_user
COPY --chown=mobiususer:mobiususer app /app/app
COPY --chown=mobiususer:mobiususer migrations /app/migrations
COPY --chown=mobiususer:mobiususer alembic.ini /app/alembic.ini

USER mobiususer

EXPOSE 8080

# Cloud Run sets PORT. Honor it.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --workers 1 --proxy-headers
