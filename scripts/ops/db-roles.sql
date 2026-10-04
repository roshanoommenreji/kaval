-- Per-service Postgres roles, matching the agent/executor privilege split CLAUDE.md's
-- constraint 3 already enforces at the Kubernetes RBAC layer (ADR-0021, KAV-47) -- this is
-- the same split enforced one layer down, in the database itself (ADR-0008, KAV-32).
--
-- Run once per release by the db-roles-job Helm hook, as the admin role, after
-- migrate-job has created the schema (CREATE/GRANT need the tables to exist first).
-- Idempotent: CREATE ROLE has no IF NOT EXISTS, so each is wrapped in a check against
-- pg_roles. Passwords come in as psql variables (-v gateway_password=... etc.), never
-- typed into this file -- Terraform generates them, SSM Parameter Store holds them.
--
-- Table list matches docs/architecture/architecture.toml's "db" node.

\set ON_ERROR_STOP on

-- psql's `:'var'` substitution does not reach inside a DO $$ ... $$ body -- the dollar
-- quoting hides it from psql's own pre-scan, so the literal text ":'gateway_password'"
-- was being sent straight to the server (found live, KAV-56: a real syntax error on the
-- first real run of this file, the db-roles-job Helm hook that would have run it earlier
-- having been blocked before it was ever built). set_config() runs substitution normally
-- (it's plain top-level SQL, not inside $$ $$), then the DO block reads it back with
-- current_setting() and applies it via format(..., %L) for safe, injection-proof quoting.
SELECT set_config('kaval.gateway_password', :'gateway_password', false);
SELECT set_config('kaval.agent_password', :'agent_password', false);
SELECT set_config('kaval.executor_password', :'executor_password', false);
SELECT set_config('kaval.collector_password', :'collector_password', false);

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kaval_gateway') THEN
    EXECUTE format('CREATE ROLE kaval_gateway LOGIN PASSWORD %L', current_setting('kaval.gateway_password'));
  ELSE
    EXECUTE format('ALTER ROLE kaval_gateway PASSWORD %L', current_setting('kaval.gateway_password'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kaval_agent') THEN
    EXECUTE format('CREATE ROLE kaval_agent LOGIN PASSWORD %L', current_setting('kaval.agent_password'));
  ELSE
    EXECUTE format('ALTER ROLE kaval_agent PASSWORD %L', current_setting('kaval.agent_password'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kaval_executor') THEN
    EXECUTE format('CREATE ROLE kaval_executor LOGIN PASSWORD %L', current_setting('kaval.executor_password'));
  ELSE
    EXECUTE format('ALTER ROLE kaval_executor PASSWORD %L', current_setting('kaval.executor_password'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kaval_collector') THEN
    EXECUTE format('CREATE ROLE kaval_collector LOGIN PASSWORD %L', current_setting('kaval.collector_password'));
  ELSE
    EXECUTE format('ALTER ROLE kaval_collector PASSWORD %L', current_setting('kaval.collector_password'));
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO kaval_gateway, kaval_agent, kaval_executor, kaval_collector;

-- collector: writes raw signals only.
GRANT SELECT, INSERT ON signal TO kaval_collector;

-- agent: reads history, writes its own reasoning output. Never touches decision or
-- execution -- those are the human-oversight and executor boundaries.
GRANT SELECT ON signal, incident TO kaval_agent;
GRANT SELECT, INSERT, UPDATE ON incident, proposal, action TO kaval_agent;
-- incident_signal: the many-to-many correlate.py writes while grouping signals into an
-- incident (insert-only per its own model docstring -- never updated or deleted).
-- Missing here caused a real, live failure (KAV-56): the deployed agent could log in fine
-- but every correlation pass failed with "permission denied for table incident_signal".
GRANT SELECT, INSERT ON incident_signal TO kaval_agent;
-- runbook_chunk: kaval_agent.index_runbooks' own table (its docstring: "creating, updating
-- or deleting rows"), not yet part of the deployed --every loop but owned by this role.
GRANT SELECT, INSERT, UPDATE, DELETE ON runbook_chunk TO kaval_agent;

-- executor: the only component that acts, and only on what's already been decided.
GRANT SELECT ON action, decision TO kaval_executor;
GRANT SELECT, INSERT, UPDATE ON execution, outcome TO kaval_executor;

-- gateway: the human-facing API. Reads broadly across the pipeline, writes decisions.
GRANT SELECT ON incident, proposal, action, execution, outcome TO kaval_gateway;
GRANT SELECT, INSERT, UPDATE ON decision TO kaval_gateway;

-- A GRANT on a table's INSERT doesn't cover its identity/serial sequence.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
  TO kaval_gateway, kaval_agent, kaval_executor, kaval_collector;
