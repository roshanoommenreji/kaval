# syntax=docker/dockerfile:1
#
# Executor image. The only component that mutates anything (CLAUDE.md constraint 3, KAV-47).
#   make execute                                      # one pass
#   python -m kaval_executor.executor --every 15       # a loop, as deployed
#
# Unlike the agent's Dockerfile, this one DOES install its own `executor` extra
# (`kubernetes`): the deployed loop calls the Kubernetes API every pass, it isn't a
# laptop-only tool the way agent's httpx/boto3 are. It also carries `opa` and `policy/`,
# same as the agent's image and for the same reason — `kaval_executor.executor` imports
# `kaval_agent.policy` to run the identical second policy check immediately before acting
# (ADR-0006 rule 3, `policy/README.md`'s "evaluated twice"), so the whole project — shared,
# gateway, collector, agent, executor — is bundled into every image, same as the agent's.
#
# No FastAPI, no uvicorn, no Anthropic/Bedrock client: those stay out, same reasoning as the
# agent's own image for its unused extras.
#
# linux/arm64 always (CLAUDE.md constraint 2). Base images pinned by digest (KAV-24).

# ── Stage 1: uv.lock → hash-pinned requirements, core deps + the executor extra only ───────
FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv
FROM python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89 AS lock
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --extra executor -o /requirements.txt

# ── Stage 1b: the opa binary, checksum-verified, same source as the agent's image ──────────
FROM python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89 AS opa
ARG OPA_VERSION=1.21.0
ARG OPA_SHA256=0ec34027c15b4d969c21d01ed570fe14fbebd508a08157043ab09f9a0dccbee6
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSLo /opa \
      "https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/opa_linux_arm64_static" \
 && echo "${OPA_SHA256}  /opa" | sha256sum -c - \
 && chmod +x /opa

# ── Stage 2: the runtime image ──────────────────────────────────────────────
FROM python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89

# OS-level patch, independent of uv.lock: Debian shipped a fix for CVE-2026-103111
# (libpcre2-8-0, HIGH) after this digest was pinned. Trivy flags the base image's own
# package, so the fix has to land here rather than in the Python dependency lock.
RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade libpcre2-8-0 \
 && rm -rf /var/lib/apt/lists/*

# Same pattern, separate CVE: Debian shipped 5.36.0-7+deb12u4 for a CRITICAL perl regex
# flaw (CVE-2026-13221) plus several perl-Archive-Tar/IO-Compress/Storable CVEs fixed in the
# same update. perl-base and libperl5.36 are listed because Trivy flags whichever of the two
# is actually present per image; --only-upgrade skips either one cleanly if it isn't installed.
RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade perl-base libperl5.36 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KAVAL_POLICY_DIR=/app/policy

WORKDIR /app

COPY --from=lock /requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY pyproject.toml ./
COPY services/shared/kaval_shared services/shared/kaval_shared
COPY services/gateway/kaval_gateway services/gateway/kaval_gateway
COPY services/collector/kaval_collector services/collector/kaval_collector
COPY services/agent/kaval_agent services/agent/kaval_agent
COPY services/executor/kaval_executor services/executor/kaval_executor
COPY --from=opa /opa /usr/local/bin/opa
COPY policy policy
# Same housekeeping as the agent's image, same reasoning (KAV-24, Trivy HIGH findings on
# setuptools' vendored jaraco.context/wheel): nothing runs these after this line.
RUN pip install --no-deps . \
 && pip uninstall -y setuptools wheel pip

# The component version, passed in by CI from kaval_executor.__version__ (ADR-0013).
ARG VERSION=unset
LABEL org.opencontainers.image.title="kaval-executor"       org.opencontainers.image.version="$VERSION"

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

ENTRYPOINT ["python", "-m", "kaval_executor.executor"]
