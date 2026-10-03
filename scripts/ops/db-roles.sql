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

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kaval_gateway') THEN
    CREATE ROLE kaval_gateway LOGIN PASSWORD :'gateway_password';
  ELSE
    ALTER ROLE kaval_gateway PASSWORD :'gateway_password';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kaval_agent') THEN
    CREATE ROLE kaval_agent LOGIN PASSWORD :'agent_password';
  ELSE
    ALTER ROLE kaval_agent PASSWORD :'agent_password';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kaval_executor') THEN
    CREATE ROLE kaval_executor LOGIN PASSWORD :'executor_password';
  ELSE
    ALTER ROLE kaval_executor PASSWORD :'executor_password';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kaval_collector') THEN
    CREATE ROLE kaval_collector LOGIN PASSWORD :'collector_password';
  ELSE
    ALTER ROLE kaval_collector PASSWORD :'collector_password';
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO kaval_gateway, kaval_agent, kaval_executor, kaval_collector;

-- collector: writes raw signals only.
GRANT SELECT, INSERT ON signal TO kaval_collector;

-- agent: reads history, writes its own reasoning output. Never touches decision or
-- execution -- those are the human-oversight and executor boundaries.
GRANT SELECT ON signal, incident TO kaval_agent;
GRANT SELECT, INSERT, UPDATE ON incident, proposal, action TO kaval_agent;

-- executor: the only component that acts, and only on what's already been decided.
GRANT SELECT ON action, decision TO kaval_executor;
GRANT SELECT, INSERT, UPDATE ON execution, outcome TO kaval_executor;

-- gateway: the human-facing API. Reads broadly across the pipeline, writes decisions.
GRANT SELECT ON incident, proposal, action, execution, outcome TO kaval_gateway;
GRANT SELECT, INSERT, UPDATE ON decision TO kaval_gateway;

-- A GRANT on a table's INSERT doesn't cover its identity/serial sequence.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
  TO kaval_gateway, kaval_agent, kaval_executor, kaval_collector;
