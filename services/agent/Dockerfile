# syntax=docker/dockerfile:1
#
# Agent image. Its entrypoint runs correlation (KAV-39): signals in, incident rows out.
#   make correlate                                   # one pass
#   python -m kaval_agent.correlate --every 30       # a loop, for when it's deployed
# Context assembly (KAV-40) and diagnosis (KAV-41, ADR-0016) ship in this image too — the
# whole kaval_agent package is copied below. They run two ways: as laptop-invoked tools
# (`make index-runbooks`, `make context`, `make diagnose`; Lab 10, Lab 11), and, since KAV-87
# (ADR-0055), unattended in the cluster as the *diagnoser* Deployment, which runs
#   python -m kaval_agent.diagnoser --every 30
# from this same image (a different command, not a different image). That is why httpx is
# installed here (the `diagnoser` extra) and docs/runbooks is copied in.
# Build context is the REPO ROOT, like the other images (compose.yaml sets it), and the
# root .dockerignore allowlist decides what leaves the laptop.
#
# Core dependencies only: no FastAPI, no uvicorn, no Kubernetes or AWS client. The agent
# reads the database and writes its own rows; it has no library to change anything else
# with, which is the privilege split (CLAUDE.md constraint 3) showing up in the image.
#
# It also carries the `opa` binary and policy/ (KAV-42, ADR-0017): kaval_agent.policy shells
# out to `opa eval` to classify every action, so both need to be inside the image the same
# entrypoint loop actually runs from — unlike diagnose.py's httpx, this one isn't optional.
# KAVAL_POLICY_DIR points at where policy/ lands below (/app/policy): hatchling's wheel build
# installs kaval_agent flattened into site-packages, so __file__-relative guessing at runtime
# resolves to the wrong place inside a built image — found the hard way, by a CI failure, not
# guessed. See kaval_agent.policy's module docstring.
#
# linux/arm64 always (CLAUDE.md constraint 2). Base images pinned by digest (KAV-24).

# ── Stage 1: uv.lock → hash-pinned requirements (core dependencies only) ─────
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS lock
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --extra diagnoser -o /requirements.txt

# ── Stage 1b: the opa binary, downloaded and checksum-verified like gitleaks/Trivy in CI ────
# (ci.yml installs the linux_amd64 build the same way, for the runner that runs `opa test`
# and the pytest suite; this is the arm64 build, for the image that actually runs on Graviton.)
FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS opa
ARG OPA_VERSION=1.21.4
ARG OPA_SHA256=810aa96e44f1f852d044888cf830324e44eafd0f510345339011347ab4deaab1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSLo /opa \
      "https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/opa_linux_arm64_static" \
 && echo "${OPA_SHA256}  /opa" | sha256sum -c - \
 && chmod +x /opa

# ── Stage 2: the runtime image ──────────────────────────────────────────────
FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf

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

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KAVAL_POLICY_DIR=/app/policy \
    KAVAL_RUNBOOKS_DIR=/app/runbooks

WORKDIR /app

COPY --from=lock /requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY pyproject.toml ./
COPY services/shared/kaval_shared services/shared/kaval_shared
COPY services/gateway/kaval_gateway services/gateway/kaval_gateway
COPY services/collector/kaval_collector services/collector/kaval_collector
COPY services/agent/kaval_agent services/agent/kaval_agent
COPY --from=opa /opa /usr/local/bin/opa
COPY policy policy
# The runbooks the diagnosis worker indexes at start (KAV-87, ADR-0055): versioned with the image,
# so the model reads the same runbooks the people did at that release.
COPY docs/runbooks runbooks
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
