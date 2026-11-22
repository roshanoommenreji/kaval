# The monthly check-in

On the first of each month a GitHub issue titled "Monthly check-in" arrives by e-mail
([ADR-0106](../adr/0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md), `KAV-168`). This
page is what to do with it. It takes about fifteen minutes. Nothing here starts production or changes AWS.

Kaval is parked between sessions, so nothing breaks loudly; the scanners only help if a person reads what they
say. This is that reading, done on a schedule so it does not depend on remembering.

## Signals

- The GitHub issue "Monthly check-in", labelled `monthly-check-in`, opened on the 1st at about 08:47 India time
- **No issue by the 2nd is itself a signal:** GitHub switches scheduled workflows off after 60 days without
  repository activity (see step 1). Open the Actions tab

## The checklist

Do these in order. The issue carries the same list as tick boxes; tick them there and close the issue when done.

1. **Are the scheduled runs still alive?** In a public repository GitHub disables scheduled workflows after 60
   days with no repository activity. Run `gh workflow list --all`: every workflow should say `active`, and
   `gh run list --workflow image-watch --limit 1` (and `codeql`, `scorecard`, `backup-watch`) should show a run
   from the last week. If one says `disabled_inactivity`, switch it on again (`gh workflow enable <name>`, or
   Actions, the workflow, "Enable workflow") and run it once by hand.
2. **The weekly image watch.** Is the issue "Weekly image watch needs a person" open? If so, open it and follow
   [image-watch-finding](image-watch-finding.md). It closes by itself when nothing serious with a fix is left, so an open one is new: a pinned image or
   library needs a newer release. The three chart images (pgvector, Ollama, Prometheus) are moved by Dependabot's
   pull requests (`KAV-170`, [third-party-image-bump](third-party-image-bump.md)).
3. **Dependabot.** Pull requests: `gh pr list --author "app/dependabot"`. Merge or close each (a merge that
   touches a service starts a release round, so read before merging). Alerts: `gh api
   repos/{owner}/{repo}/dependabot/alerts --jq '[.[]|select(.state=="open")]|length'` should be 0, or each one
   should have a reason. Then Actions, "Dependabot Updates": the run for `/deploy/third-party-images` is green and
   its log names all three images (pgvector, Ollama, Prometheus); if it does not, the bot has stopped reading the file.
4. **The Scorecard.** Read the newest score on the repository's Security tab (or the Scorecard badge). A drop of
   more than a point since last month has a cause worth finding.
5. **CodeQL.** The Security tab's code scanning list: any open alert is read, then fixed or dismissed with a reason.
6. **The bill.** `make cost-report` (month to date, against the $50 ceiling). Parked, it should be a few dollars.
   Anything near $20 with production parked is [cost-spike](cost-spike.md).
7. **The restore drill.** `docs/drills/` holds the newest `restore-<date>.md`. The backup check raises an issue
   when it is older than 35 days; do not wait for it. `make restore-drill` is read-only on AWS, needs Docker and
   takes about a minute ([restore-drill](restore-drill.md)). It uses the newest dump, so production need not run.
8. **Anything dated is near?** The table below. Do what is due in the next 30 days, or write down why not.

## Dated reminders

| Due | What | Where it is written |
|---|---|---|
| Every month | A restore drill on the newest dump (step 7) | [restore-drill](restore-drill.md) |
| Every 90 days: next by **2027-02-14** | Change the AWS access key on the laptop (done 2026-11-16) | [secret-rotation](../architecture/secret-rotation.md), [ADR-0100](../adr/0100-secrets-are-changed-yearly-and-the-laptop-key-every-90-days.md) |
| Each November: first by **November 2027** | Change every other secret, on staging first (rehearsed 2026-11-19, never yet done on production) | [secret-rotation runbook](secret-rotation.md) |
| **2026-12-04** and **2026-12-18** | The dated Trivy exceptions for the chart images end (`.github/trivy-ignore.yaml`). **Look for a newer release first**: Dependabot may already have proposed it | [third-party-image-bump](third-party-image-bump.md), [ADR-0107](../adr/0107-the-chart-images-move-to-their-newest-releases-and-the-leftovers-get-end-dates.md) |
| Every three months | Start production up, run `make prod-smoke`, take a fresh dump, park it again (about 2 cents). Proves the thing still starts, keeps the restore drill honest and lets the images get fixed ones | [prod-rollout](prod-rollout.md) |

Any production start-up needs Roshan's own typed go, as always; this table only says when to think about one.

## Likely causes when a step fails

1. **A workflow is disabled** (step 1): sixty quiet days. Re-enable it. GitHub does not say what counts as
   activity, so this can happen again; that is why step 1 comes first
2. **The watch is red** (step 2): a pinned image or library has a serious finding with a fix; follow
   [image-watch-finding](image-watch-finding.md) (for the three chart images, look for a Dependabot pull request first)
3. **A Dependabot pull request fails CI:** read the failing check before merging; often a flaky download, re-run it
4. **The drill is older than 35 days:** it is simply due (step 7)

## Do not

- Do not close the monthly issue without doing the list; an issue closed unread is the failure this exists to catch
- Do not merge every Dependabot pull request without reading it: one that touches a service starts a release round
- Do not start production only because a table row says so; each start-up needs its own typed go
