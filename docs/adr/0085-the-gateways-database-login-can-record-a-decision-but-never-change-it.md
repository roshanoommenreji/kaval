# ADR-0085: The gateway's database login can record a decision but never change it

**Status:** Accepted on 2026-11-11 (Roshan: "go with your recommendations" on the plan). Built in `KAV-116`; the staging proof and production's next start-up are still to come (see Consequences).
**Date:** 2026-11-11
**Related:** [ADR-0072](0072-a-written-threat-model-and-a-prompt-injection-set.md) (the threat model that found this, row T-APPROVE-4),
[ADR-0008](0008-production-database-on-its-own-server.md) (one database login per service), [ADR-0027](0027-automatic-secret-and-role-recreation.md) (how the permissions file is run on every start-up),
[ADR-0039](0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md) (the cleaning step that must keep working),
[Lab 87](../labs/lab-87-a-decision-is-written-once.md), `KAV-116`

## Context

Every approve or deny becomes one row in the `decision` table, and the design says a verdict is written once: the code only ever inserts a row (`decisions.record_decision`), and a unique
constraint refuses a second verdict for the same action. But `scripts/ops/db-roles.sql` also granted the gateway's database login `UPDATE` on that table. Nothing used it. It still meant a
confused or hijacked gateway could have turned a "denied" into an "approved" after the fact, and the executor reads that verdict before it acts. Threat-model row T-APPROVE-4 listed it as a gap.

Two facts shaped the fix:

- **A grant is never undone by deleting its line.** Production's database was given `UPDATE` by an earlier run of the file. Deleting the line would have fixed every new database and left production
  as it was, and a test that starts from a fresh database would have passed anyway.
- **The file runs on every start-up and every six hours**, fetched from `main` by the server itself (ADR-0027). So a change to it needs no new app version, no release and no promotion; each environment
  takes it the next time that environment starts.

## Options (what real companies do)

1. **Take the right away at the database login, and test it.** Least privilege, the standard first move for an audit table. Cheap and easy to reverse.
2. **A database trigger that refuses every update and delete on the table, even for the admin.** Stronger (the admin cannot slip either), but the staging seeding step *legitimately* updates this table:
   `anonymise.sql` replaces real names and free text in `decision` before a production copy is loaded into staging (ADR-0039). A trigger would break that, or need an exception that weakens it.
3. **Ship every decision to locked storage as well** (an object-lock bucket as the tamper-proof record). The right answer for a regulated audit trail; a project of its own for a table nothing edits.

## Decision

Option 1, with three guards, and a test of every login's rights while there:

1. **`GRANT SELECT, INSERT ON decision TO kaval_gateway`**, then **`REVOKE UPDATE, DELETE, TRUNCATE ON decision FROM kaval_gateway`** in the same file. The REVOKE is what changes production's database; where the right is
   already absent it does nothing.
2. **A test starts from the old state.** It applies the file, grants the old right back (how production's database is), applies the file again (the next start-up) and checks the right is gone, and that
   the gateway can still read.
3. **The real approval code runs as the gateway's own login** in a test (`record_decision` records a verdict, reads it back, and a second verdict is refused and does not overwrite the first). The
   all-powerful test user cannot see a missing right (found before, KAV-95), so this proves the change did not break the one thing the gateway does with the table.
4. **Every login's table rights are written down in one test** (`EXPECTED_RIGHTS`): any table or right not listed there is "none". A new table, or a right added by a slip, fails CI until somebody writes it down on
   purpose. This is the "test of each role's rights" the secure-by-design table in `docs/future-scope.md` promised.

## Consequences

- The gateway can no longer change or delete a verdict. A hijacked gateway can still *insert* a verdict for an action that has none (that is what approving is), which the approver login on the route, the
  expiry and the executor's own re-check cover; this change does not claim more than it does.
- **The admin login can still edit the table.** That is needed (the staging cleaning step) and it is not a service login. Closing it would be option 2 or 3, each its own decision.
- **The executor's `UPDATE` on `execution` and `outcome`** is by design (it records what happened and later whether the fix held) and is now written in the rights test, so a change to it is visible.
- **Rollout:** no image and no release. Staging takes it at its next build (a fresh database, so this proves the new file); production takes it at its next start-up (the database that had the right, so this
  proves the REVOKE). A read-only question to each live database confirms it afterwards. If the file fails to run, the server keeps its old rights and retries six hours later; to undo, revert the file
  (and, for a database that already lost the right, grant it back by hand, which nothing needs).
- Cost: $0.
