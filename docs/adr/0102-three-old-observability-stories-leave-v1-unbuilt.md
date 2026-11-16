# ADR-0102: Three old observability stories leave v1 unbuilt, and the go-public story closes

**Status:** Accepted on 2026-11-16. Roshan answered "do as recommended" to "close them with a note?" and to the badge question. *How* the three are closed (moved to the beyond-v1 epic, not marked Done) is the assistant's choice, for the reason in Decision 1, and the owner can change it. A documentation and board change: nothing was built, deployed or changed in AWS.
**Deciders:** Roshan
**Related:** [ADR-0042](0042-watching-the-watcher.md) (what was built instead), [ADR-0043](0043-alertmanager-not-adopted-yet.md), [ADR-0053](0053-the-still-alive-alarm-lives-in-aws.md), [ADR-0097](0097-phase-9-is-retired-slack-is-the-approval-surface.md), [ADR-0098](0098-phase-2-closes-jev-and-live-bedrock-escalation-move-to-future-scope.md) (the same pattern for two Phase 2 stories)
**Stories:** `KAV-52`, `KAV-53`, `KAV-54`, `KAV-45`

## Context

The v1 retrospective (`KAV-158`) found four stories that nobody had decided.

- `KAV-52` (Grafana, Loki and a Prometheus stack on the production server), `KAV-53` (Flux deployment notices by e-mail through Alertmanager and SES) and `KAV-54` (a custom page to see *and control* deployments) were filed in Phase 5. All three were still "To Do", with no roadmap line, 13 points between them. Some of their goal was met under other stories; the rest was never built, and nobody said so.
- `KAV-45` (go public early) had four of six boxes ticked. One of the two left (a CI badge) turned out to be **already done**: the README has carried the badge since the repository went public. The other (share the dashboard page) is **also already done**: the dashboard page is shared as "anyone with the link" (seen on 2026-11-16 when it was republished). What was never done is a homepage link on the GitHub repository.

## What real companies do

1. **Leave stale stories on the board.** Cheap; the board stops being believable.
2. **Close them as "won't do".** Honest, but counts the points as finished in most tools.
3. **Move them to a "later" list with the reason and the trigger written down.** What Kaval already does for the two Phase 2 stories ([ADR-0098](0098-phase-2-closes-jev-and-live-bedrock-escalation-move-to-future-scope.md)).

## Decision

1. **`KAV-52`, `KAV-53` and `KAV-54` leave Phase 5 and move to the beyond-v1 epic (`KAV-152`), still "To Do", with a note.** They are **not** marked Done: Jira has no "won't do" status, and "Done" would count 13 points as delivered work that was never built. Each now says what was built instead and what would bring it back (below). They are future scope [FS-16](../future-scope.md#fs-16--three-phase-5-stories-that-v1-did-not-need).
2. **`KAV-45` closes.** Its list is corrected to what is true (the badge exists and the dashboard is shared), and a homepage link on the GitHub repository becomes a note under [FS-2](../future-scope.md#fs-2--demo-craft): it is a public-facing choice for the owner, and not part of making the repository public.

| Story | What it asked for | What covers it today | What would bring it back |
|---|---|---|---|
| `KAV-52` | Prometheus, Grafana and Loki on the production server, with Flux's own dashboards, reached through the session tunnel | Prometheus runs in the cluster and is asked a question every 30 seconds ([ADR-0040](0040-prometheus-as-a-polled-signal-source.md)); Kaval's own health dashboard is a Grafana file in git, shown where it is needed ([ADR-0042](0042-watching-the-watcher.md)). The production server's spare memory went to the model (872 MB free at its worst moment, [ADR-0055](0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md)), so Loki on the node was never affordable | A second server, or a need to read logs without a laptop |
| `KAV-53` | Alertmanager e-mailing Flux failures and recoveries through SES | Alertmanager was recorded as *not adopted yet* ([ADR-0043](0043-alertmanager-not-adopted-yet.md)); Kaval's own problems reach Slack straight from the signals, the stopped-reporting alarm e-mails from AWS ([ADR-0053](0053-the-still-alive-alarm-lives-in-aws.md)). A stalled Flux release is reset by hand ([ADR-0076](0076-a-stalled-flux-release-is-reset-not-rebuilt.md)) | A Flux release that stalled unnoticed, or a second maintainer |
| `KAV-54` | A page that shows deployment state and can *act* on it | Never scoped. Its home was to be the phone app, which is retired ([ADR-0097](0097-phase-9-is-retired-slack-is-the-approval-surface.md)); approval, the one control that matters, is in Slack. Promotion and rollback are pull requests and workflows | A real need to act on a release without the command line |

## Planning standard

Nothing runs, so there is no path, security, failure or cost to plan beyond: **$0, no AWS change, no release round** (the diff touches nothing `release.yml` builds from). People: Roshan owns FS-16's triggers.

## Accepted, and said plainly

- **Phase 5's figures change.** With the three out, Phase 5 is 14 of 14 stories and 59 of 59 points, and v1 is 130 stories, 402 points. The retrospective's totals (133 and 415) are refreshed when `KAV-158` closes; until then they read the board as it stood on 2026-11-16 morning.
- **`KAV-45` closes with no homepage link on the repository.** Setting one (for example to the shared dashboard) is the owner's choice at any time (FS-2).

## Consequences

- The board has no stale v1 stories left among these four. `KAV-100`, `KAV-154` and its three follow-ups, `KAV-159`, `KAV-89`, `KAV-150` and `KAV-158` are the open ones, each with a reason.
- ADR-0076's sentence "Flux notifications by email are the existing, not started story `KAV-53`" is amended to point here.
