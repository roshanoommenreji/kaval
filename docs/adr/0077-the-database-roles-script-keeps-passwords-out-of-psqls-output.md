# ADR-0077 — The database-roles script keeps passwords out of psql's output

- **Status:** Accepted (plan shown to Roshan 2026-11-04, who said "go"). Merged as #313 on 2026-11-04 with all 11 checks green; **proved on staging on 2026-11-05** (see "Proof on staging" below). Production's bring-up, which needs nothing deployed, is still to come
- **Date:** 2026-11-04
- **Deciders:** Roshan
- **Story:** `KAV-125` (found while reading staging's boot journal for `KAV-108`; evidence in [Lab 77](../labs/lab-77-passwords-that-stay-out-of-the-logs.md); threat model row T-OPS-4)

## Context

`scripts/ops/db-roles.sql` creates the four per-service database logins (gateway, agent, executor, collector). The passwords come in as `psql` variables (`-v gateway_password=...`), and the file hands each one to the server with `SELECT set_config('kaval.gateway_password', :'gateway_password', false)`. It has to go through `set_config` because `psql`'s `:'var'` substitution does not reach inside a `DO $$ ... $$` body (the file's header explains; found in `KAV-56`). The `DO` blocks then read the passwords back with `current_setting()`.

`psql` prints the result of a `SELECT`, and here the result **is** the password. So every run of the file printed all four passwords, in plain text, to whatever was collecting the node's output.

**Measured on staging on 2026-11-04** by reading the logs, counting and never printing: each of the four passwords is in `/var/log/cloud-init-output.log` once (mode 0640, root and the `adm` group) and in the journal (kept on disk) three times. The admin password is nowhere. The file ran at first boot, again in the permissions pass about 20 seconds later (`KAV-85`), and runs every six hours after, so a long-lived node writes more.

**Who could see it.** Root and the `adm` group on the node, which in practice means anyone with a Session Manager session who can use `sudo`: the same people who can read the passwords from Parameter Store. Nothing ships these logs anywhere (no log shipper in the node template). Staging's passwords vanished with staging. The template and the script also serve production, so production's four passwords were printed the same way whenever its app server booted; its app server is not running now (only its stopped database server exists), so those logs went with it.

**Why it still matters.** A log is the first thing a person, or a future log-reading feature, copies and shares, and a password in one outlives its purpose. The project's own rule is that secrets stay out of anything that is read casually.

## Decision

1. **`\gset` on the four statements.** `SELECT set_config(...) AS discarded \gset` runs the query and keeps the result in a `psql` variable instead of printing it. The passwords reach the `DO` blocks exactly as before; errors still reach stderr and `ON_ERROR_STOP` still stops the file. The fix is in the script, the one place every caller (the node's bootstrap, a manual run) goes through.
2. **Three tests, in `services/shared/tests/test_db_roles_output.py`.** They run the **real `psql`** against Postgres in a transaction that is rolled back: (a) a control, so "no password found" cannot mean "the capture sees nothing"; (b) none of four fake passwords appears in anything `psql` prints; (c) each role's stored password fingerprint (SCRAM) matches the fake password it was given, so hiding the output did not change what reaches the roles. A fourth, needing no database, fails if any line that passes a password lacks `\gset`. Seen failing first: with today's script the leak test reported all four passwords and the static test lines 22 to 25. In CI, `KAVAL_REQUIRE_DB=1` turns a missing database or a missing `psql` into a **failure**, never a skip.
3. **The older role test no longer hides a broken file.** `test_db_roles.py` applies the same file through the Python driver and turned any exception into a skip. The new `\gset` is a `psql` command the driver cannot run, so its five tests **skipped silently** after the fix: caught locally, not by CI. It now translates the `\gset` and skips only for "this database user may not create roles" (SQLSTATE 42501); any other error fails.
4. **The fix reaches production with no infrastructure change.** The node downloads `db-roles.sql` from `main` when it boots (the same trust as `backup.sh`, [ADR-0038](0038-nightly-dump-is-taken-by-the-app-node.md)), so once merged production has it at its next bring-up. For the same reason the staging proof can only run after the merge.
5. **Recommendation, for Roshan to confirm: do not rotate production's four passwords.** The exposure was a root-and-`adm`-readable log on a server that no longer exists, with no log shipping, and the database accepts connections only from the app node's security group. Rotating (`terraform apply -replace` on the four `random_password` resources) is cheap and available if he prefers it at the next bring-up; the plan would show it.

## Alternatives considered

- **`\o /dev/null` around the statements.** Works on Linux but names a Unix path; `\gset` is portable and says what is meant (keep it, do not show it).
- **Scrub the logs afterwards.** Does not stop the print, and a journal cannot be edited.
- **`-q`, `-t` or `-o /dev/null` on each caller.** Every caller would have to remember it; the script is the one place.
- **Rewrite with `\getenv` and `\gexec`, as `infra/modules/database/db-monitoring.sh` does** (`SELECT format('CREATE ROLE ... PASSWORD %L', :'pw') ... \gexec`). It prints nothing and drops the `set_config` detour, and it keeps the password off the command line. A bigger change to a file with four roles and two branches each; a possible later cleanup, not needed to stop the print.
- **Bind parameters (`\bind`, psql 16 and later) or pre-computed password fingerprints.** They keep the literal out of the statement text altogether; out of proportion for a story about printing.

## Noticed, not changed

- The node's bootstrap passes the passwords as `-v name=value` arguments, so they are visible in the process list for the moments `psql` runs (a root-only server). `db-monitoring.sh` already uses the environment and `\getenv` for the same reason. A later hardening, not part of this story.
- The statement text, with the literal password, reaches the database server. Its own statement log is off: the database module sets no `log_statement` (only `log_connections`), and PostgreSQL's default is `none`. Read live during the staging proof of 2026-11-05: `log_statement = none`.
- Searched the scripts and the infrastructure for any other place that prints a password variable: none (the rest pass passwords through the environment or a root-only file).

## Consequences

- The threat model gains row T-OPS-4. It read "built, staging proof pending" until the proof; after the proof of 2026-11-05 it reads "Partly" (proved on staging), and it becomes "Covered" when production's bring-up shows the same zero.
- `KAV-125`'s criteria are ticked after the merge and the staging proof (zero occurrences of each password in the node's boot log and journal, counted without printing), then production's bring-up. That proof shared one staging session with `KAV-126`; `KAV-124` needed none.
- Cost: $0 for the build; the proof shared a staging session, whose figure is in the journal of 2026-11-05.

## Proof on staging, 2026-11-05

After Roshan's "go", staging was built from nothing (93 resources, 22:02 to 22:08 IST) and its node downloaded the merged `db-roles.sql` from `main` while booting. A read-only script then ran on the node over Session Manager. It read each password from the parameter store and counted it in the two logs, printing counts only:

| Password | Boot log (275 lines) | Journal (4,855 lines) | Control: a planted copy |
|---|---|---|---|
| gateway | 0 | 0 | 1 |
| agent | 0 | 0 | 1 |
| executor | 0 | 0 | 1 |
| collector | 0 | 0 | 1 |
| admin | 0 | 0 | 1 |

Before the fix the four service passwords counted 1 and 3 (2026-11-04). The control is a file holding one copy of the password, counted with the same `grep`: it shows the method finds a password where one exists, so a zero cannot come from an empty search or a broken command. `SHOW log_statement` read `none`. The same session ran `make staging-smoke` (7 of 7 passed for `sha-b790720`) and then `make staging-down`.

**Not proven here: production.** Its app server runs the same `db-roles.sql` from `main` at its next bring-up. The same count with `/kaval/kaval-prod/db/...` is the proof; the plan needs no infrastructure change.
