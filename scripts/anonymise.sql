-- Sanitise a restored production snapshot before staging can be used.
--
-- Run by scripts/restore.sh against the STAGING database, immediately after
-- pg_restore and before anything is allowed to read it.
--
-- Kaval's data is not personal data in any meaningful sense -- it is cluster
-- events and model output. This exists anyway, because copying production
-- data into a lower environment unsanitised is the practice auditors flag,
-- and a project that cites ISO 42001 in its governance story should not do
-- the thing the standard exists to prevent.
--
-- See docs/adr/0005-data-durability-and-staging-seeding.md.
--
-- MAINTENANCE: a new column holding something sensitive needs a new statement
-- here, and forgetting is silent. The assertions at the bottom are the guard.

BEGIN;

-- ── who approved what ───────────────────────────────────────────────
-- Stable pseudonym, not random: the same actor must remain the same actor
-- across rows, or the approval history becomes unreadable.
UPDATE decision
SET actor = 'operator-' || substr(md5(actor), 1, 8)
WHERE actor IS NOT NULL;

-- ── log excerpts ────────────────────────────────────────────────────
-- signal.value carries arbitrary application output. Truncate it, then
-- redact the shapes that carry secrets.
UPDATE signal
SET value = left(value, 500)
WHERE value IS NOT NULL AND length(value) > 500;

UPDATE signal SET value = regexp_replace(value, '\d{12}',            '000000000000', 'g') WHERE value ~ '\d{12}';
UPDATE signal SET value = regexp_replace(value, 'arn:aws[^ "'']+',   'arn:aws:REDACTED', 'g') WHERE value ~ 'arn:aws';
UPDATE signal SET value = regexp_replace(value, '(AKIA|ASIA)[A-Z0-9]{16}', 'AKIAREDACTED', 'g') WHERE value ~ '(AKIA|ASIA)';
UPDATE signal SET value = regexp_replace(value, '[\w.+-]+@[\w-]+\.[\w.]+', 'user@example.com', 'g') WHERE value ~ '@';
UPDATE signal SET value = regexp_replace(value, 'eyJ[A-Za-z0-9_-]{10,}', 'JWT_REDACTED', 'g') WHERE value ~ 'eyJ';

-- ── model output, which quotes those log lines back ─────────────────
UPDATE proposal SET root_cause = regexp_replace(root_cause, '\d{12}',          '000000000000', 'g') WHERE root_cause ~ '\d{12}';
UPDATE proposal SET root_cause = regexp_replace(root_cause, 'arn:aws[^ "'']+', 'arn:aws:REDACTED', 'g') WHERE root_cause ~ 'arn:aws';
UPDATE proposal SET summary    = regexp_replace(summary,    '\d{12}',          '000000000000', 'g') WHERE summary ~ '\d{12}';

-- ── command output ──────────────────────────────────────────────────
-- SECOND line of defence only. The executor redacts before it inserts, so
-- production itself never stores a secret here -- see ADR-0005. If this
-- statement ever changes a row, that is a bug upstream, not a success here.
UPDATE execution
SET stdout = '[redacted during staging seed — investigate: executor should have redacted at write time]'
WHERE stdout ~ '(AKIA|ASIA)[A-Z0-9]{16}'
   OR stdout ~ 'eyJ[A-Za-z0-9_-]{10,}'
   OR stdout ~* 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY';

UPDATE execution SET stdout = left(stdout, 2000) WHERE length(stdout) > 2000;

-- ── infrastructure identifiers ──────────────────────────────────────
UPDATE execution SET before_state = regexp_replace(before_state::text, '\d{12}', '000000000000', 'g')::jsonb WHERE before_state::text ~ '\d{12}';
UPDATE execution SET after_state  = regexp_replace(after_state::text,  '\d{12}', '000000000000', 'g')::jsonb WHERE after_state::text  ~ '\d{12}';

-- ── mark the database so nobody mistakes it for production ──────────
COMMENT ON DATABASE CURRENT_DATABASE IS 'STAGING — seeded from a sanitised production snapshot';

COMMIT;

-- ── assertions ──────────────────────────────────────────────────────
-- The script is only as good as its coverage, so fail loudly rather than
-- leaving something behind quietly. restore.sh aborts on a non-zero exit.
DO $$
DECLARE leaked int;
BEGIN
  SELECT count(*) INTO leaked FROM signal
   WHERE value ~ '\d{12}' AND value !~ '000000000000';
  IF leaked > 0 THEN
    RAISE EXCEPTION 'anonymise: % signal rows still contain a 12-digit identifier', leaked;
  END IF;

  SELECT count(*) INTO leaked FROM decision WHERE actor NOT LIKE 'operator-%';
  IF leaked > 0 THEN
    RAISE EXCEPTION 'anonymise: % decision rows still carry a real actor', leaked;
  END IF;

  SELECT count(*) INTO leaked FROM execution
   WHERE stdout ~ '(AKIA|ASIA)[A-Z0-9]{16}' OR stdout ~ 'eyJ[A-Za-z0-9_-]{10,}';
  IF leaked > 0 THEN
    RAISE EXCEPTION 'anonymise: % execution rows still contain credential-shaped text', leaked;
  END IF;
END $$;
