# Lab 87 — A decision is written once

**Phase:** 8 (hardening) · **Story:** `KAV-116` · **Time:** about 30 minutes · **Cost:** $0 (a throwaway Postgres on your own laptop; nothing in AWS for Parts 1 to 4)

Every approve or deny becomes one row in the `decision` table, and a verdict should never change afterwards. The gateway's database login could nevertheless `UPDATE` that table
([ADR-0072](../adr/0072-a-written-threat-model-and-a-prompt-injection-set.md), threat-model row T-APPROVE-4). This lab takes that right away and proves it,
including on a database that already has it ([ADR-0085](../adr/0085-the-gateways-database-login-can-record-a-decision-but-never-change-it.md)).

## Prerequisites

- The repository checked out and `make sync`
- Docker running (any Postgres with the project's schema will do; this lab uses a throwaway one)
- Read [ADR-0008](../adr/0008-production-database-on-its-own-server.md) (one login per service) and [ADR-0027](../adr/0027-automatic-secret-and-role-recreation.md) (how the permissions file is run)

## Part 1 — A throwaway database with the real schema

```bash
docker run -d --name kaval-roles-lab -e POSTGRES_DB=kaval -e POSTGRES_USER=kaval \
  -e POSTGRES_PASSWORD=<any-throwaway-value> -p 127.0.0.1:55432:5432 pgvector/pgvector:pg16
export POSTGRES_HOST=localhost POSTGRES_PORT=55432 POSTGRES_DB=kaval POSTGRES_USER=kaval \
  POSTGRES_PASSWORD=<the-same-value>
alembic upgrade head
```

The tests run inside a transaction that is rolled back, so nothing they create outlasts them.

## Part 2 — See the gap (on the commit before this change)

```bash
python -m pytest services/shared/tests/test_db_roles.py -q
```

On `main` at `f2cd828` the five older tests pass. Add the new tests from this story without the fix and four of them fail, all for the same reason: `UPDATE` on `decision` is allowed for
`kaval_gateway`. The test that lists every login's rights shows exactly one difference from what is written down, the gateway's extra `UPDATE`.

## Part 3 — The fix

In `scripts/ops/db-roles.sql` the gateway's line for `decision` becomes:

```sql
GRANT SELECT, INSERT ON decision TO kaval_gateway;
REVOKE UPDATE, DELETE, TRUNCATE ON decision FROM kaval_gateway;
```

The second line is the one that matters for production. A `GRANT` is not undone by deleting it: production's database was given `UPDATE` by an earlier run of this file, so only an
explicit `REVOKE` takes it back. Where the right is already absent the `REVOKE` does nothing, so the file stays safe to run every six hours.

## Part 4 — The tests (all in `services/shared/tests/test_db_roles.py`)

| Test | What it proves |
|---|---|
| the gateway records and reads decisions but can never change one | `INSERT` and `SELECT` allowed; `UPDATE`, `DELETE`, `TRUNCATE` refused |
| the real approval code still works as the gateway's own login | `record_decision` run as `kaval_gateway`: records a verdict, reads it back, refuses a second one, and the second never overwrites the first |
| a database that already has the old update right loses it | the negative case: start from the old state, run the file again, the right is gone and reading still works |
| the script never grants the gateway update on decision | the cheap check that needs no database (runs even where the others are skipped) |
| every login has exactly the table rights written down | `EXPECTED_RIGHTS`: any table or right not listed is "none", so a slip fails CI |

Two checks that the tests are real, both done once by hand:

- Take `INSERT` away from the gateway in the file: three of the tests fail (the gateway could no longer record a decision, which is the thing this change must not break). Put it back.
- Leave the `REVOKE` out: the "already has the old right" test fails. That is the test a fresh database would have let through.

```bash
python -m pytest services/shared/tests/test_db_roles.py -q      # 10 passed
make lint                                                       # ruff and mypy
docker rm -f kaval-roles-lab                                    # the throwaway database
```

## Part 5 — Where it goes live (no image, no release)

The file is fetched from `main` by the app server on every start and every six hours (ADR-0027), so the change reaches each environment by itself:

- **Staging:** at its next build. Its database is new, so this proves the new file on a fresh database. A read-only question to the live database then shows the right is absent:
  `SELECT has_table_privilege('kaval_gateway', 'public.decision', 'UPDATE')` returns `false`.
- **Production:** at its next start-up. Its database had the right, so this proves the `REVOKE`. The same question confirms it.

The staging proof rides on the next staging build (the `KAV-111` release) rather than costing its own session. If you would rather have it separately it is a normal staging session, about 5 cents.

**Rolling back:** revert the file. A database that already lost the right keeps losing nothing it needs; nothing uses the right.

## What this does not do

- The admin login can still edit the table. The staging cleaning step needs that. Closing it (a trigger, or locked storage) is its own decision ([ADR-0085](../adr/0085-the-gateways-database-login-can-record-a-decision-but-never-change-it.md), options 2 and 3).
- A gateway that is hijacked can still insert a verdict for an action that has none. Who may approve is the approver login's job, the expiry's and the executor's own re-check.

## Checklist

- [x] The gateway's database login can record and read a decision and cannot change or delete one *(2026-11-11; Part 4)*
- [x] The permissions file takes the right back on every run, and a test starts from a database that still has it *(Part 4)*
- [x] The real approval code works as the gateway's own login *(Part 4)*
- [x] Every login's table rights are listed in one test *(Part 4)*
- [ ] Staging applied the file and the live question returns `false` (rides on the next staging build)
- [ ] Production applied it at its next start-up and the live question returns `false`
