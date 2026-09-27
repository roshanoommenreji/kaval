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
# linux/arm64 always (CLAUDE.md constraint 2).

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY services/shared/kaval_shared services/shared/kaval_shared
COPY services/gateway/kaval_gateway services/gateway/kaval_gateway
COPY services/collector/kaval_collector services/collector/kaval_collector
RUN pip install .

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

ENTRYPOINT ["python", "-m", "kaval_collector.synthetic"]
CMD ["--list"]
