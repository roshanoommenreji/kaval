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
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS lock
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --extra executor -o /requirements.txt \
 && uv export --frozen --only-group build --no-emit-project -o /build-requirements.txt

# ── Stage 1b: the opa binary, built from OPA's release commit (KAV-132, ADR-0079, Lab 80) ────
# The identical stage to the agent's image: see services/agent/Dockerfile for why it is built here and for what each pin
# means. scripts/dev/test_opa_build_pins.py fails if the two stages ever differ.
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

# ── Stage 1c: Kaval's own code, packed into one wheel (KAV-111, ADR-0086, Lab 88) ──────────
# The packing tool (hatchling) and its helpers come from uv.lock, every file hash-checked, and
# the build is told not to fetch anything else (--no-build-isolation, --no-deps). Before this
# stage `pip install .` let pip download the tool unchecked on every build. The runtime image
# below receives only the finished wheel, so it never holds the tool or a second copy of the code.
FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS wheel
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
COPY services/executor/kaval_executor services/executor/kaval_executor
RUN pip wheel --no-deps --no-build-isolation --wheel-dir /wheels .

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
    KAVAL_POLICY_DIR=/app/policy

WORKDIR /app

COPY --from=lock /requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Our own code, as the finished wheel from stage 1c (KAV-111).
COPY --from=wheel /wheels /wheels
COPY --from=opa /opa /usr/local/bin/opa
COPY policy policy
# Same housekeeping as the agent's image, same reasoning (KAV-24, Trivy HIGH findings on
# setuptools' vendored jaraco.context/wheel): nothing runs these after this line.
RUN pip install --no-deps /wheels/*.whl \
 && rm -rf /wheels \
 && pip uninstall -y setuptools wheel pip

# The component version, passed in by CI from kaval_executor.__version__ (ADR-0013).
ARG VERSION=unset
LABEL org.opencontainers.image.title="kaval-executor"       org.opencontainers.image.version="$VERSION"

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

ENTRYPOINT ["python", "-m", "kaval_executor.executor"]
