# Lab 70 — The first install over the chart's own database no longer waits on itself

**Phase:** 7 follow-up · **Story:** `KAV-104` · **Time:** about 30 minutes on the laptop plus a one-hour lab session · **Cost:** $0 for the laptop part; about $0.20 for the live proof (a one-hour lab session)

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

## Part 4 — The live proof (done 2026-11-01)

First the lab was moved to the build production runs (`sha-1d38be1`, pinned in `deploy/environments/lab-eks/values.yaml` and the lab's release). It was still on `sha-eb3f81a`, from before the two health pages (`/livez`, `/readyz`) that the chart now checks, so its gateway would never have become ready and the session would have failed for an unrelated reason. Found by reading, before any money was spent.

One EKS lab session, following [the lab runbook](../runbooks/lab-eks.md), each live step on Roshan's own go:

1. `make lab-plan`: 45 to add, 0 to change, 0 to destroy; Kubernetes 1.35, two `t4g.medium` On-Demand servers, access limited to the laptop's address, nothing touching production or staging. `make lab-up` built it (about 15 minutes).
2. The laptop's address had changed again (the third session in a row), so `kubectl` timed out. The fix is the one the runbook gives: update `api_allowed_cidrs`, `make lab-plan` (0 to add, 1 to change, 0 to destroy), `make lab-up`. Both servers were `Ready` and all ten system pods running.
3. Flux's installer was downloaded and its sha256 checked against the release's published checksum (it matched), then applied with the two settings ConfigMaps.
4. `kubectl apply -f deploy/gitops/lab-eks/source.yaml -f deploy/gitops/lab-eks/sync.yaml`: the test.

| What was checked | Result |
|---|---|
| The release's install settings | only the retry limit; no `disableWait` |
| How long until the release was Ready | about 83 seconds, first try |
| Install failures, upgrade failures, retries | none; one release made, ever |
| The migration Job | an ordinary Job with no hook tag, `Complete` after 24 seconds |
| Pods | 8 of 8 running and ready, 0 restarts |

In Lab 66 the same install looped forever. The fix is proven on a real cluster.

Teardown followed the runbook's order: pause Flux, delete the Ingress and the release, then AWS showed 0 load balancers and 0 lab disks (the database's disk finished deleting by itself within a minute); the destroy plan was 0 to add, 0 to change, 45 to destroy, all lab resources; the apply took about 7 minutes. `make lab-sweep` straight afterwards: *nothing left, no billable resource, no leftover* (one note: the label index still listed a disk that AWS says no longer exists, the known lag). The 24-hour sweep, run on the evening of 2026-11-02 (more than a day later): `make lab-sweep` said *nothing left, no billable resource, no leftover*, and a direct look found no server running.

## Done when

- [x] The chart renders as ADR-0070 says, and staging and production are unchanged (Part 3)
- [x] Tests cover the three cases and fail if `disableWait` returns
- [x] A lab session installs the release without `disableWait` on the first try *(2026-11-01, about 83 seconds, 8 of 8 pods ready, 0 restarts)*
- [x] The sweep 24 hours later is clean *(2026-11-02, 20:27 IST: nothing left)*

## Cost

The lab session ran for about one hour (built around 11:55 UTC, destroyed by 12:57 UTC): the control plane at $0.10 an hour plus two On-Demand `t4g.medium` servers, about $0.20 to $0.25 in all. The cost check on 2026-11-02 could not confirm the figure: Cost Explorer still marked 2026-11-01 an estimate ($0.09 so far, with no cluster line), so it stays an estimate from running hours until that day is final. The two earlier sessions cost $1.60 and $0.85.
