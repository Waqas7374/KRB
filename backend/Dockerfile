# syntax=docker/dockerfile:1.7

# =============================================================================
# Base — shared runtime layer
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Runtime libraries only. Build tools live in the builder stage and are not
# shipped: a smaller image is also a smaller attack surface.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        # WeasyPrint (PDF rendering) native dependencies
        libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
        libffi8 libharfbuzz-subset0 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# =============================================================================
# Builder — compiles wheels into a venv that later stages copy
# =============================================================================
FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

COPY pyproject.toml ./
# Install dependencies without the application itself, so a source change does
# not invalidate the dependency layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip setuptools wheel && \
    pip install ".[pdf]"

# =============================================================================
# Development — adds dev tooling, source is bind-mounted by compose
# =============================================================================
FROM builder AS development

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ".[dev,pdf]"

ENV APP_ENV=development
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# =============================================================================
# Production
# =============================================================================
FROM base AS production

COPY --from=builder /opt/venv /opt/venv

RUN groupadd --system --gid 1001 app && \
    useradd --system --uid 1001 --gid app --no-create-home app

COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app

USER app

ENV APP_ENV=production
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 4 uvicorn workers under gunicorn. Tune with WEB_CONCURRENCY.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
