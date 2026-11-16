# ADR-0099: The roadmap clean-up: what is finished, what is parked, and what v1 still needs

**Status:** Accepted on 2026-11-15 (Roshan: "can we do clean up pass now?"; he chose which items leave v1). A documentation and tracking change: nothing is built, deployed or changed in AWS.
**Deciders:** Roshan
**Related:** [ADR-0097](0097-phase-9-is-retired-slack-is-the-approval-surface.md) (Phase 9 retired), [ADR-0098](0098-phase-2-closes-jev-and-live-bedrock-escalation-move-to-future-scope.md) (Phase 2 closed), [ADR-0062](0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md) (the waste scan), [ADR-0032](0032-promote-workflow-and-the-promotion-guard.md) (the promotion gate), [ADR-0095](0095-production-refuses-unsigned-images-from-its-next-start-up.md) (`KAV-147`'s leftover)
**Story:** `KAV-153`

## Context

After Phase 9 was retired and Phase 2 closed, the roadmap still carried 31 open lines in Phases 4, 5, 6 and 8, and the dashboard read "current phase: 4" while the real work is in Phase 8. Phase 6 was marked "not started" although its exit gate passed on 2026-10-22, and Phase 8 was marked "not started" with most of its lines ticked.

Roshan asked whether tomorrow's production session (`KAV-147`) would close v1. It would not, and nobody could say from the roadmap what would. So each open line was checked against the repo, the labs and Jira on 2026-11-15.

What the check found:

- **15 lines were finished and only unticked.** Most were written as "half done" when one production-only proof was still open, and that proof arrived later under another story without the line being revisited.
- **4 lines wait on one production session,** the one already planned for `KAV-147`.
- **4 items were never built and v1 does not need them.** These were Roshan's decision.
- **The rest is real remaining work,** and five of those lines had no Jira story.

## What real companies do before a release milestone

1. **Leave the backlog as it is** and close the milestone "by feel". Quick, and the board keeps lying.
2. **Scope triage (a release readiness review):** every open item gets one of three answers: done, still in, or moved out with a reason and a trigger. The usual practice; the milestone then closes on a short, true list.
3. **Declare v1 done now** and move everything open to v2. Fast, but it would drop work that is cheap and that the exit gate names (the video, the bullets).

## Decision

**Option 2.** Every open line gets one answer, recorded here.

### Finished, ticked (15)

| Phase | Line | Evidence |
|---|---|---|
| 4 | Database backups | The 19:30 UTC timer was seen firing by itself on 2026-10-28 (`KAV-90`, Lab 64) |
| 4 | `release.yml` | All its sub-lines are done; every release since `v0.2.0` has gone through it |
| 5 | Five chaos types | Exit review of 2026-10-16; the production part closed 2026-10-27/28 |
| 5 | Time to restore, measured and charted | `mttr.svg`, seven recorded runs. Production has no figure of its own, because no real fix has happened there; that is said on the line |
| 5 | Missing-dump alert and restore drill | The last gap (no dump on parked nights) closed with `KAV-134` on 2026-11-12 |
| 6 | Cost Explorer ingestion | Live in production since 2026-10-21 (`KAV-92`, `KAV-98`) |
| 6 | Tagging strategy and enforcement | 0 of 124 resources missing a label in production (`KAV-93`) |
| 6 | Read-only role for the cost reader | Built (`KAV-92`); the remover's allow-list came with `KAV-95`. The permission boundary was not attached: see `KAV-154` below |
| 6 | Tag policy and the show-back report | Built, and the first real report ran (`KAV-93`, `KAV-94`) |
| 8 | Security pass: Trivy and image signing | `KAV-137` to `KAV-144`. Its RBAC audit moved to `KAV-154` |
| 8 | gitleaks over the full history | A CI job on every pull request |
| 8 | Architecture diagrams | Generated from `architecture.toml` into the dashboard |
| 8 | Repo public | Since 2026-09-30 |
| 8 | Threat-model fixes | `KAV-113` to `KAV-117` are all Done; the line's text for `KAV-117` was stale |
| 8 | Every release carries a version tag | `KAV-135`, Done |
| 8 | Disaster recovery stated and drilled | `disaster-recovery.md`; drills passed on real production dumps on 8, 10 and 12 November |

(The table has 16 rows because the old "Security pass" line was split: its finished half is ticked and its unfinished half joined the access review.)

### Waiting on the `KAV-147` production session (4)

| Line | What closes it |
|---|---|
| Supply chain (`KAV-147`) | Production left up past six hours, then the checker's registry login read and a signed pod accepted |
| Approvals nobody answers (Phase 5) | One button pressed on a real production card |
| Reminders sent once with two gateway copies (`KAV-100`) | A real open approval going through a production session |
| Waste detection (Phase 6) | The scheduled 03:15 UTC scan seen to have run by itself in production (a read-only look) |

**Result (2026-11-16, `KAV-147`):** three of the four closed (the checker's login refresh, a real button pressed, the scheduled scan seen). `KAV-100` stays open: no count of reminders per question was recorded.

Two of these need a real card to appear, which depends on the scan finding something. If none appears, they stay open and that is said, not worked around: nothing fake is put into production's database.

### Moved to future scope, as FS-14 (3)

| Item | Why v1 does not need it | Trigger |
|---|---|---|
| Cost and Usage Report ingestion (line items in S3) | Cost Explorer's daily figures found the spikes, and waste detection was built without line items (ADR-0062 said so already) | A cost question the per-service daily bill cannot answer |
| A live example of the server-overstay finding | The finding is unit-tested. A live run needs staging awake past 12 hours, which fights the nightly 02:00 IST stop, for about 55 cents | A staging session that has to run that long for another reason |
| GitHub Environments with required reviewers on promotion | The owner already approves each promotion by merging its pull request. On a one-person repo a second gate adds a click, not a second person | A second maintainer |

### Kept in v1 by the owner's choice

**The Terraform plan on every pull request (`KAV-89`).** It was offered for future scope and Roshan kept it: seeing what an infrastructure change will do before it merges is a standard part of a real pipeline, and the read-only role it needs follows directly from `KAV-86`.

### What v1 still needs

| Work | Story |
|---|---|
| The production session above | `KAV-147`, `KAV-100` |
| Access review: RBAC audit, IAM Access Analyzer, a permission boundary on the roles that act | `KAV-154` |
| The secret rotation schedule and the ownership page | `KAV-155` |
| Terraform plan on pull requests | `KAV-89` |
| README for a public audience | `KAV-156` |
| Demo video and resume bullets | `KAV-157` (the video moved to future scope 2026-11-16, FS-2) |
| Jira retrospective, course outline, the Phase 8 learn page | `KAV-158` |
| Four stale comments in the service code | `KAV-150`, with the next gateway and executor release |

### Phase markers

Phase 4 is `[x]`. Phases 5 and 6 are `[~]` (were `[~]` and `[ ]`): each waits only on the production session. Phase 8 is `[~]` (was `[ ]`). The dashboard's current phase therefore reads 5 until that session, then 8.

## Planning standard

- **The whole path:** no code that runs in a cluster changes, so there is no local, staging and production rollout and no release round. The diff touches no path that `release.yml` builds from.
- **Security:** one real gap was found and is now tracked instead of hidden in a half-ticked line: the role that removes waste has an allow-list but no permission boundary (`KAV-154`). Nothing is loosened.
- **Failure:** the risk of a clean-up is ticking something that is not done. Every tick names its evidence in the table above, and anything with a proof still open stays `[~]`.
- **Observability:** the dashboard and Jira now say the same thing as the repo.
- **Cost:** $0. FS-14's overstay proof would cost about 55 cents if ever run.
- **Testing:** the dashboard's tests hold that Phase 4 is closed and is not the current phase; `make test`, `make lint` and a fresh `make docs-sync`.
- **People:** Roshan owns the remaining stories and FS-14's triggers.

## Accepted, and said plainly

- **Production has no time-to-restore figure of its own.** The chart is a staging figure. That line is ticked because the measurement and the chart exist, not because production was measured.
- **The server-overstay finding has never been seen live.** It is tested, and it is the one waste finding with no live example.
- **The Cost and Usage Report was in Phase 6's first line from the start and was never built.** The line is now named for what it delivered.

## Rejected

- **Declaring v1 done now.** The exit gate names the video and the bullets, and the access review is the one security item nobody has done.
- **Ticking the four production-session lines early** because the session is tomorrow. A tick is a record of something seen.
- **Moving `KAV-89` out** against the owner's choice.
- **One future-scope entry per parked item.** Three small items with one line of reasoning each do not need three sections; FS-14 holds them together.

## Consequences

- The roadmap's open list is 9 not-started lines and 5 half-done ones, each with a story, down from 31.
- Phase 4's epic (`KAV-9`) closes. Phases 5 and 6 close after the production session if their one proof each is seen.
- The next clean-up of this kind should not be needed: when a later story supplies a proof an older line was waiting for, that line is ticked in the same change.
