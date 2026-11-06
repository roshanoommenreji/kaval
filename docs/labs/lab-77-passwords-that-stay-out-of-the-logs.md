# Lab 77 — Passwords that stay out of the logs

**Phase:** 8 (hardening) · **Story:** `KAV-125` · **Time:** about 30 minutes · **Cost:** $0 for Parts 1 to 3 (a local database in Docker); the staging proof (Part 4) shares a staging session ($0.045 an hour)

Before this lab, running `scripts/ops/db-roles.sql` through `psql` printed the four service-login passwords, in plain text, to whatever collected its output: on a node that is the journal and the boot log. This lab reproduces that with fake passwords, makes it stop, and proves the roles still get the same passwords. The decision is [ADR-0077](../adr/0077-the-database-roles-script-keeps-passwords-out-of-psqls-output.md).

## Prerequisites

- Docker running, and the project's Python environment (`make sync`).
- No `psql` is needed on your computer: the lab runs it inside the database container (Part 3 has a one-line stand-in for the tests).

## Part 1 — A database to try it on, and the leak

Start the same Postgres image CI uses and migrate it:

```bash
docker run -d --name kaval-lab77 -e POSTGRES_DB=kaval -e POSTGRES_USER=kaval \
  -e POSTGRES_PASSWORD=ci-only-not-a-secret -p 5432:5432 pgvector/pgvector:pg16
export POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=kaval POSTGRES_USER=kaval \
  POSTGRES_PASSWORD=ci-only-not-a-secret
uv run alembic upgrade head
```

Run the file **as it was before the fix** (the commit before the fix is `5c5f45e`), the way the node does, with obviously fake passwords, inside a transaction that is rolled back so nothing is left behind:

```bash
( echo "BEGIN;"; git show 5c5f45e:scripts/ops/db-roles.sql; echo "ROLLBACK;" ) | docker exec -i kaval-lab77 \
  psql -U kaval -d kaval -X -v ON_ERROR_STOP=1 \
  -v gateway_password=lab-only-gateway -v agent_password=lab-only-agent \
  -v executor_password=lab-only-executor -v collector_password=lab-only-collector -f -
```

Four result blocks come back, each one showing a password:

```
 set_config
-----------------
 lab-only-gateway
(1 row)
```

That is the leak: `psql` prints the result of a `SELECT`, and the result is the password.

## Part 2 — The fix

The four statements now end in `\gset` (and carry a comment saying why):

```sql
SELECT set_config('kaval.gateway_password', :'gateway_password', false) AS discarded \gset
```

`\gset` runs the query and keeps the result in a `psql` variable instead of printing it. The passwords reach the `DO` blocks exactly as before (they read them back with `current_setting()`). Run the same command with the current file:

```bash
( echo "BEGIN;"; cat scripts/ops/db-roles.sql; echo "ROLLBACK;" ) | docker exec -i kaval-lab77 \
  psql -U kaval -d kaval -X -v ON_ERROR_STOP=1 \
  -v gateway_password=lab-only-gateway -v agent_password=lab-only-agent \
  -v executor_password=lab-only-executor -v collector_password=lab-only-collector -f -
```

It prints only the command tags (`BEGIN`, `DO`, `GRANT`, `ROLLBACK` and so on): no password.

## Part 3 — The tests

```bash
uv run pytest services/shared/tests/test_db_roles_output.py services/shared/tests/test_db_roles.py -v
```

Nine pass: the four new ones and the five older ones. The new file runs the real `psql`:

| Test | What it proves |
|---|---|
| `test_the_capture_can_see_what_psql_prints` | The control: a `SELECT`'s result does reach the output, so "no password found" means something |
| `test_running_the_script_prints_none_of_the_passwords` | None of four fake passwords appears anywhere in what `psql` prints |
| `test_the_roles_still_get_exactly_the_passwords_they_were_given` | Each role's stored password fingerprint (SCRAM) matches the fake password it was given |
| `test_every_line_that_passes_a_password_hides_its_result` | No database needed: a new password line without `\gset` fails |

**Seen failing first:** against the file before the fix, the control passed, the leak test said `psql printed the password of: gateway, agent, executor, collector`, and the static test said `lines [22, 23, 24, 25]`. **Can it fail the other way?** Changing one line to pass a different password made the fingerprint test say `kaval_agent got a different password`; the file was put back and checked identical.

**On a computer without `psql` (this project's Windows laptop):** put a stand-in called `psql.cmd` first on the `PATH`, containing `docker exec -i -e PGPASSWORD kaval-lab77 psql %*`, and run the tests from the same shell as above. Without `psql` the new tests **skip** on a laptop and **fail** in CI, where `KAVAL_REQUIRE_DB=1` is set: a missing check cannot hide in a green run.

**A second thing this found.** The older `test_db_roles.py` applies the same file through the Python driver and used to turn *any* exception into a skip. `\gset` is a `psql` command the driver cannot run, so right after the fix its five tests skipped without a word. It now translates the line and skips only when the database user may not create roles (SQLSTATE 42501); any other error fails.

## Part 4 — The proof on staging (after the merge)

The node downloads `db-roles.sql` from `main` when it boots, so this can only be done after the merge. After `make staging-up`, on the node (Session Manager), count how often each password appears in the two logs, printing nothing:

```bash
for s in gateway agent executor collector admin; do
  PW=$(aws ssm get-parameter --region ap-south-1 --with-decryption \
    --name "/kaval/kaval-staging/db/$s-password" --query Parameter.Value --output text)
  echo "$s: boot log $(grep -c -F -- "$PW" /var/log/cloud-init-output.log) line(s)," \
       "journal $(journalctl --no-pager | grep -c -F -- "$PW") line(s)"
done; unset PW
```

Before the fix this printed 1 and 3 for the four service passwords and 0 and 0 for the admin password. After it, every line must say 0 and 0. Also read the live value of the database server's statement log (`SHOW log_statement;`, expected `none`).

**Result, 2026-11-05** (a fresh staging built from `main`; the script above plus a control per password and the two log sizes, so a zero cannot be an empty search): boot log 275 lines and journal 4,855 lines; gateway, agent, executor, collector and admin each counted **0 and 0**, and the planted copy of each counted 1; `log_statement = none`. The same session's `make staging-up` ended with `Seed status: ok` ([Lab 41](lab-41-filling-staging-from-a-cleaned-copy.md)). The control is worth copying: write the password into a temporary file, count it with the same `grep`, expect 1.

**Result, 2026-11-06** (a different node: staging parked by hand and resumed, build `sha-978db36`, the same script): boot log 271 lines and journal 4,234 lines; the five passwords each counted **0 and 0**, each control 1; `log_statement = none`. The session's first node, the fresh build, was not counted.

## Part 5 — Production, at its next bring-up

Nothing to deploy: the script comes from `main`. After `make prod-smoke`, the same count with `/kaval/kaval-prod/db/...` must be 0 and 0.

## Done when

- [x] The leak is reproduced with fake passwords and the tests were seen failing first *(2026-11-04)*
- [x] The four statements use `\gset`; the roles get exactly the passwords they were given (the fingerprint test); a new bare line fails a test *(2026-11-04)*
- [x] The older role test no longer turns a broken file into a skip *(2026-11-04)*
- [x] Merged with CI green (CI must not skip the new tests: `psql` has to be on the runner) *(2026-11-04: merged as #313 with all 11 checks green; in CI's test job the four new psql tests ran and passed, 2045 passed)*
- [x] Proved on staging: zero occurrences of each password in the boot log and the journal, and `log_statement` read *(2026-11-05: all five passwords 0 and 0, each control 1; `log_statement = none`; repeated on a resumed node on 2026-11-06 with the same result; see Part 4)*
- [ ] Production's next bring-up shows the same zero *(at that bring-up)*

## Cost

$0 for the build and Parts 1 to 3. Part 4 shares a staging session with `KAV-126` (budgeted at $0.50 in all; the real figure is in the journal of 2026-11-05). `KAV-124` needed no staging session.

Clean up when you are done: `docker rm -f kaval-lab77`.
