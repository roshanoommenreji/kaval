# Runbook: a Dependabot pull request for pgvector, Ollama or Prometheus

**When:** a pull request titled `chore: Bump <image> from <old> to <new> in /deploy/third-party-images` arrives (weekly at most, one per image), or the weekly image watch is red for one of these three images and a newer release exists.
**Owner:** Roshan merges; the steps are run in a session. **Cost:** $0 until the staging and production steps, which are their own typed go (see the end). Story `KAV-170`, [ADR-0108](../adr/0108-a-bot-watches-the-chart-images-through-one-manifest-and-a-sync-step.md).

The bot changes **one file**, `deploy/third-party-images/Dockerfile.images`. The same image is named in six other files; until they agree, CI is red on purpose.

## 1. Make the other files agree (every pull request)

On the bot's branch:

```bash
git fetch origin && git checkout <the dependabot branch>
python scripts/dev/sync_third_party_images.py          # rewrites the other files from the manifest
python scripts/dev/sync_third_party_images.py --check  # prints "every place agrees"
python -m pytest scripts/dev/test_third_party_image_pins.py -q
git add -A && git commit -m "chore: sync the third-party image pins with the manifest"
git push
```

It refuses a database image that is not PostgreSQL 16 on Debian bookworm. If the bot ever proposes a new PostgreSQL major or a newer Debian for the database, **close the pull request**: that is a migration with its own story (a new Debian changes the text sort order and can corrupt indexes, `KAV-169`).

## 2. Read the release, then the image-specific check

| Image | Look at | Extra check before merging |
|---|---|---|
| **Ollama** | the release notes (model loading, memory) | The golden evals must give the same result on the old and the new version: the golden set on the laptop with each version (`python -m evals.run --set golden`; it needs a database, `opa` on the path and the model: the full steps are in [Lab 110](../labs/lab-110-moving-the-chart-images-past-the-problems-the-weekly-watch-found.md), Part 4); the staging session then reads its memory on the real node (about 1,069 MiB loaded was the 2026-11-22 reading; 558 MB free of 3,829) |
| **Prometheus** | the release notes (a changed flag or rule syntax) | `make rules-test` passes (CI runs it); the chart's alert rules still load |
| **pgvector** (the database image) | the release notes; a minor bump (0.8 to 0.9) may need `ALTER EXTENSION vector UPDATE` in the database | Production's database server does **not** pick the new image up by itself: see step 4 |

If the pull request only moves the digest of the same tag (a rebuild), say so in the merge note: it is usually a base-image security fix, which is the point.

## 3. Merge, then the usual road

CI must be green (14 checks). **He merges.** The change touches the chart, so the release round runs: the bot opens the release pull request, its checks wait for his "Approve and run workflows", then he merges; the staging pin follows. Nothing here reaches production until all of this, each on his own typed go:

1. `go staging smoke` (staging up, `make staging-smoke`, read the new image running by digest, staging down)
2. a release tag recorded, then `go promote <tag>`
3. production's next start-up

## 4. The database image on production (pgvector bumps only)

Production's database server never re-reads its start-up script, so the new image reaches it only through the explicit step at the next production start-up: `scripts/ops/db-image-refresh.sh` (needs his "yes"; the first real run, 2026-11-22, is in Lab 110 Part 6). Staging's database gets the pin when it is rebuilt.

## 5. The dated exceptions

When a bump removes a finding, delete its entry from `.github/trivy-ignore.yaml` in the same change. Entries end on their own date (2026-12-04 and 2026-12-18); the weekly watch goes red again when one ends. **Look for a newer release first**: this bot may already have proposed it.

## If the bot goes quiet

1. Actions, "Dependabot Updates": the run for `/deploy/third-party-images` should be green and its log should say `Checking if pgvector/pgvector ... needs updating`, then `ollama/ollama`, then `prometheus/prometheus`.
2. Repository, Insights, Dependency graph, Dependabot: an error there names the file or registry it could not read.
3. The monthly check-in has a line for this. The weekly image watch is the backstop: it is red whenever a pinned image has a fixable serious finding, bot or no bot.

## Do not

- Do not edit the pins by hand in the six files; change the manifest and run the script.
- Do not merge a bot pull request with a red `pin` test by "fixing" the test.
- Do not move the database off PostgreSQL 16 or Debian bookworm without its own story.
- Do not promote, start production or run the refresh without his typed go.
