# ADR-0070 — The first install over the chart's own database no longer waits on itself

- **Status:** Accepted (plan approved by Roshan 2026-11-01). Built and tested locally; the live proof is one more EKS lab session, which needs its own go. Until it has run, `KAV-104` stays open
- **Date:** 2026-11-01
- **Deciders:** Roshan
- **Story:** `KAV-104`, found live on 2026-10-30 in [Lab 66](../labs/lab-66-the-eks-lab-live-two-sessions-and-what-they-found.md) · amends [ADR-0067](0067-the-eks-lab-is-ephemeral-has-its-own-timer-and-is-proven-clean-by-a-label-sweep.md) (item 4 of its amendment) and builds on [ADR-0057](0057-the-database-is-brought-up-to-date-before-a-new-version-starts.md)

## Context

When the chart carries its own Postgres (the EKS lab, the local k3d cluster, CI), the migration that creates the tables was a Helm `post-install` hook. Helm runs that hook only **after every Deployment is ready**, and the agent, executor and collector cannot be ready before the tables exist. The install waited for the services, the services waited for the tables, the tables waited for the hook. Flux gave up after five minutes, removed the release and tried again, forever. The lab got past it with `install.disableWait: true` in its own release, which hides the wait for every other failure too.

Staging and production never met this: their database is a separate server, and the migration is a `pre-install` hook that runs before anything else (ADR-0057).

## Decision

**On a first install with the chart's own Postgres, the migration Job carries no hook annotation: it is an ordinary resource of the release, created together with the Deployments.** The Job keeps its `wait-for-postgres` init container, so it waits for the database, runs `alembic upgrade head` and finishes. The services retry until the tables exist and come up. Helm's wait looks at Deployments, not at this Job, so nothing in the chain waits on something that waits on it.

The condition is one line in `templates/migrate-job.yaml`: `postgres.enabled` **and** `.Release.IsInstall`. Every upgrade is a hook again (`pre-upgrade`); the first install's Job drops out of the next revision's manifest, and its name carries the revision, so nothing collides. The lab's `disableWait` is removed in the same change, and a test now fails if it comes back.

## What real companies do, and why this one

1. **Run the migration as an ordinary Job beside the services** (or a service-side wait for the schema): chosen. The smallest change, one condition, no new moving part.
2. **Make the database itself a "run first" hook** so the migration can be `pre-install`. Heavy: the database, its storage and its Secret would all become hook resources with their own delete rules.
3. **Keep the wait switched off** where the chart carries its own database. That was the workaround; it stops the installer from noticing a service that never starts.
4. **Put the migration in each service's start-up.** Several copies would race on the same tables, which is the problem a single Job exists to avoid.

## Consequences

- **Staging and production are unchanged.** The rendered output of both is byte-for-byte what it was (checked by rendering before and after, for a first install and for an upgrade). The only rendered difference anywhere is the lab's and the local cluster's first-install Job losing its hook annotations.
- **The services may restart while the tables are being made.** They crash and retry for the minute the migration takes; this is what the lab already did after its workaround, and it is the same shape as any service that starts before its database is ready.
- **A failed migration is now visible in the install.** With the hook, a failed migration never started. Now the services stay not ready, so the install times out and Flux's retry shows it. That is the intended direction: the wait is back on.
- **Honest limit:** the first-install behaviour is proven by rendering and by tests; that it removes the deadlock on a real cluster is proven only by the lab session. Until then `KAV-104` is open.

## Testing

`scripts/dev/test_chart_migration.py` renders the real chart: staging and production keep their `pre-install` hook; the lab and local first install have no hook and still wait for the database before migrating; the same environments on an upgrade are still a hook (the negative case). `test_lab_eks.py` fails if `disableWait` returns. `test_migration_rules.py`'s old first-install check now covers the upgrade.
