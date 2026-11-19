# ADR-0105: Version 1 closes, and the nightly drift check moves to future scope

**Status:** Accepted on 2026-11-19. Roshan answered "go, move KAV-165 to future scope" to the choice of building the drift check first or closing v1 without it. A documentation and board change: nothing was built, deployed or changed in AWS.
**Deciders:** Roshan
**Related:** [ADR-0104](0104-a-pull-request-shows-its-terraform-plan-through-a-read-only-role.md) (the plan on pull requests, which this story would have extended), [ADR-0098](0098-phase-2-closes-jev-and-live-bedrock-escalation-move-to-future-scope.md) and [ADR-0102](0102-three-old-observability-stories-leave-v1-unbuilt.md) (the same pattern), [ADR-0099](0099-roadmap-clean-up-what-v1-still-needs.md) (what v1 still needed)
**Stories:** `KAV-158`, `KAV-165`

## Context

On 2026-11-19, with `KAV-159` (the secret rotation rehearsal) merged, Jira showed Phases 0 to 8 at 134 of 136 stories done. Two were left:

- `KAV-158`, this close-out: the retrospective's final figures, the Phase 8 learn page, and the Phase 8 and version 1 markers in the roadmap.
- `KAV-165`, filed on 2026-11-18 when `KAV-89` was proved: a scheduled, read-only Terraform plan that refreshes from the live account, so a console change made by hand is reported. `KAV-89`'s plan compares the code with Terraform's record and does not refresh, so it cannot see one. Not started.

The retrospective's "still open" table (written 2026-11-16) was out of date: `KAV-147`, `KAV-100`, `KAV-154`, `KAV-89`, `KAV-150` and `KAV-162` had all closed since.

## What real companies do

1. **Close the release when the committed scope is done and write the rest down.** The usual way: a release is a promise about a list, and a new idea found late goes on the next list.
2. **Keep adding to the release until the new finds stop.** Never closes; a safety phase finds more safety work every week (Phase 8 grew from one story to 49).
3. **Build the follow-up first because it is small.** Fine when it is small. This one is not: it needs a second login for CI that can read the live account.

## Decision

1. **`KAV-165` leaves Phase 8 and moves to the beyond-v1 epic (`KAV-152`), still "To Do".** It is not marked Done (Jira has no "won't do", and Done would count 3 points of unbuilt work). It is future scope [FS-17](../future-scope.md#fs-17--a-nightly-check-for-changes-made-by-hand-in-the-aws-account), with what covers it today and a written trigger.
2. **Phase 8 closes, and with it version 1.** Phases 0 to 8 are 135 stories and 411 points, all done once `KAV-158` is. The roadmap marks Phase 8 `[x]`, the retrospective carries the final figures, and the Phase 8 learn page is rewritten from what happened (`Written from: experience`).

## Why not build it first

- It is a new feature, not a close-out. The plan-showing login made for `KAV-89` can read only Terraform's record; a refreshing plan needs read access across the account, which is a reviewed Terraform change, a staging proof and his typed go, then a place for the answer to go.
- What it guards against is thin here. There is one person with access to the account, every apply on the laptop already refreshes and shows its plan first, and two read-only checks of the live account can be run by hand at any time (`make tag-scan` and `make iam-review`). A hand change would be his own and would show at the next apply.
- Holding v1 open for it would repeat what the retrospective names as the first lesson: a safety phase's first estimate is a floor.

## Planning standard

Nothing runs, so there is no path, security, failure or cost to plan beyond: **$0, no AWS change, no release round** (the diff touches nothing `release.yml` builds from). People: Roshan owns FS-17's trigger.

## Accepted, and said plainly

- **A console change made by hand is not reported between applies.** It shows up as a difference in the next plan, which is shown before any apply. That is a delay, not a blind spot, and it is the limit the plan-on-pull-request page already states.
- **Version 1 closes with this one safety idea unbuilt.** It is written down with a trigger rather than left on the board.

## Consequences

- Phase 8 shows 49 stories, 105 points, all done. `KAV-152` now holds seven stories, none built.
- ADR-0104's sentence "a nightly drift check is a separate story, `KAV-165`" is amended to point here.
- The retrospective's "still open" section becomes "closed on 2026-11-19" and carries no open items except the future-scope list.
