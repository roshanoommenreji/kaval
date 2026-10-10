-- Sanitise a restored production snapshot before staging can be used.
--
-- Run by scripts/ops/restore.sh against the STAGING database, immediately after
-- pg_restore and before anything is allowed to read it. restore.sh runs it with
-- `psql --single-transaction`, so the whole file is ONE transaction: if an assertion at
-- the bottom fails, every change here rolls back, and restore.sh then wipes the restored
-- tables (a copy that could not be proven clean is never left readable). That is why this
-- file has no BEGIN/COMMIT of its own, and why the test can run it inside a transaction it
-- rolls back (services/shared/tests/test_anonymise.py).
--
-- Kaval's data is not personal data in any meaningful sense -- it is cluster
-- events and model output. This exists anyway, because copying production
-- data into a lower environment unsanitised is the practice auditors flag,
-- and a project that cites ISO 42001 in its governance story should not do
-- the thing the standard exists to prevent.
--
-- See docs/adr/0005-data-durability-and-staging-seeding.md and, for the rewrite against the
-- real schema (jsonb columns, single transaction, "scrubbing twice changes nothing"),
-- docs/adr/0037-staging-seeding-from-the-production-dump.md.
--
-- MAINTENANCE: a new column holding anything free-form needs a new statement in the UPDATE
-- section AND a line in the final check, and forgetting is silent. The test fills every
-- text and jsonb column with dirty data and fails if one is left untouched, which is the
-- guard against forgetting. The columns this file does NOT touch are listed at the bottom,
-- with the reason, so "not covered" is a decision on record and not an accident.

-- ── the scrubbing functions ─────────────────────────────────────────
-- Created in pg_temp: they exist for this session only and leave nothing behind in the
-- staging database.

-- Text: replace every shape that identifies an account, a person, a machine or a secret.
-- Order matters: ARNs go before bare account numbers, because an ARN contains one.
-- Every replacement is itself unchanged by a second pass, so scrub(scrub(x)) = scrub(x):
-- the final check relies on that ("is there anything left for the scrubber to change?").
CREATE OR REPLACE FUNCTION pg_temp.scrub_text(t text) RETURNS text
LANGUAGE sql IMMUTABLE AS $f$
  SELECT regexp_replace(
         regexp_replace(
         regexp_replace(
         regexp_replace(
         regexp_replace(
         regexp_replace(
         regexp_replace(
         regexp_replace(
           t,
           '-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)', 'PRIVATE_KEY_REDACTED', 'gs'),
           'eyJ[A-Za-z0-9_-]{10,}(\.[A-Za-z0-9_-]+){0,2}', 'JWT_REDACTED', 'g'),
           '(AKIA|ASIA)[A-Z0-9]{16}', 'AKIAREDACTED', 'g'),
           'arn:aws[a-z-]*:[^ "''\\,;)]+', 'arn:aws:REDACTED', 'g'),
           '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+', 'user@example.com', 'g'),
           '\mip-[0-9]+-[0-9]+-[0-9]+-[0-9]+(\.[A-Za-z0-9.-]+)?', 'ip-0-0-0-0.internal', 'g'),
           '(?<![0-9])[0-9]{12}(?![0-9])', '000000000000', 'g'),
           '\m(xox[abprs]|xapp)-[A-Za-z0-9-]{10,}', 'SLACK_TOKEN_REDACTED', 'g')
$f$;

-- Text with a length cap, for the columns that carry arbitrary application output. Scrub
-- first, cut second: cut first and a secret could be sliced in half, leaving a fragment no
-- pattern recognises; and a replacement longer than what it replaced could push the result
-- back over the cap.
CREATE OR REPLACE FUNCTION pg_temp.scrub_text_capped(t text, cap int) RETURNS text
LANGUAGE sql IMMUTABLE AS $f$
  SELECT left(pg_temp.scrub_text(t), cap)
$f$;

-- jsonb: scrub every string value and every key's value, at any depth. Numbers, booleans
-- and nulls pass through. Walking the structure (instead of casting the whole document to
-- text and back) cannot produce invalid JSON, and cannot damage a number that happens to
-- have twelve digits.
CREATE OR REPLACE FUNCTION pg_temp.scrub_json(j jsonb, cap int) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE AS $f$
BEGIN
  CASE jsonb_typeof(j)
    WHEN 'string' THEN
      RETURN to_jsonb(pg_temp.scrub_text_capped(j #>> '{}', cap));
    WHEN 'object' THEN
      RETURN COALESCE(
        (SELECT jsonb_object_agg(pg_temp.scrub_text(k), pg_temp.scrub_json(v, cap))
           FROM jsonb_each(j) AS e(k, v)),
        '{}'::jsonb);
    WHEN 'array' THEN
      RETURN COALESCE(
        (SELECT jsonb_agg(pg_temp.scrub_json(v, cap) ORDER BY ord)
           FROM jsonb_array_elements(j) WITH ORDINALITY AS a(v, ord)),
        '[]'::jsonb);
    ELSE
      RETURN j;
  END CASE;
END
$f$;

-- ── who approved what ───────────────────────────────────────────────
-- Stable pseudonym, not random: the same actor must remain the same actor
-- across rows, or the approval history becomes unreadable. Real values look like
-- 'slack:<name>' or a command-line user, so the whole string is hashed.
UPDATE decision
SET actor = 'operator-' || substr(md5(actor), 1, 8)
WHERE actor NOT LIKE 'operator-%';

-- A free-text reason can name a person, and no pattern finds a name. Keep that a reason
-- existed, drop what it said.
UPDATE decision
SET reason = '[reason removed during staging seed]'
WHERE reason IS NOT NULL;

-- ── raw observations ────────────────────────────────────────────────
-- signal.value is arbitrary application output (jsonb): capped at 500 characters per
-- string, then scrubbed.
UPDATE signal
SET target = pg_temp.scrub_text(target),
    value  = pg_temp.scrub_json(value, 500);

UPDATE incident
SET fingerprint = pg_temp.scrub_text(fingerprint);

-- ── model output, which quotes those log lines back ─────────────────
UPDATE proposal
SET summary    = pg_temp.scrub_text(summary),
    root_cause = pg_temp.scrub_text(root_cause);

-- ── what the executor was asked to do ───────────────────────────────
UPDATE action
SET target = pg_temp.scrub_text(target),
    params = pg_temp.scrub_json(params, 500);

-- ── command output and state snapshots ──────────────────────────────
-- SECOND line of defence only. The executor redacts before it inserts, so production
-- itself never stores a secret here -- see ADR-0005. If this ever changes a row beyond the
-- length cap, that is a bug upstream, not a success here.
UPDATE execution
SET stdout       = pg_temp.scrub_text_capped(stdout, 2000),
    before_state = pg_temp.scrub_json(before_state, 2000),
    after_state  = pg_temp.scrub_json(after_state, 2000);

-- ── mark the database so nobody mistakes it for production ──────────
-- COMMENT ON takes a name, not an expression, so the name is looked up and quoted.
DO $f$
BEGIN
  EXECUTE format('COMMENT ON DATABASE %I IS %L', current_database(),
                 'STAGING - seeded from a sanitised production snapshot');
END
$f$;

-- ── the final check ─────────────────────────────────────────────────
-- Scrubbing a second time must change nothing. If it would, something was missed, and the
-- error rolls the whole transaction back. One line per column, so the message says which.
DO $f$
DECLARE
  leaked int;
BEGIN
  SELECT count(*) INTO leaked FROM decision WHERE actor NOT LIKE 'operator-%';
  IF leaked > 0 THEN RAISE EXCEPTION 'anonymise: % decision rows still carry a real actor', leaked; END IF;

  SELECT count(*) INTO leaked FROM decision WHERE reason IS NOT NULL AND reason <> '[reason removed during staging seed]';
  IF leaked > 0 THEN RAISE EXCEPTION 'anonymise: % decision rows still carry a free-text reason', leaked; END IF;

  SELECT count(*) INTO leaked FROM signal
   WHERE target <> pg_temp.scrub_text(target) OR value <> pg_temp.scrub_json(value, 500);
  IF leaked > 0 THEN RAISE EXCEPTION 'anonymise: % signal rows would still change on a second pass', leaked; END IF;

  SELECT count(*) INTO leaked FROM incident WHERE fingerprint <> pg_temp.scrub_text(fingerprint);
  IF leaked > 0 THEN RAISE EXCEPTION 'anonymise: % incident rows would still change on a second pass', leaked; END IF;

  SELECT count(*) INTO leaked FROM proposal
   WHERE summary <> pg_temp.scrub_text(summary) OR root_cause <> pg_temp.scrub_text(root_cause);
  IF leaked > 0 THEN RAISE EXCEPTION 'anonymise: % proposal rows would still change on a second pass', leaked; END IF;

  SELECT count(*) INTO leaked FROM action
   WHERE target <> pg_temp.scrub_text(target) OR params <> pg_temp.scrub_json(params, 500);
  IF leaked > 0 THEN RAISE EXCEPTION 'anonymise: % action rows would still change on a second pass', leaked; END IF;

  SELECT count(*) INTO leaked FROM execution
   WHERE stdout <> pg_temp.scrub_text_capped(stdout, 2000)
      OR before_state <> pg_temp.scrub_json(before_state, 2000)
      OR after_state  <> pg_temp.scrub_json(after_state, 2000);
  IF leaked > 0 THEN RAISE EXCEPTION 'anonymise: % execution rows would still change on a second pass', leaked; END IF;
END
$f$;

-- ── columns deliberately NOT touched ────────────────────────────────
-- runbook_chunk   the runbooks are public files in this repository; the table is an index of them.
-- proposal.model, action.type, every enum, boolean and timestamp: no free text.
-- numbers inside jsonb: a 12-digit JSON number is as likely a byte count as an account id
--                 (ids with a leading zero arrive as text, and text is scrubbed), and rewriting
--                 it would corrupt real figures. Account ids are only ever scrubbed as text.
-- alembic_version the schema revision. Left as the dump had it on purpose: staging's newer
--                 migrations then run against real data, which is half the point (ADR-0037).
