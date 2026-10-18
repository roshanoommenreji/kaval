#!/usr/bin/env bash
# Runs promtool's unit tests over Kaval's own-health alert rules (KAV-78, ADR-0042).
# Uses the exact Prometheus image the chart deploys, read from values.yaml, so the rules are
# tested by the version that will evaluate them. Needs Docker; nothing else is installed.
set -euo pipefail
cd "$(dirname "$0")/../.."

image=$(sed -n 's|^  image: \(quay.io/prometheus/prometheus:[^ ]*\).*|\1|p' deploy/charts/kaval/values.yaml)
[ -n "$image" ] || { echo "could not read the Prometheus image from deploy/charts/kaval/values.yaml" >&2; exit 1; }

# `pwd -W` is the Windows path under Git Bash, which Docker Desktop needs; plain `pwd` elsewhere.
here=$(pwd -W 2>/dev/null || pwd)
MSYS_NO_PATHCONV=1 docker run --rm -v "${here}/deploy/charts/kaval:/work" -w /work/tests \
  --entrypoint promtool "$image" test rules kaval-alerts.test.yaml kaval-diagnoser.test.yaml kaval-cost.test.yaml
