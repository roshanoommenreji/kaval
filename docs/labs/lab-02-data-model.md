# Lab 02 — The data model: SQLAlchemy models and an Alembic migration

**Phase:** 1 · **Time:** ~2 hrs · **Cost:** $0 (entirely local — a throwaway Docker Postgres, deleted
at the end)

The first application code in the project. Everything else in Phase 1 — the synthetic signal
generator, the gateway, eventually the agent and executor — writes to these seven tables. Getting
the schema right first means the rest of Phase 1 is built against something real instead of a
placeholder.

---

## Why this order

`docs/architecture/overview.md#data-model` already names the seven tables and what each one holds.
That document is the *decision*; this lab is where it becomes a schema a database can actually
enforce — primary keys, foreign keys, the constraints that make "append-only" a property the
database checks rather than a convention people remember.

The temptation with a first migration is to hand-write it and move on — there's no running
database yet, so what would you even test it against? The answer used in this lab: stand up a
**throwaway** Postgres in Docker, generate the migration against it for real, apply it, and tear
the container down. A migration nobody has run is a migration nobody has verified.

---

## Prerequisites

- [Lab 00](lab-00-toolchain.md) complete — Python 3.11, Docker
- `docs/architecture/overview.md` read — this lab implements its data model section

---

## Step 1 — Project scaffolding

A single root `pyproject.toml` covers every service under `services/` — this is a monorepo-lite
layout, not one package per service. `services/shared` holds code every service imports (models,
DB session setup), installed in editable mode so `import kaval_shared` resolves without any path
hacking.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/python -m pip install -e ".[dev]"
```

Key `pyproject.toml` choices:

- `hatchling` as the build backend, with `packages = ["services/shared/kaval_shared"]` — the only
  package published, everything else is an application, not a library
- `sqlalchemy`, `alembic`, `psycopg[binary]`, `pydantic` as runtime deps; `pytest`, `ruff`, `mypy`
  as dev deps
- `mypy` in `strict` mode from day one — catching type errors here is cheaper than after four
  services depend on these models

---

## Step 2 — The models

`services/shared/kaval_shared/models.py`, SQLAlchemy 2.0 declarative style (`Mapped` /
`mapped_column`, not the legacy `Column`).

Design calls made here, beyond what the architecture doc already specified:

| Choice | Reasoning |
|---|---|
| UUID primary keys, generated Python-side (`default=uuid.uuid4`) | No dependency on a Postgres extension (`pgcrypto`/`uuid-ossp`) just to generate an ID |
| `decision.action_id` and `execution.action_id` are `UNIQUE` | Enforces one decision and one execution per action at the database level. A re-decision or a retry is a **new** `action` row referencing the same proposal — consistent with "nothing is ever updated in place" |
| `signal.value` and `action.params` are `JSONB`, not a rigid columns-per-field schema | Signal payloads and action parameters genuinely vary by `source`/`type`; a fixed column set would mean a migration for every new signal source |
| `proposal.confidence` has a `CHECK (confidence BETWEEN 0 AND 1)` constraint | A confidence outside that range is a bug in the agent, not valid data — better to have the database refuse it |
| Sync SQLAlchemy engine, not async | Nothing in Phase 1 needs it; the models work unchanged under either. Revisit only if request volume ever makes it matter — see `services/shared/kaval_shared/db.py` |
| `execution.stdout` is documented, not enforced, as pre-redacted | The executor doesn't exist yet (Phase 3). The column-level docstring flags the constraint now so it isn't forgotten later — see [ADR-0005](../adr/0005-data-durability-and-staging-seeding.md) |

`services/shared/kaval_shared/db.py` builds the connection string from the same `POSTGRES_*`
variables every service already reads from `.env`, with a **bounded `connect_timeout`** — found
necessary during this lab (Step 5) rather than added speculatively.

---

## Step 3 — Alembic, pointed at the models

```bash
alembic.ini            # script_location = migrations; no sqlalchemy.url — see env.py
migrations/env.py          # builds the URL from POSTGRES_* at runtime, imports Base.metadata
migrations/script.py.mako  # the revision template
migrations/versions/       # generated migrations live here
```

One root cause this avoids: a hardcoded `sqlalchemy.url` in `alembic.ini` would be a second place
a connection string could drift from what the services actually use. `env.py` calls the same
`database_url()` function `db.py` uses, so there is exactly one place a Postgres URL is assembled.

---

## Step 4 — Generate and verify the migration against a real Postgres

No project Postgres exists yet (that's `docker-compose`, still open in Phase 1). Rather than
hand-write the first migration and hope, this lab spins up a **disposable** container to generate
and prove it against:

```bash
docker run -d --rm --name kaval-verify-pg \
  -e POSTGRES_USER=kaval -e POSTGRES_PASSWORD=verify-only -e POSTGRES_DB=kaval \
  -p 5433:5432 pgvector/pgvector:pg16
```

(`pgvector/pgvector:pg16` — the same image family `docker-compose` will use, since Phase 2's
similarity search needs the extension even though this migration doesn't use it yet.)

```bash
export POSTGRES_USER=kaval POSTGRES_PASSWORD=verify-only \
       POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_DB=kaval

alembic revision --autogenerate -m "initial spine: signal through outcome"
alembic upgrade head
alembic check                 # "No new upgrade operations detected" == migration matches models
```

Then the round-trip that actually matters:

```bash
alembic downgrade base
alembic upgrade head
```

---

## What broke, and why it's worth knowing

### PostgreSQL `ENUM` types survive `DROP TABLE`

The `downgrade → upgrade` round-trip above failed the first time:

```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.DuplicateObject) type "severity" already exists
```

Alembic's autogenerated `downgrade()` drops tables but **not** the named `ENUM` types backing
their columns — dropping a table doesn't drop a type Postgres created for it. Six enums in this
schema (`severity`, `risk_level`, `blast_radius`, `policy_class`, `verdict`, `execution_status`)
all hit this. Fix: the migration's `downgrade()` explicitly drops each type after the tables:

```python
for enum_name in ("severity", "risk_level", "blast_radius", "policy_class",
                   "verdict", "execution_status"):
    sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
```

This is a known Alembic/Postgres gap, not a mistake in the models — but it only surfaces if the
downgrade path is actually exercised, which is exactly why this lab exercises it.

### A test fixture that owns schema lifecycle fights Alembic for ownership

The integration test (`test_models_roundtrip.py`) originally called
`Base.metadata.create_all(engine)` before each test and `drop_all(engine)` after. That collided
directly with this lab's own migration testing: running `alembic upgrade head`, then `pytest`,
then `alembic downgrade base` failed with `table "execution" does not exist` — the test's own
teardown had already dropped every table before Alembic tried to.

Fixed by giving the test proper transactional isolation instead of schema ownership: bind the
`Session` to a connection whose outer transaction is rolled back at teardown, using
`join_transaction_mode="create_savepoint"` so the test's own `session.commit()` calls create a
`SAVEPOINT` rather than really committing. Nothing the test does — insert or "commit" — survives
past the test, and the schema itself is never touched by the test suite. Confirmed by checking the
row count in the database directly after a test run, not just trusting the assertion passed.

### An unreachable Postgres hung for over four minutes instead of failing fast

Pointing the test suite at a closed port (simulating "forgot to run `docker-compose up`") should
skip quickly. Instead it hung for **261 seconds** before the connection attempt gave up. The fix —
a `connect_timeout` on the engine:

```python
create_engine(database_url(), pool_pre_ping=True, connect_args={"connect_timeout": 5})
```

— brought the same case down to about 12 seconds. This matters beyond the test suite: without it,
every service would hang for minutes on a genuinely down database instead of failing fast and
letting an orchestrator restart it.

---

## Step 5 — Tests

Two files, two different jobs:

- `test_models_schema.py` — no database required. Inspects `Base.metadata` directly: every table
  present, every foreign key pointing at the right parent, the `UNIQUE` constraints on
  `decision.action_id` / `execution.action_id`, the confidence `CHECK` constraint. Catches typos
  in the models before they ever reach a migration.
- `test_models_roundtrip.py` — requires a real Postgres. Inserts one row per table through the
  full spine (`signal → incident → proposal → action → decision/execution → outcome`) and reads
  it back through the ORM relationships. **Skips**, rather than fails, if no database is reachable
  — `make dev` brings up the database `make test` needs; a missing database during Phase 1
  development isn't a broken build.

```bash
pytest services/shared -v
ruff check services/ migrations/
mypy services/
```

All three clean before this lab counts as done.

---

## Step 6 — Clean up

```bash
docker stop kaval-verify-pg    # --rm on `docker run` means it's gone once stopped
```

Nothing persists from this lab except the migration file itself and the models it was generated
from. `docker-compose`'s own Postgres (next lab) starts from the same migration, applied fresh.

---

## Done when

- `alembic upgrade head` applies cleanly against a fresh Postgres
- `alembic downgrade base` followed by `alembic upgrade head` round-trips without error
- `alembic check` reports no drift between the models and the migration
- `pytest services/shared`, `ruff check`, and `mypy --strict` all pass
- The throwaway verification container is stopped and no state from it persists anywhere

## Related

- [docs/architecture/overview.md](../architecture/overview.md) — the data model this implements
- [ADR-0005](../adr/0005-data-durability-and-staging-seeding.md) — why `execution.stdout` must be
  pre-redacted
- `docs/learn/phase-1-local-first.md` — the concept-level explanation of containers, images, and
  why CPU inference is viable at this scale (written before this lab; flips to `experience` once
  Phase 1 closes)
