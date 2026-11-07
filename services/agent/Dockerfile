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
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS lock
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --extra diagnoser -o /requirements.txt

# ── Stage 1b: the opa binary, built from OPA's release commit (KAV-132, ADR-0079, Lab 80) ────
# Built here instead of downloaded, so that a fix for a scanner finding in one of OPA's libraries reaches the image on the
# day it exists, not with OPA's next release 10 to 14 days later. The executor's Dockerfile carries the identical stage and
# scripts/dev/test_opa_build_pins.py fails if the two ever differ. Every input is pinned and checked:
#   - the Go toolchain: the digest on the FROM line (Dependabot proposes new digests; golang minor and major bumps are ignored);
#   - the source: OPA_COMMIT, the commit the release tag v${OPA_VERSION} points at. The stage stops if anything else is checked
#     out, then tags the checkout: without the tag Go stamps a made-up version and scanners report OPA itself as old;
#   - the overrides: OPA_GO_GET, libraries raised above the versions in OPA's own go.mod (Go's checksum database verifies them).
#     The stage stops if one is missing from the finished binary. Delete an entry when OPA's go.mod catches up:
#       golang.org/x/net@v0.62.0  CVE-2026-78661 (HTML parser); OPA v1.22.2 and main still use v0.60.0 (read 2026-11-07);
#   - the result: OPA_SHA256_ARM64, checked in its own step after the compile, so a changed value never recompiles. Builds are
#     byte-reproducible, so a changed value means an input changed; the failing build prints the new one.
# The images are linux/arm64 only (CLAUDE.md constraint 2). This stage runs on the build machine and cross-compiles: native on
# CI's arm64 runner, fast on a laptop. ci.yml installs OPA's own linux_amd64 release for `opa test` and the pytest suite; that
# file is neither shipped nor scanned.
FROM --platform=$BUILDPLATFORM golang:1.27.3-trixie@sha256:af1bfc2614ebb1dbd31af514b78740fd678850e76498762b425ecee4c5c6ea80 AS opa
ARG TARGETARCH
ARG OPA_VERSION=1.21.4
ARG OPA_COMMIT=05c845177c80747a2aabb607f9318f981993cc76
ARG OPA_GO_GET="golang.org/x/net@v0.62.0"
ENV GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org CGO_ENABLED=0 GOOS=linux GOFLAGS=-buildmode=exe
WORKDIR /src
RUN set -eu; \
    test "${TARGETARCH}" = arm64 || { echo "the images are linux/arm64 only: build with --platform linux/arm64"; exit 1; }; \
    git init -q; \
    git fetch -q --depth 1 https://github.com/open-policy-agent/opa.git "${OPA_COMMIT}"; \
    git checkout -q --detach FETCH_HEAD; \
    test "$(git rev-parse HEAD)" = "${OPA_COMMIT}"; \
    git tag "v${OPA_VERSION}"; \
    go get ${OPA_GO_GET}; \
    GOARCH="${TARGETARCH}" go build -trimpath -tags=opa_wasm -o /opa .; \
    go clean -cache -modcache
ARG OPA_SHA256_ARM64=f6850f6c6b1335f62918bdb281f627a1b029ae16ecf8811e2ccefa174c228ba3
RUN set -eu; \
    for spec in ${OPA_GO_GET}; do \
      mod="${spec%@*}"; ver="${spec#*@}"; \
      go version -m /opa | grep -Eq "[[:space:]]${mod}[[:space:]]+${ver}[[:space:]]" || { echo "override not applied: ${spec}"; exit 1; }; \
    done; \
    got="$(sha256sum /opa | cut -d' ' -f1)"; \
    test "${got}" = "${OPA_SHA256_ARM64}" || { echo "opa checksum differs: expected ${OPA_SHA256_ARM64}, built ${got}"; exit 1; }; \
    echo "opa ${OPA_VERSION} built from ${OPA_COMMIT}, overrides applied, sha256 ${got}"

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

# Same pattern once more: Debian shipped apt and libapt-pkg6.0 2.6.2 for CVE-2026-109518 (HIGH) after this
# digest was pinned; Trivy flagged all five images on 2026-11-05. Another new instruction, so a cached
# layer cannot hide it.
RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade apt libapt-pkg6.0 \
 && rm -rf /var/lib/apt/lists/*

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
