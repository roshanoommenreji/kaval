# Game-day post-mortems

One file per game day, named `<date>-<experiment>.md`, copied from [chaos/game-day-template.md](../../chaos/game-day-template.md). Blameless: what the system did, what changes.

| Date | Experiment | Environment | Result |
|---|---|---|---|
| 2026-10-11 | [oom](2026-10-11-oom.md) | staging | Hypothesis true: noticed in 81 s, fixed after approval, held. 158 s machine, 431 s wall-clock |
| 2026-10-11 | [badrelease](2026-10-11-badrelease.md) | staging | Hypothesis true: noticed in 66 s, rolled back on approval (not restarted), held. 94 s machine, 168 s wall-clock. Found: no readiness check means a bad release takes the whole service down |
| 2026-10-12 | [node](2026-10-12-node.md) (lose the app server) | staging | Hypothesis true: replaced by itself, every pod ready in about 2 min 10 s, earlier incident intact. Nothing could alert meanwhile. Found two mistakes in the test script itself, both failed shut |
| 2026-10-12 | [oom, second run](2026-10-11-oom.md#second-run-2026-10-12-the-label-fix-kav-80) | staging | The label fix seen live: incident `oom_killed`, not `crashloop`. 157 s machine, 231 s wall-clock |
