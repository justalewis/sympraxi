# syntax=docker/dockerfile:1

# --- Browser assets (USWDS, htmx), copied from npm packages ---------------------
FROM node:24-trixie-slim AS assets
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/scripts ./scripts
COPY frontend/src ./src
RUN mkdir -p ../sympraxi/static && npm run build

# --- Python dependencies, installed with uv (uv itself stays out of the final image)
FROM python:3.12-slim-trixie AS python-deps
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
RUN pip install --no-cache-dir uv==0.13.1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- Application -----------------------------------------------------------------
FROM python:3.12-slim-trixie AS common
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Pick up Debian security fixes released since the base image was built.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /opt/venv /opt/venv

WORKDIR /app
COPY manage.py LICENSE ./
COPY sympraxi ./sympraxi
# The topic vocabulary and resource catalog, loaded by `manage.py load_catalog`.
COPY catalog ./catalog
# The open-content pack (content-pack/cc-by; its noncommercial folder is in .dockerignore).
COPY content-pack ./content-pack
COPY --from=assets /build/sympraxi/static/vendor ./sympraxi/static/vendor
RUN SYMPRAXI_SECRET_KEY=build-only DATABASE_URL=postgres://build@localhost/build \
    python manage.py collectstatic --noinput

RUN useradd --system --uid 10001 --no-create-home sympraxi \
    && mkdir -p /app/media && chown sympraxi /app/media
USER sympraxi

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-m", "sympraxi.healthcheck"]

# Gunicorn takes its number of workers from WEB_CONCURRENCY. Set it in deploy/.env
# to size an installation (docs/install.md, "Web workers").
ENV WEB_CONCURRENCY=3
# The access log hides survey link tokens (sympraxi/core/logs.py).
CMD ["gunicorn", "sympraxi.wsgi", "--bind", "0.0.0.0:8000", "--timeout", "60", "--access-logfile", "-", "--logger-class", "sympraxi.core.access_log.AccessLog"]

# --- The task worker, with LibreOffice -------------------------------------------
# Makes the PDF copies of Word, OpenDocument, rich text and plain text files that
# the comment viewer draws (SYMPRAXI_CONVERT_DOCUMENTS=true; docs/install.md,
# "Files"). Only the worker needs it, so the web and scheduler images stay small.
FROM common AS worker
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
USER sympraxi

# --- The default image: everything but LibreOffice -------------------------------
# Last on purpose, so `docker build .` makes this one.
FROM common AS app

