# Restore drill — 2026-11-08

Made by `scripts/ops/restore-drill.sh` ([runbook](../runbooks/restore-drill.md),
[ADR-0049](../adr/0049-backups-watched-and-restores-drilled.md)). Production was only **read**; the
restore went into a throwaway Postgres on this machine, which is deleted afterwards.

Result: **FAIL**

- the newest dump is more than a week old, so this drill proves little about last night's backup

| | |
|---|---|
| Dump restored | `postgres/kaval-20261027T193005Z.dump` (0.3 MB) |
| Dump taken | 2026-10-27 19:30 UTC |
| Drill run | 2026-11-08 15:38 UTC |
| **Data that would have been lost** (dump age at the drill) | **284 h 8 min** (target: at most 24 h 0 min) |
| Download | 3 s |
| Strict restore, no errors tolerated | 1 s, clean |
| Replace the database server (not measured here: Lab 27, `make staging-up`) | about 5 min 0 s |
| **Estimated recovery time** | **5 min 5 s** (target: at most 30 min 0 s) |
| Schema version in the dump | `d4f7a2c9e815`, this repository's newest |

Rows found after the restore:

| Table | Rows |
|---|---|
| `signal` | 101 |
| `incident` | 2 |
| `incident_signal` | 8 |
| `proposal` | 1 |
| `action` | 1 |
| `decision` | 0 |
| `execution` | 0 |
| `outcome` | 0 |
| `alembic_version` | 1 |
| `notification` | 5 |
