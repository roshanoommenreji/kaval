# ADR-0034: `rollback.yml` goes back only to a version we already trusted, never waits on Jira, and refuses to cross a database migration unprepared

**Status:** Accepted
**Date:** 2026-10-10
**Related:** [ADR-0004](0004-environment-strategy-and-promotion.md) (promotion path),
[ADR-0032](0032-promote-workflow-and-the-promotion-guard.md) (`promote.yml` and the guard),
[ADR-0031](0031-staging-smoke-test-and-the-passed-staging-record.md) (the record), `KAV-66`, Lab 35

## Context

ADR-0032 made `promote.yml` the only way prod's image tags change, and `promotion-guard` enforces it by
refusing any tag that is not on the passed-staging record. That is the right rule for going forward and the
wrong one for going back: the version prod ran before (`sha-ffb436b`) predates the record, so it is blocked,
and `promote.yml` itself calls an older tag "a rollback" and refuses it. Until this story the emergency exit
was shut. A rollback also has a property a promotion lacks: it is done in a hurry, when something is already
wrong, so every extra thing it depends on is a way for it to fail.

## Decision

**1. A separate workflow, `rollback.yml`, shaped like `promote.yml`.** A person dispatches it with a tag, a
reason and (rarely) an acknowledgement; a `gate` job (read-only AWS) decides; a `propose` job (no AWS) opens
a pull request that re-pins prod; merging is the go/no-go. Nothing touches the cluster. Same two-job split,
same OIDC role, same `main`-only rule as ADR-0032.

**2. The gate asks four questions** (`scripts/release/rollback.py verify`):

| Question | Why |
|---|---|
| Is the tag one **prod has run before** (it was prod's pin at an earlier commit on `main`) **or** one that passed staging? | A rollback goes to a version we already trusted. A new, untested version is a release and belongs to `promote.yml` |
| Is the tag an **ancestor** of what prod runs now? | Same tag: nothing to do. Newer or unrelated: a release |
| Do all **four images still exist in ECR** (and equal the recorded digests, if the tag is on the record)? | A tag whose image was deleted cannot be pulled. ECR's immutable tags are the guarantee for tags older than the record |
| Does going back **cross a database migration**? | See decision 4 |

**3. It never asks Jira.** `promote.yml` reads story status; `rollback.yml` does not. An emergency exit that
fails because Atlassian is down is not an exit. The `uat` sign-off was about whether to *release* a story;
a rollback returns to a version that was already accepted. If the gate cannot decide (AWS unreadable, a commit
missing) it still refuses and says so.

**4. Crossing a migration is refused unless the person says it was dealt with, because it cannot work
otherwise.** This was found on a real cluster (Lab 35), not assumed: every Helm upgrade first runs
`alembic upgrade head` from the gateway image. The *older* image does not contain the newer migration, so
against a database already at the newer revision it stops with `Can't locate revision identified by
'a1c4f9b0e3d2'`. The job failed four times, Helm gave up, and Flux restored the newer release itself. The
services never went down (the old pods were never touched), but the rollback did not happen. The working route
is to undo the migration first with the **newer** image (`alembic downgrade <previous revision>`), then roll
back; that is written up in `docs/runbooks/rollback-prod.md`. The workflow input `accept_migrations` means
"I have done that (or the database never reached that revision)". The gate cannot see the database, so it
asks a person.

**5. `promotion-guard` learns the same targets.** A change to prod's tags is allowed if the new tag is on the
record **or** was pinned in prod's files at an earlier commit on the base branch (`prod_history_tags`). The
history is read from the base, like the record, so a pull request cannot add its own proof. A tag that is
neither is still refused. The guard checks a tag is *legitimate*; whether going back is *wise* (direction,
images, database) is `rollback.yml`'s job, so a hand edit to a past tag passes the guard but skips those
checks. That is the same trade as ADR-0032 made for forward edits (the guard is a backstop, the workflow is
the process).

## Measured time-to-restore (rehearsed on staging, 2026-10-09)

Prod is parked, so the cluster half was measured on staging with the same Flux settings (one-minute checks).
A scratch Git branch stood in for `main`; the commit that re-pins the four images was pushed as the stand-in
for pressing Merge.

| Step | Time |
|---|---|
| Button pressed to rollback pull request open (`rollback.yml`, run on `main`) | **30 s** |
| Checks on the pull request | under 2 min (slowest job 47 s, plus a person approving the bot's first run) |
| Merge to Flux noticing the commit | **55 s** (a once-a-minute check) |
| Flux noticing to all four services Ready on the old tag | **25 s** |
| **Merge to everything healthy** | **about 80 s** |

So the machine-owned part, button to healthy, is roughly **4 to 5 minutes** with no waiting for a person;
the real figure is that plus however long the person takes to read and merge. Two-thirds of the post-merge time
is Flux's polling interval; a webhook would remove most of it and is not worth building at this size.
Caveat: staging's old images were already on the node or pulled quickly in-region; prod's node may need a pull,
and prod's database has real data to consider (decision 4). Staging brought up from nothing took 3 min 55 s.

## Alternatives considered

| Option | Why not |
|---|---|
| **Let `promote.yml` do both directions** | It would need the Jira check skipped for rollbacks and the direction check inverted: one workflow with two opposite rule sets is how a gate gets a hole. Two small workflows are easier to read and test |
| **Allow rolling back to any tag still in ECR** | Includes images that were never run or tested. The point of a rollback is a known-good version |
| **Require the target to be on the passed-staging record** | Blocks the very case that matters today: the version prod ran before the record existed |
| **Check Jira (`uat`) as in promotion** | See decision 3 |
| **Make the workflow run the database downgrade itself** | The workflow has no credentials to the cluster or database by design (ADR-0032). A downgrade drops data and needs a snapshot first; a person should choose that |
| **Make the migrate step tolerate a database ahead of the code** | Hides real mismatches in normal upgrades too; the right fix is expand/migrate/contract migrations (ADR-0004), which would make most rollbacks safe without any downgrade |
| **Auto-roll back when a deploy fails** | Flux already does this for a failed Helm upgrade (observed: it put the previous release back). Whether to go back after a *successful* deploy that is wrong is a human call |

## Consequences

**Easier.** There is a tested, reviewed, evidence-carrying way back, and it is not blocked by the guard or by
Jira. Time-to-restore is a measured number, not a hope.

**Harder / limits.**

- Migrations that are not backward-compatible make rollbacks a database operation. Until migrations follow
  expand/migrate/contract, each release that adds one needs the runbook on hand.
- "Prod has run it before" means "it was a prod pin on `main`". A hand-edit that slipped in before the guard
  existed would count; the history has been reviewed for that.
- For tags older than the record the gate cannot compare digests; it relies on ECR's immutable tags.
- The `accept_migrations` box is trust, not proof.
- Every bot pull request's first CI run still waits for a person to approve it.

**Revisit when** migrations become backward-compatible by rule, a second maintainer joins, or the
Flux polling interval stops being acceptable for a real outage.
