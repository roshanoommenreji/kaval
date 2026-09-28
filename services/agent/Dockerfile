# syntax=docker/dockerfile:1
#
# Agent image. Its entrypoint runs correlation (KAV-39): signals in, incident rows out.
#   make correlate                                   # one pass
#   python -m kaval_agent.correlate --every 30       # a loop, for when it's deployed
# Context assembly (KAV-40) and diagnosis (KAV-41, ADR-0016) ship in this image too — the
# whole kaval_agent package is copied below — but run as laptop-invoked tools for now
# (`make index-runbooks`, `make context`, `make diagnose`; see Lab 10, Lab 11), not as part
# of the entrypoint's own loop: no orchestration decision for "run diagnosis automatically
# after every correlation pass" has been made yet, and the `agent` extra (httpx) isn't
# installed here, so an unattended run would fail loudly rather than silently doing nothing.
# Build context is the REPO ROOT, like the other images (compose.yaml sets it), and the
# root .dockerignore allowlist decides what leaves the laptop.
#
# Core dependencies only: no FastAPI, no uvicorn, no Kubernetes or AWS client. The agent
# reads the database and writes its own rows; it has no library to change anything else
# with, which is the privilege split (CLAUDE.md constraint 3) showing up in the image.
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
COPY services/agent/kaval_agent services/agent/kaval_agent
# Then drop the packaging tools the base image ships: nothing runs them after this line,
# and setuptools vendors old copies of jaraco.context and wheel that Trivy flags HIGH
# (CVE-2026-23949, CVE-2026-24049; found by the first CI run, KAV-24). pip goes too: the
# running service never installs anything, and a shell in the container gets no installer.
RUN pip install --no-deps . \
 && pip uninstall -y setuptools wheel pip

# The component version, passed in by CI from kaval_agent.__version__ (ADR-0013). A local build
# without it says "unset" rather than guess, and CI checks the label matches the code.
ARG VERSION=unset
LABEL org.opencontainers.image.title="kaval-agent"       org.opencontainers.image.version="$VERSION"

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

ENTRYPOINT ["python", "-m", "kaval_agent.correlate"]
