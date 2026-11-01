# Lab 70 — The first install over the chart's own database no longer waits on itself

**Phase:** 7 follow-up · **Story:** `KAV-104` · **Time:** about 30 minutes for the part done on the laptop · **Cost:** $0 for this part; about $1 for the live proof (a lab session, started only on Roshan's go)

[Lab 66](lab-66-the-eks-lab-live-two-sessions-and-what-they-found.md) found that the chart cannot install over its own database without a workaround: the migration ran after the services were ready, and the services cannot be ready without it. This lab fixes the chart. The decision is [ADR-0070](../adr/0070-the-first-install-over-the-charts-own-database-no-longer-waits-on-itself.md).

## Prerequisites

- `helm` on the laptop, the project's Python environment (`make sync`)

## Part 1 — See the old behaviour

On `main`, before the change:

```bash
helm template kaval-lab-eks deploy/charts/kaval -f deploy/environments/lab-eks/values.yaml | grep -n "helm.sh/hook"
```

You see `"helm.sh/hook": post-install,pre-upgrade` on the migration Job. That is the hook Helm runs only after the services are ready.

## Part 2 — The change

`deploy/charts/kaval/templates/migrate-job.yaml`: the annotations are written only when it is **not** (the chart's own Postgres **and** a first install):

```yaml
{{- if not (and .Values.postgres.enabled .Release.IsInstall) }}
annotations: ...
{{- end }}
```

`deploy/gitops/lab-eks/helmrelease.yaml`: `install.disableWait: true` is removed.

## Part 3 — Prove it renders the way the decision says

Render every environment before and after (`helm template`, and again with `--is-upgrade`) and compare:

| | First install | Upgrade |
|---|---|---|
| staging, production | identical to before, byte for byte | identical |
| lab, local | the migration Job has no hook annotations; nothing else changed | identical |

Then run the tests: `python -m pytest scripts/dev/test_chart_migration.py scripts/dev/test_lab_eks.py scripts/dev/test_migration_rules.py`. The new file covers the three cases (own server: `pre-install`; own Postgres, first install: no hook; own Postgres, upgrade: still a hook). The whole `scripts/dev` suite passed (263 tests).

## Part 4 — The live proof (not done yet)

First the lab was moved to the build production runs (`sha-1d38be1`, pinned in `deploy/environments/lab-eks/values.yaml` and the lab's release). It was still on `sha-eb3f81a`, from before the two health pages (`/livez`, `/readyz`) that the chart now checks, so its gateway would never have become ready and the session would have failed for an unrelated reason. Found by reading, before any money was spent.

One more EKS lab session, following [the lab runbook](../runbooks/lab-eks.md) exactly, with the release **without** `disableWait`. It passes if the release becomes Ready on its first try (no "removed and retried"), `kubectl -n kaval-lab get jobs` shows the `-migrate-1` Job complete, and all pods end ready. Each live step needs Roshan's own go. This part is written up here when it has run.

## Done when

- [x] The chart renders as ADR-0070 says, and staging and production are unchanged (Part 3)
- [x] Tests cover the three cases and fail if `disableWait` returns
- [ ] A lab session installs the release without `disableWait` on the first try (`KAV-104` closes then)

## Cost

$0 so far. The live proof is one lab session, about $1 (two earlier sessions cost $1.60 and $0.85), and a timer deletes the lab after six hours.
