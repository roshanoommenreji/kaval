# Retrospective: version 1 (Phases 0 to 8)

For `KAV-158`. Written on 2026-11-16 from Jira (read that day) and the daily journal, while version
1's last pieces are still open (the list is at the bottom). Numbers come from those two sources and
nothing else. Where a number moves when the open pieces close, the section says so.

## The short version

Kaval was planned as ten phases and built as nine: the phone app (Phase 9) was dropped on purpose.
Of the 133 stories filed under the nine phases, 122 are done. The plan on paper was small and the
work that grew was safety: Phase 8 started as one story and ended as 44, and 43 of them were filed
after the day the planning standard was written. The money limit held: the first two months used
$1.39 and $15.31 of AWS usage, all covered by credits, against a ceiling of $50 a month.

## The numbers

| | |
|---|---|
| Stories in the nine phases | 133 (122 done, 11 not) |
| Story points | 415 planned, 385 done |
| Calendar span | first story filed 2026-09-14, planning from 2026-08-22, last story done so far 2026-11-16 |
| Stories finished per month | September 22, October 52, November 47 (to the 16th) |
| Commits and pull requests | about 1,050 commits, pull requests numbered to #439 |
| Written down | 101 labs, 100 decision records, 27 runbooks, 52 journal days, 6 game-day write-ups |
| Money | usage $1.39 (September), $15.31 (October); billed $0.00; November is not closed |

## What was planned and what happened, phase by phase

Dates are the first story filed and the last story done in that phase. Phases overlapped, so the
spans overlap.

| Phase | Done / filed | Points done / planned | Span | What changed against the plan |
|---|---|---|---|---|
| 0 Foundations | 5 / 5 | 9 / 9 | 14 Sep to 28 Sep | Nothing. |
| 1 Local first | 10 / 10 | 24 / 24 | 17 Sep to 27 Sep | Nothing. |
| 2 The agent loop | 8 / 8 | 40 / 40 | 28 Sep to 30 Sep | Two pieces parked, not dropped: a second model that rates risk (Jev) and a live hand-off to a larger model. Both wait on something outside the project ([ADR-0098](adr/0098-phase-2-closes-jev-and-live-bedrock-escalation-move-to-future-scope.md)). |
| 3 Kubernetes local | 5 / 5 | 21 / 21 | 1 Oct to 11 Oct | Nothing. |
| 4 AWS landing | 23 / 23 | 84 / 84 | 26 Sep to 31 Oct | The biggest phase by points. The cost ceiling moved twice here: $25 to $40 ([ADR-0008](adr/0008-production-database-on-its-own-server.md)), then to $50 ([ADR-0028](adr/0028-prod-app-node-on-demand-and-ceiling-50.md)) when cheap spare capacity ran out twice. |
| 5 Chaos and proof | 14 / 17 | 59 / 72 | 2 Oct to 28 Oct | Three stories never built as written: Grafana and Loki (`KAV-52`), emailed deploy notices (`KAV-53`) and a deployment-status dashboard (`KAV-54`). What was built instead was Prometheus watching Kaval's own health ([ADR-0042](adr/0042-watching-the-watcher.md)). |
| 6 FinOps | 8 / 9 | 32 / 35 | 16 Oct to 28 Oct | One story waits on a live production session (`KAV-100`). |
| 7 EKS chapter | 11 / 12 | 31 / 34 | 16 Oct to 8 Nov | One story left on purpose in v1: the Terraform plan shown on every pull request (`KAV-89`). |
| 8 Harden and publish | 38 / 44 | 85 / 96 | 30 Sep to 16 Nov | Grew from one story to 44 (below). |
| 9 Mobile app | retired | none | none | Put off on 2026-10-03 ([ADR-0026](adr/0026-slack-chatops-and-deferred-mobile.md)), retired on 2026-11-15 ([ADR-0097](adr/0097-phase-9-is-retired-slack-is-the-approval-surface.md)): Slack on a phone already shows Approve and Deny. |

## The five things that did not go to plan

1. **Phase 8 grew from one story to 44.** The count was one on 11 October, the day the planning
   standard was written ([CLAUDE.md](../CLAUDE.md)), and 43 of the 44 were filed after it. The
   standard asks every plan what the whole path is, what an attacker could do, and how it fails.
   Asking those questions turned "make it safe" into a signed-image pipeline (eight stories), a
   threat model and its fixes, an access review, secret rotation and more. The work was worth
   doing; the lesson is that the first estimate of a safety phase is a floor.
2. **A phase was dropped, and the record says why.** The phone app was deferred first and retired
   five weeks later, each time with a decision record. Slack did the job, and the app would have
   needed the system's first way in from the internet.
3. **The cost ceiling moved, in writing.** From $25 to $50 in two steps, each with a record, and
   the real bill stayed far below it. The ceiling was a design constraint that was allowed to be
   argued with, not a fixed number.
4. **Three board stories were left behind.** `KAV-52`, `KAV-53` and `KAV-54` are still "To Do" in
   Jira with no roadmap line. Some of what they described was built under other stories
   (Prometheus; the dashboard), the rest was not, and nobody closed them. `KAV-45` (go public early) has four of six boxes ticked; the other two
   (a build badge, and sharing the dashboard page) were never done. A board that lags the repo is
   what the project's own rules warn about, so these are listed in "Still open" below.
5. **Production stayed parked, by design.** It runs for sessions and is switched off between them.
   That kept the bill low and means long-running behaviour has been tested in sessions, not in
   months. It is the first limit to say out loud about the project.

## What the record says worked

- **Proof before claim.** A restore drill on 8 November failed on a database dump that was
  twelve days old ([the drill record](drills/restore-failed-2026-11-08.md)). That produced a
  final dump at every park (`KAV-134`), and the next drill passed. A backup nobody has restored is
  a guess.
- **A real environment before the real thing.** A change to the gateway's image failed on staging on
  12 November (the migration job could not find its settings), nothing was promoted, and the fix
  went through a second round. Staging cost cents and caught what the tests could not.
- **Writing things down at the time.** Fifty-two journal days, 100 decision records and six
  game-day write-ups meant most of this page could be read off, not remembered.
- **Sweeping for stale facts.** Several times the sweep found an old fact in code, not only prose
  (a cost gauge on 26 September; the dashboard's phase links, one phase out, on 28 October; Phase 5
  pointed at the wrong Jira epic until 13 November). Each was cheap to fix once found and would
  have misled a reader if not.

## What the record says to do differently

These are drawn from the journal, not from hindsight, and are for the owner to edit.

- **File the safety stories at the start of a phase, not as they are found.** Most of Phase 8's
  growth could have been seen on 11 October if the threat model had been written first.
- **Keep the board in step day by day.** It lagged the repo on 5 October (a story showing 0 of 12
  boxes while done), on 28 October (phase links one out) and on 13 November (Phase 5 on the wrong
  epic), and again on the three stories above.
- **Check a claim against its source before saying it.** The journal records several of the
  assistant's own slips caught in time: reading the wrong place and so saying a message was never
  sent (28 October), a watcher looking in the wrong namespace (4 November), a script that would have
  changed one of the attack test cases (10 November), and a first demo plan that showed a Slack card
  from an environment that has no Slack (16 November). All were caught by looking, not by luck.
- **Plan the rollout when the thing is built, not later.** The planning standard exists because
  Prometheus was built and proved locally with no plan for staging or production
  (2026-10-11).

## What v1 still lacks, said plainly

- A small model, and a person approves everything: no action class is automatic
  (`policy/promotions.json` is empty).
- One operator, one AWS account, nobody else's hands on it.
- The real Slack approval and denial pressed in production on 2026-11-16 are recorded by that
  session's own write-up (`KAV-147`), not here.
- A demo video was not recorded; it is parked in [future scope](future-scope.md#fs-2--demo-craft).

## Still open at this writing

| Story | What is left |
|---|---|
| `KAV-147`, `KAV-100` | The production session of 2026-11-16: its last proofs, the parking of production, and the write-up |
| `KAV-154` | The AWS half of the access review (the cluster half is done) |
| `KAV-159` | A secret rotation rehearsed on staging |
| `KAV-89` | The Terraform plan on pull requests |
| `KAV-150` | Four stale comments, with the next gateway and executor release |
| `KAV-45`, `KAV-52`, `KAV-53`, `KAV-54` | Board housekeeping: decide each one (close, or say why it stays) |
| `KAV-158` | This page's final figures, the Phase 8 learn page, and the Phase 8 and v1 markers, once the rows above close |

When those close, update the numbers in the two tables at the top and the row for Phase 8, then
flip the Phase 8 page and the roadmap's Phase 8 marker.
