# syntax=docker/dockerfile:1
#
# Collector image. Phase 1 runs only the synthetic source in it, one-shot:
#   make signals SCENARIO=oom-crashloop
# Build context is the REPO ROOT, like the gateway's (compose.yaml sets it), and the
# root .dockerignore allowlist decides what leaves the laptop.
#
# One pyproject.toml covers every service, so the wheel carries all the service packages,
# but this image installs only the core dependencies: no FastAPI, no uvicorn. The
# collector's attack surface is its own code plus SQLAlchemy and psycopg.
#
# linux/arm64 always (CLAUDE.md constraint 2). Base images pinned by digest (KAV-24).

# ── Stage 1: uv.lock → hash-pinned requirements (core dependencies only) ─────
FROM ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 AS uv
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b AS lock
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /requirements.txt

# ── Stage 2: the runtime image ──────────────────────────────────────────────
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY --from=lock /requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

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

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

ENTRYPOINT ["python", "-m", "kaval_collector.synthetic"]
CMD ["--list"]
