# syntax=docker/dockerfile:1
#
# A utility image, not an application service: just enough to run scripts/ops/backup.sh,
# restore.sh and db-roles.sql as Helm CronJob/hook containers (KAV-32). kaval_backup/
# __init__.py carries nothing but __version__, copied in (not pip-installed) only so CI's
# version-label check (ADR-0013) has the same thing to check here as on every other image;
# ci.yml still skips the generic kaval_shared/alembic import check for this one entry, since
# there's no real Python package underneath.
#
# linux/arm64 always (CLAUDE.md constraint 2). Base pinned by digest, same as the gateway
# image -- Dependabot bumps both together.

FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

# --only-upgrade libpcre2-8-0: Debian shipped a fix for CVE-2026-103111 (HIGH) after this
# digest was pinned; folded into this same apt-get since it's the only one in this image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade libpcre2-8-0 \
 && apt-get install -y --no-install-recommends postgresql-client awscli \
 && rm -rf /var/lib/apt/lists/*

# Same CVE the gateway image hit first (CVE-2026-23949, HIGH): the base image's bundled
# setuptools vendors an old jaraco.context. Nothing in this image ever installs a package
# after this point, so there's no installer to lose.
RUN pip uninstall -y setuptools wheel pip

WORKDIR /app
COPY scripts/ops/backup.sh scripts/ops/restore.sh scripts/ops/db-roles.sql scripts/ops/anonymise.sql scripts/ops/
RUN chmod +x scripts/ops/backup.sh scripts/ops/restore.sh
# Not pip-installed -- there's nothing else to this "package" but __version__, kept only so
# CI's generic per-image checks (ADR-0013's version label, the import smoke test) have the
# same thing to check here as on every other service image.
COPY services/backup/kaval_backup kaval_backup

ARG VERSION=unset
LABEL org.opencontainers.image.title="kaval-backup" \
      org.opencontainers.image.version="$VERSION"

RUN useradd --system --uid 10001 --no-create-home kaval
USER kaval

CMD ["scripts/ops/backup.sh"]
