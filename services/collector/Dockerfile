# syntax=docker/dockerfile:1
#
# Collector image. Two entrypoints live here:
#   make signals SCENARIO=oom-crashloop         # fake incidents (Phase 1), one-shot
#   python -m kaval_collector.prometheus --every 30   # Prometheus (KAV-75), same idea, own Deployment
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
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv
FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39 AS lock
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --extra collector -o /requirements.txt \
 && uv export --frozen --only-group build --no-emit-project -o /build-requirements.txt

# ── Stage 1b: Kaval's own code, packed into one wheel (KAV-111, ADR-0086, Lab 88) ──────────
# The packing tool (hatchling) and its helpers come from uv.lock, every file hash-checked, and
# the build is told not to fetch anything else (--no-build-isolation, --no-deps). Before this
# stage `pip install .` let pip download the tool unchecked on every build. The runtime image
# below receives only the finished wheel, so it never holds the tool or a second copy of the code.
FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39 AS wheel
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY --from=lock /build-requirements.txt /tmp/build-requirements.txt
RUN pip install --require-hashes -r /tmp/build-requirements.txt
# pyproject.toml lists every service package, so all of them must be present for the build.
COPY pyproject.toml ./
COPY services/shared/kaval_shared services/shared/kaval_shared
COPY services/gateway/kaval_gateway services/gateway/kaval_gateway
COPY services/collector/kaval_collector services/collector/kaval_collector
COPY services/agent/kaval_agent services/agent/kaval_agent
RUN pip wheel --no-deps --no-build-isolation --wheel-dir /wheels .

# ── Stage 2: the runtime image ──────────────────────────────────────────────
FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

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

# Same pattern again: Debian shipped 3.0.22-1~deb12u2 for libssl3 (CVE-2026-84782, HIGH: information
# disclosure via DTLS handshake retransmission) after this digest was pinned; Trivy flagged all five
# images on 2026-10-26. A new instruction, so a cached layer cannot hide it.
RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade libssl3  && rm -rf /var/lib/apt/lists/*

# Same pattern once more: Debian shipped apt and libapt-pkg6.0 2.6.2 for CVE-2026-109518 (HIGH) after this
# digest was pinned; Trivy flagged all five images on 2026-11-05. Another new instruction, so a cached
# layer cannot hide it.
RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade apt libapt-pkg6.0 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY --from=lock /requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Our own code, as the finished wheel from stage 1b (KAV-111).
COPY --from=wheel /wheels /wheels
# Then drop the packaging tools the base image ships: nothing runs them after this line,
# and setuptools vendors old copies of jaraco.context and wheel that Trivy flags HIGH
# (CVE-2026-23949, CVE-2026-24049; found by the first CI run, KAV-24). pip goes too: the
# running service never installs anything, and a shell in the container gets no installer.
RUN pip install --no-deps /wheels/*.whl \
 && rm -rf /wheels \
 && pip uninstall -y setuptools wheel pip

# The component version, passed in by CI from kaval_collector.__version__ (ADR-0013). A local build
# without it says "unset" rather than guess, and CI checks the label matches the code.
ARG VERSION=unset
LABEL org.opencontainers.image.title="kaval-collector"       org.opencontainers.image.version="$VERSION"

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

ENTRYPOINT ["python", "-m", "kaval_collector.synthetic"]
CMD ["--list"]
