# syntax=docker/dockerfile:1
#
# Gateway image, also used for the one-shot `migrate` service in compose.yaml.
# Build context is the REPO ROOT (compose.yaml sets it): the gateway needs
# services/shared, migrations/ and pyproject.toml, not just its own folder.
# .dockerignore at the root allowlists exactly those, so .env never leaves the laptop.
#
# linux/arm64 always (CLAUDE.md constraint 2). On the dev server that's native.

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies and code first, migrations after: editing a migration doesn't
# reinstall anything. pyproject.toml lists every service package, so all of them must be
# present for the build; only the gateway's extra dependencies get installed.
COPY pyproject.toml ./
COPY services/shared/kaval_shared services/shared/kaval_shared
COPY services/gateway/kaval_gateway services/gateway/kaval_gateway
COPY services/collector/kaval_collector services/collector/kaval_collector
RUN pip install ".[gateway]"
COPY migrations migrations

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

CMD ["uvicorn", "kaval_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
