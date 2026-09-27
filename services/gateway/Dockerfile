# syntax=docker/dockerfile:1
#
# Gateway image, also used for the one-shot `migrate` service in compose.yaml.
# Build context is the REPO ROOT (compose.yaml sets it): the gateway needs
# services/shared, migrations/, pyproject.toml and uv.lock, not just its own folder.
# .dockerignore at the root allowlists exactly those, so .env never leaves the laptop.
#
# linux/arm64 always (CLAUDE.md constraint 2). On the dev server that's native.
#
# Base images are pinned by digest as well as tag (KAV-24): a tag can be re-pointed, a
# digest can't. Dependabot bumps both together.

# ── Stage 1: turn uv.lock into a hash-pinned requirements file ──────────────
# uv only lives in this stage; the final image installs with plain pip.
FROM ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 AS uv
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b AS lock
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
# --frozen: use the lock exactly, and fail rather than re-resolve.
RUN uv export --frozen --no-dev --extra gateway --no-emit-project -o /requirements.txt

# ── Stage 2: the runtime image ──────────────────────────────────────────────
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, from the lock, every file hash-checked: this layer only rebuilds
# when uv.lock changes, and the image holds exactly the versions CI tested.
COPY --from=lock /requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Then our own code, with --no-deps so nothing outside the lock can sneak in.
# pyproject.toml lists every service package, so all of them must be present for the build.
COPY pyproject.toml ./
COPY services/shared/kaval_shared services/shared/kaval_shared
COPY services/gateway/kaval_gateway services/gateway/kaval_gateway
COPY services/collector/kaval_collector services/collector/kaval_collector
# Then drop the packaging tools the base image ships: nothing runs them after this line,
# and setuptools vendors old copies of jaraco.context and wheel that Trivy flags HIGH
# (CVE-2026-23949, CVE-2026-24049; found by the first CI run, KAV-24). pip goes too: the
# running service never installs anything, and a shell in the container gets no installer.
RUN pip install --no-deps . \
 && pip uninstall -y setuptools wheel pip
COPY migrations migrations

# The component version, passed in by CI from kaval_gateway.__version__ (ADR-0013). A local build
# without it says "unset" rather than guess, and CI checks the label matches the code.
ARG VERSION=unset
LABEL org.opencontainers.image.title="kaval-gateway"       org.opencontainers.image.version="$VERSION"

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

CMD ["uvicorn", "kaval_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
