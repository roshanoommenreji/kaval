# Disaster recovery: what is kept, how much can be lost, how long to get back

Written for `KAV-82` ([ADR-0049](../adr/0049-backups-watched-and-restores-drilled.md)). Two words first:

- **RPO** (recovery point): how much *recent work* could be lost. "24 hours" means up to a day of new
  data may be gone.
- **RTO** (recovery time): how long until it works again after you decide to restore.

Every number below says where it came from. A number nobody has measured is marked **not measured**.

## The database (the only store that matters)

Production's Postgres runs on its own server with its data on a separate disk
([ADR-0008](../adr/0008-production-database-on-its-own-server.md)). It has two independent copies, which
protect against different things.

| Copy | Protects against | Taken | Kept | Recovery point (RPO) | Proven |
|---|---|---|---|---|---|
| **Disk snapshots** | the disk or its filesystem being damaged | daily at 03:00 UTC, **and one before every stop** | 7 daily | at most 24 h while running; the moment of the stop for a planned park | Lab 27, live: **3 min 51 s** to restore a real snapshot into production |
| **Nightly dump** (a logical copy, in S3) | a bad change or a wrong command that the disk faithfully kept | 19:30 UTC, by a timer on the app server, **only on nights it is running** | 14 days | since the last 19:30 at which the app server was up (a parked production takes none) | `make restore-drill`, 2026-10-13: restored cleanly in **1 s** (production held no data yet: mechanics only) |

**Stated objectives for the database: RPO 24 hours, RTO 30 minutes.** The 24 hours is the disk
snapshot's. It is *not* true of the dump, which can be days old when production is parked; the drill
record says so whenever the dump is older than a day, and the daily check ([ADR-0049](../adr/0049-backups-watched-and-restores-drilled.md))
alerts if a dump is missing for a night the server was running.

Recovery time, from the pieces that were measured:

| Step | Time | From |
|---|---|---|
| Replace the database server and attach the restored disk | about 4 to 5 min | Lab 27 (3 min 51 s), `make staging-up` |
| Restore a dump into it | seconds for today's size (24 KB); grows with the data | `make restore-drill` |
| A person deciding and typing | the unknown part | game days: 74 s, 74 s, 273 s |
| **Total, worst case taken from those** | **about 10 minutes**, against the 30-minute target | |

## Everything else

| Store | Where it is | How it comes back | Recovery point | Recovery time |
|---|---|---|---|---|
| **The app server** (k3s, the services) | nothing on it that is not in git or SSM | the auto-scaling group replaces it and Flux deploys from git | none: stateless | about 2 min 10 s, live (Lab 47) |
| **Secrets** (database passwords, Slack tokens) | SSM Parameter Store, recreated onto the server at every boot | the same boot | none | included above |
| **Code and settings** | git, on GitHub | clone | the last commit | minutes |
| **Container images** | ECR, newest 15 per repository kept | rebuild from git (`release.yml`) | none: rebuildable | **not measured** |
| **Staging** | disposable | `make staging-up` | none | about 5 min |
| **Terraform state** (what it has built) | a private, versioned S3 bucket, one object per environment, locked by S3 ([ADR-0058](../adr/0058-terraforms-record-lives-in-a-private-versioned-bucket.md)) | a new laptop runs `scripts/ops/tf-init.sh <env>`; a damaged record is replaced from an earlier version ([runbook](../runbooks/terraform-state.md)) | the last apply; older versions kept at least 90 days | restoring an earlier version: one command and a plan, tried on staging 2026-10-16; a whole new laptop **not timed** |

## The two gaps this writing-down found

1. **Terraform's state lived only on the laptop.** *Closed 2026-10-16 (`KAV-86`):* a remote copy, an S3 bucket
   with versioning and a lock, now holds it ([ADR-0058](../adr/0058-terraforms-record-lives-in-a-private-versioned-bucket.md)).
   What the record alone does not bring back is each environment's gitignored `terraform.tfvars` (alert email,
   budget numbers, SSH public key); the runbook says where each comes from. Not timed: a whole new laptop.
2. **The dump skips nights production is parked.** Harmless while the disk snapshots exist, but it means
   the "RPO 24 h" in older documents ([ADR-0005](../adr/0005-data-durability-and-staging-seeding.md)) is
   the snapshots' promise, not the dump's. A dump taken at the moment production is parked would close
   it; not built.

## Drilling it

`make restore-drill` once a month (the daily check complains after 35 days) restores the newest dump and
writes `docs/drills/restore-<date>.md`. A disk-snapshot restore into a *new* server is Lab 27's drill; it
was run once, live, and is repeated implicitly by every `make staging-up` for the server-replacement
part. A full drill with real data waits until production holds real data.
