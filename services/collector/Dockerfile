# syntax=docker/dockerfile:1
#
# Collector image. Two entrypoints live here:
#   make signals SCENARIO=oom-crashloop         # fake incidents (Phase 1), one-shot
#   python -m kaval_collector.k8s_events --every 15   # real K8s events (KAV-48), the
#                                                       # Helm-deployed shape — overrides
#                                                       # this image's default ENTRYPOINT
# Build context is the REPO ROOT, like the gateway's (compose.yaml sets it), and the
# root .dockerignore allowlist decides what leaves the laptop.
#
# One pyproject.toml covers every service, so the wheel carries all the service packages.
# This image installs core deps plus its own `collector` extra (the Kubernetes client,
# KAV-48) — but still no FastAPI, no uvicorn, no Anthropic/Bedrock client.
#
# linux/arm64 always (CLAUDE.md constraint 2). Base images pinned by digest (KAV-24).

# ── Stage 1: uv.lock → hash-pinned requirements, core deps + the collector extra only ──────
FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b AS lock
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --extra collector -o /requirements.txt

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

# The component version, passed in by CI from kaval_collector.__version__ (ADR-0013). A local build
# without it says "unset" rather than guess, and CI checks the label matches the code.
ARG VERSION=unset
LABEL org.opencontainers.image.title="kaval-collector"       org.opencontainers.image.version="$VERSION"

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

ENTRYPOINT ["python", "-m", "kaval_collector.synthetic"]
CMD ["--list"]
