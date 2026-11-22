# ADR-0108: A bot watches the three chart images through one manifest and a sync step

**Status:** Accepted on 2026-11-22 (built and proved on the laptop with Dependabot's own command-line tool; proved live the same day: its first run on `main` checked all three images and skipped Ollama 0.40.17 by the cooldown, [Lab 111](../labs/lab-111-a-bot-that-watches-the-chart-images.md) Part 5)
**Deciders:** Roshan
**Related:** [ADR-0107](0107-the-chart-images-move-to-their-newest-releases-and-the-leftovers-get-end-dates.md) (the move that this keeps from happening again), [ADR-0106](0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md) (the weekly watch and the monthly check-in), [ADR-0010](0010-ci-pipeline-and-supply-chain.md) (Dependabot and the CI gate)
**Story:** `KAV-170`, epic `KAV-166`

## Context

`KAV-169` moved Prometheus, Ollama and pgvector to their newest releases by hand, because nothing opened a pull request for them. The weekly watch (`KAV-167`) finds a problem *after* it exists; a bot that proposes the new release finds the *fix* the week it appears. Dependabot already runs here for libraries, actions, base images and Terraform.

Two facts shaped the design:

1. **Correction to ADR-0107 and Lab 110.** Both said Dependabot "does not read the chart's values file". That was wrong in the way that matters. Its Docker updater reads any YAML file in a directory it is pointed at and finds `image:` lines; it was simply never pointed at the chart. (Read from the updater's source, `docker/lib/dependabot/docker/file_parser.rb`, on 2026-11-22.) Those two documents now carry the correction.
2. **The three images are named in eleven places across six files** (the chart, compose, CI, the database server's start-up script and two scripts that start a throwaway database). A bot can read some of them (the chart, compose, CI) and none of the shell scripts or the Terraform template. Pointed at each file separately it would open several pull requests for one image that disagree with each other, and the test from `KAV-169` that insists they all name the same build would turn every one red.

## What real companies do

1. **Point the bot at every file** and merge several pull requests per image. Simple to configure; cannot cover shell scripts and templates, and the pieces disagree while they wait.
2. **Renovate** with regular-expression managers: one pull request edits every file that names an image. Solves the many-files problem properly, but is a second bot with write access to the repository next to Dependabot, and its own configuration to keep right.
3. **One small manifest the bot reads, a script that copies its pins into every other place, and a test that fails on any disagreement.** One pull request per image from the bot you already run; the copy is one command.

Recommendation, taken: 3. It adds no new bot or permission, and the test makes "a place was forgotten" impossible to merge.

## Decision

- **`deploy/third-party-images/Dockerfile.images`** holds three `FROM <image>:<tag>@sha256:<digest> AS <name>` lines. It is never built. Dependabot's `docker` updater reads it and rewrites the tag and digest together.
- **A Dependabot entry for that one folder:** weekly (Saturday, like the others), **one pull request per image** (no group), commit prefix `chore`, and a **three-day cooldown** on new releases.
- **`scripts/dev/sync_third_party_images.py`** copies the manifest's pins into the other six files (`--check` changes nothing and exits 1 on a difference). It rewrites only a recognised pin and refuses a database image that is not PostgreSQL 16 on Debian bookworm.
- **`scripts/dev/test_third_party_image_pins.py`** now also fails when any place disagrees with the manifest, and its message names the one command that fixes it. So a bot pull request is red until the script has been run and the result committed to the bot's branch ([runbook](../runbooks/third-party-image-bump.md)).
- **No automation that pushes to the bot's branch.** A workflow triggered by Dependabot gets a read-only token and no secrets, and the usual way round that (`pull_request_target` with a write token) hands repository write access to a run that reads the bot's branch. One manual commit per bot pull request is the cheaper, safer price for a repository with a handful of these a quarter.

## What the local proof showed (Dependabot's own tool, a scratch copy with older pins)

- It read the manifest and opened **three separate pull requests**, each changing one `FROM` line (tag and digest together): pgvector 0.8.6 to 0.8.7, Ollama 0.40.15 to 0.40.16, Prometheus v3.15.0 to v3.16.0.
- The database stayed on `-pg16-bookworm`: it did not propose PostgreSQL 17 or Debian trixie.
- **The cooldown works for Docker Hub** (it skipped Ollama 0.40.17, published that day, and proposed 0.40.16) **but not for quay.io**: the updater logs "no verified registry publication date source for quay.io". Prometheus bumps therefore arrive the day they appear. That is accepted: a person merges, CI and the staging smoke test sit behind it, and the weekly watch scans what production runs.

## Consequences

- A pull request from this entry **touches `deploy/charts/kaval/values.yaml`**, so merging it starts a release round (chart version, release pull request, staging smoke, then a promotion on its own typed go). Ollama also needs the golden evals compared old against new on the laptop (`KAV-169`'s method); the database image needs the refresh script at the next production start-up. All of it is in the runbook.
- If Dependabot stops reading the manifest, nothing is said. The weekly watch still goes red for a known-bad pinned image, and the monthly check-in now has a line to look at Dependabot's run for this folder.
- The three-day cooldown does not apply to security updates, which Dependabot opens straight away.
- Cost: $0 (a public repository). No AWS, no capacity, no release, nothing deployed.

## Rejected

- **Pointing the bot at the chart's values file instead of a manifest.** It would still leave six files unread, and the file's own structure (first-party images tagged `dev`) is Dependabot's business to guess at, not ours to depend on.
- **Grouping the three** into one weekly pull request: a bad Ollama bump would block a safe Prometheus one, and each needs a different check.
- **Renovate**: see above.
- **A workflow that commits the sync to the bot's branch**: see Decision.
