# Lab 111 — A bot that watches the chart's three third-party images

**Phase:** 8 (hardening; written after version 1 closed, in the "keeping watch" group, [ADR-0106](../adr/0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md)) · **Story:** `KAV-170` · **Decision:** [ADR-0108](../adr/0108-a-bot-watches-the-chart-images-through-one-manifest-and-a-sync-step.md) · **Status:** built and proved on the laptop on 2026-11-22; the first live run is Part 5 (filled in after the merge)

## What you will do

Make Dependabot open a pull request whenever pgvector, Ollama or Prometheus has a newer release, without it ever being able to change one place and forget the others. Cost: $0. Nothing is deployed.

## Why

[Lab 110](lab-110-moving-the-chart-images-past-the-problems-the-weekly-watch-found.md) moved the three images by hand because nothing proposed a new release. The weekly watch finds a problem after it exists; a bot finds the fix the week it appears. (Lab 110 first said Dependabot cannot read the chart's values file. That was wrong: it reads image lines in any YAML file in a folder it is pointed at. The reason for a manifest is different: the same image is named in eleven places across six files, and a bot can read only some of them.)

## Part 1 — The manifest

`deploy/third-party-images/Dockerfile.images` has three lines and is never built:

```
FROM pgvector/pgvector:<tag>@sha256:<digest> AS database
FROM ollama/ollama:<tag>@sha256:<digest> AS ollama
FROM quay.io/prometheus/prometheus:<tag>@sha256:<digest> AS prometheus
```

Dependabot's Docker updater knows this shape best: it rewrites the tag and the digest together, and a rebuilt tag with a new digest counts as an update too. The database tag ends `-pg16-bookworm`, which keeps the bot on PostgreSQL 16 and Debian bookworm.

## Part 2 — The copy script and the test

```bash
python scripts/dev/sync_third_party_images.py --check   # changes nothing; exit 1 if any place differs
python scripts/dev/sync_third_party_images.py           # copies the manifest's pins into the six files
```

`scripts/dev/test_third_party_image_pins.py` runs the check in CI, so a bot pull request that moved only the manifest is red, and the failure message is the command above. The script touches only a recognised `repository:tag@sha256:digest` and refuses any database image that is not PostgreSQL 16 on bookworm (tests in `test_sync_third_party_images.py`: rewrites everything, keeps Windows line endings, is idempotent, refuses pg17, trixie, a floating tag, a short digest, another image, a missing or duplicate stage).

**The negative case, done by hand:** change one Ollama tag in `compose.yaml`, run the pin test, and it fails naming `compose.yaml` and the command; run the script and it is green again.

## Part 3 — The Dependabot entry

`.github/dependabot.yml` gained a `docker` entry for `/deploy/third-party-images`: weekly (Saturday), **no group** (one pull request per image), commit prefix `chore`, and `cooldown: default-days: 3`.

## Part 4 — Prove what Dependabot would do, without waiting for it

Dependabot's own command-line tool runs the real updater in Docker against a folder on the laptop (about 2 GB of images the first time):

1. Download `dependabot` for your system from <https://github.com/dependabot/cli/releases> into an empty folder of its own.
2. In a scratch folder make `deploy/third-party-images/Dockerfile.images` with **older** pins (`docker buildx imagetools inspect <image>:<old tag>` prints the digest).
3. `dependabot update docker <owner>/<repo> --local <scratch folder> --directory /deploy/third-party-images -o out.yml`

What it did on 2026-11-22, with pgvector 0.8.6, Ollama 0.40.15 and Prometheus v3.15.0 as the starting pins:

- three separate pull requests, one `FROM` line each, tag and digest together: pgvector 0.8.7, Ollama 0.40.16, Prometheus v3.16.0
- the database stayed `-pg16-bookworm` (no PostgreSQL 17, no trixie)
- **Ollama 0.40.17 was skipped: "due to cooldown period"** (it was published that day), so the three-day wait works on Docker Hub
- **quay.io has no publication date the updater trusts**, so the wait does not apply to Prometheus: its bumps arrive at once

## Part 5 — The first live run (after the merge)

Dependabot reads a changed `dependabot.yml` on `main` and runs at once. Look in Actions, "Dependabot Updates", for the run on `/deploy/third-party-images`; its log must say `Checking if pgvector/pgvector ... needs updating`, then Ollama, then Prometheus. *Result: written in the record pull request that follows the merge.* On 2026-11-22 Ollama 0.40.17 already existed, so a real pull request is expected after the wait; follow the [runbook](../runbooks/third-party-image-bump.md) with it end to end.

## What can go wrong

- **The bot's pull request is red.** Expected: run the script (Part 2) and push the result to its branch.
- **The bot proposes PostgreSQL 17 or a newer Debian for the database.** It should not, because of the tag's suffix; if it does, close it (the script refuses it anyway).
- **Nothing ever arrives.** Check Dependabot's run log (Part 5) and the monthly check-in line; the weekly watch is the backstop.
- **A bump is merged and something breaks.** It follows the normal road (release, staging smoke, promote), so staging catches it first; going back is `rollback.yml`.
