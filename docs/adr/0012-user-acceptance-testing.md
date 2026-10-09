# ADR-0012 — User acceptance testing: which stories, where, and a recorded sign-off

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** Roshan
- **Jira:** `KAV-34`

## Context

The delivery design ([ADR-0004](0004-environment-strategy-and-promotion.md)) had tests on every
PR, smoke and policy tests on staging, and a human go/no-go before prod. It had no **user
acceptance**: nobody confirmed that a feature does what its user needs before it ships. Roshan
raised it on 2026-09-28, from how releases work at their job.

Two different questions are easy to blur:

| | Asks | Who answers | Here |
|---|---|---|---|
| **Verification** | Was it built right? | Tests | CI on every PR; smoke + policy tests on staging |
| **Validation (UAT)** | Is it the right thing? | The user | Roshan, acting as the operator who'll approve fixes from a phone |

Tests can pass on a feature that's useless. An approval screen can work perfectly and still bury
the one fact the operator needs to decide. Only a person using it finds that.

The release go/no-go (`promote.yml`, Phase 4) is a third thing again. It approves a **release**,
the whole bundle. UAT accepts each **story** in it.

## Decision

1. **UAT is per story, and only where a user is affected.** A story gets the Jira label `uat` when
   it changes what an operator sees or decides:
   - API responses;
   - proposals and their explanations;
   - notifications;
   - mobile screens;
   - the approve/deny flow.

   Infra, CI, docs and refactors don't need UAT. Their verification is the tests.
2. **UAT scenarios are written before the work**, at *In Definition*, as their own checklist on
   the story ("UAT scenarios", Given / When / Then). They're kept separate from the Acceptance
   Criteria, which the developer checks. `jira-sync.py create --uat "…"` writes them and adds the
   label.
3. **No new status** (Roshan's call). The existing workflow carries it:

   | Status | Means, for a `uat` story |
   |---|---|
   | In Staging | Deployed to the UAT environment, automated checks passed, being accepted |
   | Ready for Prod | UAT signed off |

   A story without `uat` passes through In Staging on its automated checks alone.
4. **The UAT environment is staging** from Phase 4. Until staging exists (Phases 2–3), it's the
   dev server, and each sign-off says which one.
5. **A sign-off is recorded, never implied:**

   ```bash
   python scripts/tracking/jira-sync.py uat KAV-40 pass --env staging --note "what was checked"
   python scripts/tracking/jira-sync.py uat KAV-40 fail --env staging --note "what broke"
   ```

   - **Both** verdicts are refused unless the story is labelled `uat`, has UAT scenarios, and is
     *In Staging*.
   - **`pass`** ticks the UAT scenarios (and only those), comments "UAT passed on \<env\> by
     \<who\>, \<date\>: \<note\>", and moves the story to Ready for Prod. It's also refused while a
     linked UAT defect is still open.
   - **`fail`** comments the same way and raises a **Bug** with these properties:
     - labelled `uat-defect`;
     - in the story's epic;
     - Service copied from the story;
     - *Found in* set to the environment;
     - linked to the story;
     - in the current sprint.

     Then it moves the story back to In Progress.
6. **The gate (Phase 4, built 2026-10-09, [ADR-0032](0032-promote-workflow-and-the-promotion-guard.md)):**
   `promote.yml` refuses a release that contains a `uat` story not yet
   signed off, and the change record lists each sign-off: story, environment, who, when.

## Alternatives considered

| Option | Why not |
|---|---|
| **A separate "In UAT" status** | Clearer on a big team, where testers and developers need distinct queues. With one person it adds a column, and Roshan preferred reusing In Staging |
| **UAT on every story** | Most stories have no user-facing change. Accepting a CI tweak is ceremony, and ceremony teaches people to click through |
| **Xray or Zephyr** (test management apps) | No free tier: Xray Standard is $10/month even for one user. Test repositories and execution reports pay off with many testers and regression suites; one person's acceptance fits in a story checklist and a Jira dashboard |
| **"Approved" as a comment by hand** | Unqueryable and easy to forget. The command makes the verdict structured (status + comment + ticks) and refuses the cases that shouldn't pass |
| **UAT in prod, behind a feature flag** | Fine for mature teams. Here the promise is that nothing reaches prod unaccepted |

## Consequences

- A story's route to prod has an explicit human acceptance wherever a human is affected, and the
  evidence is on the issue.
- The **UAT** Jira dashboard (`KAV-36`) shows:
  - awaiting acceptance: `uat` stories In Staging;
  - signed off: Ready for Prod;
  - open defects: `uat-defect` Bugs.
- *Found in* on Bugs (dev · staging · prod) shows whether acceptance catches defects before prod:
  a bug found in staging is the process working, and one found in prod is a gap.
- Phase 5, the mobile app, is where most `uat` stories will be. Its screens are the product's user
  interface.
- The sign-off names the person whose token ran the command. With one person that's accurate.
  With a team, UAT would need a named acceptor per story, which is a revisit trigger.

## Revisit when

- A second person joins: separate the acceptor from the developer, and consider a distinct
  "In UAT" status.
- Regression suites grow past what a checklist holds: reconsider a test-management app.
