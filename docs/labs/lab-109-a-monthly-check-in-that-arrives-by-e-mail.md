# Lab 109 — A monthly check-in that arrives by e-mail

**Phase:** 8 (hardening; written after version 1 closed, in the "keeping watch" group, [ADR-0106](../adr/0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md)) · **Story:** `KAV-168` · **Decision:** [ADR-0106](../adr/0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md) · **Status:** built and proved live 2026-11-20

## What you will build

A runbook that says what a person reads on the first of each month, a table of things that fall due on a date, and a scheduled job that opens one GitHub issue with that checklist so it arrives by e-mail. No AWS, no secret, $0.

You need the repository and a GitHub account that can run workflows by hand. Nothing else.

## Why

Kaval is parked most of the time, and its scanners (Dependabot, CodeQL, the Scorecard, the backup check, the weekly image watch) are quiet unless someone reads them. A scanner nobody reads is the same as no scanner. Real companies handle this with a recurring review ticket or a calendar slot; the cheapest version that cannot be forgotten is an issue GitHub e-mails you. A Google Calendar reminder was offered and declined, so the issue is the only reminder and the checklist has to be good enough to rely on.

The check-in also covers the one failure no scanner reports. GitHub's own documentation says: "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days." It does not say what counts as activity. If that happened, every scheduled job here would stop, this one included, with nothing to say so. So step 1 of the checklist is to look, and **an issue that does not arrive on the 1st is itself the signal** to open the Actions tab.

## Part 1 — The runbook

`docs/runbooks/monthly-check-in.md`: eight steps in order (are the scheduled runs alive; the weekly image watch; Dependabot pull requests and alerts; the Scorecard; CodeQL; the bill with `make cost-report`; a restore drill in the last 35 days; anything dated that is near) and a table of dated reminders:

| Due | What |
|---|---|
| Every month | A restore drill on the newest dump (`make restore-drill`; read-only on AWS, about a minute) |
| Every 90 days, next by 2027-02-14 | Change the AWS access key on the laptop |
| Each November, first by November 2027 | Change every other secret, on staging first |
| Every three months | Start production, smoke test it, take a fresh dump, park it (about 2 cents; needs its own typed go) |
| After `KAV-169` | Check the weekly image watch went green and closed its issue by itself |

The restore drill is monthly because the ownership page and the backup check already say so (the check raises an issue after 35 days). It needs a dump, not a running production, so it does not wait for a start-up.

## Part 2 — The workflow

`.github/workflows/monthly-check-in.yml`: one job, on the 1st at 03:17 UTC (08:47 in India) and by hand. Its token starts with nothing and the job gets `issues: write` and nothing else. It does not check out the code, has no AWS login and no secret, and no expression is pasted into its shell script (nothing in the issue text comes from outside the file).

- It makes the label `monthly-check-in` if missing.
- It opens **one** issue titled "Monthly check-in YYYY-MM" with the checklist as tick boxes. If an issue for that month already exists (a hand re-run), it does nothing.
- If earlier check-in issues are still open, the new one says so at the top. An issue left unread is what this exists to catch.

## Part 3 — Tests

`scripts/dev/test_monthly_check_in_workflow.py` (6 guards): runs only on the 1st and by hand with no input; the token starts empty and the one job can only write issues; no AWS login, no secret, no checkout; no `${{` inside the script; one issue a month and older open ones called out; the runbook exists and holds the key dates and the `gh workflow list` check.

```bash
python -m pytest scripts/dev/test_monthly_check_in_workflow.py -q
```

Also `bash -n` on the extracted script.

## Part 4 — Live proof (after the merge)

A workflow can only be started by hand once it is on the default branch. After the merge:

1. Actions, `monthly-check-in`, Run workflow. Expect one issue, "Monthly check-in 2026-11", with eight boxes and the `monthly-check-in` label.
2. Run it again. Expect **no** second issue and the log line "already exists".
3. Work the real checklist once, tick the boxes, close the issue.

Recorded 2026-11-20 (after #471 merged):

| Run | What happened |
|---|---|
| Hand run 1 | Succeeded in a few seconds; opened issue #472, "Monthly check-in 2026-11", with the label and the eight boxes |
| Hand run 2 | Succeeded; log line "an issue for 2026-11 already exists; nothing to do"; still exactly one issue |

The first real pass of the checklist was done the same day, on the GitHub side only: every workflow active; image-watch red (the three chart images, `KAV-169`), CodeQL, Scorecard and the backup check recent and green; no Dependabot pull requests; 0 open Dependabot alerts; no open CodeQL alerts. The Scorecard's own list holds four old best-practice items and one "vulnerabilities" alert (an OpenTelemetry advisory published 2026-11-13) that is already fixed in `uv.lock` (1.45.2), so it clears at the next Scorecard run. The bill and the restore drill (last one 2026-11-12, due before 2026-12-17) were left for the person doing the check-in.

## What it cannot do

- It cannot notice that it was switched off (see Why). The only protection is a person noticing a missing e-mail.
- It cannot read the Scorecard, Dependabot or the bill for you; it hands you the list. Reading is the point.
