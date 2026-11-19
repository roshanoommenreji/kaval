# ADR-0106: A weekly scan of what production runs, and a monthly check-in

**Status:** Accepted on 2026-11-19. Roshan asked how new vulnerabilities would be found while Kaval is parked, was shown the plan (a weekly scan and a monthly check-in), and answered "go", "separate" (the work sits outside the nine phases) and "no" (no Google Calendar reminder).
**Deciders:** Roshan
**Related:** [ADR-0105](0105-version-1-closes-and-the-nightly-drift-check-moves-to-future-scope.md) (why a second CI login into AWS was declined), [ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md) (dated, scoped scan exceptions), [ADR-0049](0049-backups-watched-and-restores-drilled.md) (the daily backup check this copies the shape of), [ADR-0093](0093-the-cluster-checks-image-signatures-with-kyverno-watching-first.md) (the cluster refuses unsigned images)
**Stories:** `KAV-167` (the weekly image watch), `KAV-168` (the monthly check-in), epic `KAV-166`

## Context

Version 1 closed on 2026-11-19 and Kaval is parked between sessions. The question Roshan asked: if nobody is changing anything, how will a newly found vulnerability, or a component that turns out to be harmful, be noticed?

What already watches, with no one touching it:

| Watcher | When | Covers |
|---|---|---|
| Dependabot (updates and security alerts) | Saturdays, and on an advisory | Libraries, base images, build steps and Terraform modules in git |
| CodeQL | Mondays 04:41 UTC | Kaval's own code and workflow files |
| OpenSSF Scorecard | Sundays 03:17 UTC | The repository's own habits |
| Secret scanning with push protection | Always | A pushed secret |
| IAM Access Analyzer | Always, in AWS | What outsiders can reach in the account |
| The daily backup check | Daily | The nightly database dump |
| The cluster's signature check | At every pod start | An image not made by our release workflow |

The gap: **Trivy, which scans the finished images for known problems, runs only when something changes** (`ci.yml` on a pull request, `release.yml` on a release). A flaw announced after a release is not seen until the next change, and Kaval changes rarely now. Dependabot reads the libraries and alerts on them, but it does not look inside a base image's operating-system packages.

## What real companies do

1. **Scan the registry continuously** (ECR enhanced scanning through Inspector, Wiz, Prisma). The watcher reads the live images. Accurate; Inspector's enhanced scanning is billed per image, Wiz and Prisma are sold by quote, and all of them need access into the account.
2. **Re-scan on a schedule in CI** (a weekly or nightly job running the same scanner against what is deployed). Cheap, no new product, the finding lands where the team already works.
3. **Rely on update bots alone.** Free, but blind to operating-system packages and to anything the bot does not open a pull request for.

## Decision

**Pattern 2, reading the sources and not the live images.** A weekly workflow (`.github/workflows/image-watch.yml`, `scripts/ops/image_watch.py`) runs Trivy, the same tool and the same bar as CI (HIGH and CRITICAL, with a fix available, minus the dated exceptions in `.github/trivy-ignore.yaml`), against two things:

- **`main`**: what the next release would ship;
- **the commit production is pinned to** (read from `deploy/environments/prod/values.yaml`): what the next production start-up would run.

For each, it scans `uv.lock` (`trivy fs`), the base image of the **last** `FROM` of each service's Dockerfile (the earlier ones are build stages that are thrown away), and the third-party images the chart names (`trivy image`, pulled from public registries as arm64). A base image is read for its **operating-system packages only**: the Dockerfiles remove pip, setuptools and wheel from the finished image, and the libraries that stay are in `uv.lock`; reading the base image whole reported two findings that are in no Kaval image. A third-party image is read whole.

The report is **grouped by package** (108 vulnerabilities became 18 lines on the first run; the whole list stays in the run log). A finding opens, or edits, **one** GitHub issue labelled `image-watch` (GitHub e-mails it when it is opened) and turns the run red, which is a second e-mail every week it stays unresolved. A clean run closes the issue. The shape is `backup-watch.yml`'s: two jobs, a `scan` that can read and cannot write, and a `tell` that can write one issue and cannot read anything else. If the scan cannot run, `tell` says so; silence is never read as "all fine".

**A scan that finds nothing because it read nothing must not pass as clean.** Each target has a minimum package count (20 for a lock file, 10 for an image); fewer is reported as "the check could not look", and that beats a finding in the verdict.

**A monthly check-in** (`KAV-168`) covers what no scanner does: someone has to read the alerts and click merge. See below.

### Why not read the images from ECR

It would need a second CI login into AWS, a reviewed Terraform change, a staging proof and Roshan's typed go, for something the sources already say: every Kaval image is made from a base image pinned by digest, the locked libraries and Kaval's own code, all in git. This is the same reasoning that parked the nightly drift check ([ADR-0105](0105-version-1-closes-and-the-nightly-drift-check-moves-to-future-scope.md)). If the sources ever stop describing the images (a build step that installs unpinned packages), this decision should be revisited.

## What the first real run found (2026-11-19)

Run in a Linux container before the pull request was merged. `uv.lock` (77 packages) and the base image (105 packages) were clean, for `main` and for production's pinned commit `sha-f280013`. Three third-party images were not:

| Image (chart value) | On in | Problems | Cause |
|---|---|---|---|
| Prometheus v3.15.0 | staging, production | 14 | Go standard library and `x/net` of an old build |
| Ollama 0.34.4 | staging, production | 55 | Go standard library, `x/crypto`, `x/net`, and Ubuntu's `apt` and `openssl` |
| pgvector pg16 | staging and local (production's database is its own server) | 27 | Go standard library of a bundled tool |

**None of the three has an updater.** Dependabot's `docker` ecosystem reads the five Dockerfile folders; these images are named only in `deploy/charts/kaval/values.yaml`. They are pinned and nothing moves them. That is the finding that justifies the watch, and it is filed as `KAV-169`. Until `KAV-169` moves them the weekly run stays red and the one issue stays open: that is the watch working, not a fault. Production is parked, so nothing is exposed meanwhile.

## What it cannot see (accepted)

1. **What the Dockerfiles install themselves** (`apt` steps in the build). Those packages are in the shipped image but not in the base image's digest, so this scan misses them. They are scanned at every build and release by `ci.yml` and `release.yml`.
2. **The Helm charts Flux installs** (Kyverno and others). Their images are not named in Kaval's chart values. Dependabot does not cover them either.
3. **The OPA binary** that `agent` and `executor` build from source (`KAV-132`): a Go program with its own libraries, fixed at build and checked then.
4. **Our own code**, which is CodeQL's job.
5. **A flaw with no fix yet.** `--ignore-unfixed` is the same bar as CI: an issue nobody can act on is noise. A person reading the Trivy output by hand sees them.

## The monthly check-in

Watching only works if someone looks. `KAV-168` adds `docs/runbooks/monthly-check-in.md`: a checklist for the first of each month (open Dependabot pull requests and alerts, the `image-watch` issue, the Scorecard, that the scheduled runs have not been switched off, the budget), a table of dated reminders (the laptop AWS key by 2027-02-14, the first real secret rotation by November 2027, a quarterly production start-up and restore drill), and a scheduled workflow that opens a checklist issue each month so it arrives by e-mail.

**GitHub switches scheduled workflows off.** GitHub's own documentation says: "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days." It does not say what counts as activity. A parked repository could therefore stop running CodeQL, the Scorecard, the backup check, this watch and the monthly issue itself, with nothing to say so. Dependabot is not a workflow and is not affected. Roshan declined a Google Calendar reminder outside GitHub, so the mitigation is the check-in's own first line ("every scheduled workflow shows a run in the last week; if one says disabled, enable it") and the weekly run's red e-mail. This is an accepted risk with a known limit: if the monthly issue never arrives, that silence is the signal to look at the Actions tab.

## Planning standard

- **Whole path:** nothing deploys. CI-only and documentation; no release round, no staging, no production. Runs from `main`.
- **Security:** `scan` has `contents: read` only, no AWS, no secrets; `tell` has `issues: write` only. The report is CVE ids, package names and versions, read from an environment variable and never pasted into a command. Third-party actions are pinned to full commit SHAs and Trivy is downloaded and checked against a pinned checksum, as in `ci.yml`. The workflow takes one hand-run input (`plant`, a boolean).
- **Failure:** a failed vulnerability-list download is retried five times and then fails the run; a run that read too little is "broken"; a run that could not start leaves `tell` to say so. All three are loud.
- **Observability:** the issue, the red run, and the log line per target with its package count.
- **Cost:** $0. A public repository's GitHub Actions minutes are free. Nothing runs on the 4 GB node.
- **Tests:** the rules are tested functions (`test_image_watch.py`, offline). A real run on `main`, and a hand run with `plant` that must open the issue, then a normal run that must close it. The negative case (clean closes the issue, a too-small scan is not clean) is in the tests and in the live proof.
- **People:** the runbook `docs/runbooks/image-watch-finding.md`; the owner is Roshan; the monthly check-in is the escalation, because an issue that sits unread is the failure this exists to catch.

## Consequences

- A flaw announced after a release is seen within a week, in the sources, without any AWS access.
- One more scheduled workflow, and one more thing that GitHub can switch off after 60 quiet days; the monthly check-in is where that is noticed.
- The weekly scan can only be as honest as the sources: it says "the libraries and base images we pin have no known fixable problem", not "the running servers are safe".
- **The issue and the run log are public**, because the repository is. They name CVE ids and the versions we pin. That is accepted: the pins are already in the public chart, a CVE id is public by definition, Dependabot's pull requests already name vulnerabilities in the open, and none of Kaval is reachable from the internet (production is parked and has no public way in). The stricter alternative is an issue with counts only and the detail in a private place; it can be switched to if a finding ever concerns something that is reachable.
- Tools like Wiz were considered and not adopted: sold by quote with no free tier found, and they would need read access into the account to do what Access Analyzer, the access review and this watch already do at $0.
