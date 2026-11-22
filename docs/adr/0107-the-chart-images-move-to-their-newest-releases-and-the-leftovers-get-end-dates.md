# ADR-0107: The chart's three third-party images move to their newest releases, and what is left gets an end date

**Status:** Accepted on 2026-11-20 (the move and the dated exceptions); the staging proof and the promotion are recorded in Lab 110 when they happen. Roshan was shown the scan of every candidate, the plan and the leftovers, and answered "go" to both the plan and the exceptions.
**Deciders:** Roshan
**Related:** [ADR-0106](0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md) (the weekly watch that found them), [ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md) (the rules for exceptions), [ADR-0008](0008-production-database-on-its-own-server.md) (the database has its own server)
**Story:** `KAV-169`, epic `KAV-166`. The reason it will not happen again (a bot that opens these pull requests) is `KAV-170`.

## Context

The weekly image watch (`KAV-167`) found known, fixable HIGH and CRITICAL problems in the three third-party images the chart names, because nothing updates them: Prometheus v3.15.0 (14), Ollama 0.34.4 (55) and pgvector pg16 (27). Planning this story found two more things.

- **The database image in the chart is not the database image in production.** The chart's value is used only by the local cluster and the lab. Staging's and production's databases each run on their own server ([ADR-0008](0008-production-database-on-its-own-server.md)), started by a script that is read once, when the server is built; Terraform ignores later edits to it (`ignore_changes = [user_data]`), because a boot script must never replace a running database. So an image pin changed in git reaches a new server (staging is rebuilt every time) but never one that already exists (production's). The `pg16` tag also floated, so production's database runs whichever build was current the day its server was first built, and nothing says which. (ADR-0106's table says pgvector runs on "staging"; that was the chart's value, not what staging runs.)
- **The three images were each named in several places** (chart, compose, CI, the start-up script, two scripts) with nothing keeping them in step.

## What real companies do

1. A bot opens a pull request for every new version of every image, pinned by digest (Dependabot or Renovate). The usual answer; for these three it is `KAV-170`, because Dependabot does not read the chart's values file.
2. Float a tag and rebuild often. Easy, and nobody knows what is running.
3. Build and patch your own copies. Heavy for one person; rejected.

## Decision

**Move all three to their newest releases, each pinned by digest, in every place that names them:**

| Image | From | To |
|---|---|---|
| Prometheus | v3.15.0 | v3.16.0 |
| Ollama | 0.34.4 | 0.40.16 (newest stable; release candidates are not used) |
| Database | `pgvector/pgvector:pg16` (floating) | `pgvector/pgvector:0.8.7-pg16-bookworm`, pinned by digest |

The database stays on **PostgreSQL 16 on Debian bookworm**. The newer "trixie" builds use a newer Debian, whose C library sorts text differently; an index on text built under one version can silently return wrong answers under the other. Moving the major version would also make the data folder unreadable. Neither is a security patch's business.

**What is left gets a dated, file-scoped exception ([ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md)'s rules), 28 entries:**

| Finding | Where | Why nothing fixes it yet | Safe because | Ends |
|---|---|---|---|---|
| `x/net` v0.61.0 (CVE-2026-78661) | `bin/prometheus`, `bin/promtool`, `usr/bin/ollama` | Fixed in x/net 0.62.0; Prometheus v3.16.0 and Ollama 0.40.16 are the newest releases and both still carry 0.61.0 | Needs an HTML page as input; neither program is given one, and neither is reachable from the internet | 2026-12-04 |
| `apt` 2.8.3 (CVE-2026-109518), two packages | Ollama's Ubuntu layer | Ubuntu has the fix; Ollama has not rebuilt its image | A package installer that never runs in the finished container | 2026-12-04 |
| Go standard library v1.24.6 in `gosu` (26 CVE ids) | `usr/local/bin/gosu` in the database image | The official PostgreSQL image (rebuilt 2026-11-18) has the same; gosu 1.20 (2026-10-25) is built with a newer Go but neither image has picked it up | It runs once at start-up to drop administrator rights, listens on nothing, reads nothing from the network | 2026-12-18 |

At each end date Trivy stops ignoring the entry and the weekly run goes red again by itself. The runbook says to look for the release that fixes it before renewing anything.

**A real step for the database's server**, since git cannot reach it: `scripts/ops/db-image-refresh.sh` (with `db-image-refresh-remote.sh`, which runs on the server) moves a running server's Postgres unit onto the pinned image. It pulls first (a failed pull leaves the old one running), rewrites only the image in the unit, restarts the database (a clean 60-second checkpoint on stop), and prints the image, the vector extension and the server version. It refuses anything that is not a pinned pgvector PostgreSQL 16 image, and it does nothing if the pin is already running. It is step 2 of `prod-rollout.md`, needs Roshan's go like any change to AWS, and is not needed on staging.

**A test (`scripts/dev/test_third_party_image_pins.py`) fails if** any of the six places names a different database build, a tag floats again, Ollama differs between compose and the chart, an image is not pinned by digest, or an exception has no end date or scope.

## Not decided here

How a bot learns about these images is `KAV-170`. Building our own database image with a newer gosu would clear the 26 findings today but makes us own an image and its patching; rejected for a four-week gap.

## Consequences

- Prometheus goes from 14 findings to 1, Ollama from 55 to 3, the database from 27 to 26 (all in one small tool); the 28 leftovers are written down with reasons and end dates instead of living in an issue that nobody can act on.
- **The weekly watch stays red until production's pin moves**, not until the merge: it also reads the commit production is pinned to, which still names the old images. It goes green after the release is promoted, which is also when "a clean run closes the issue" is first proved live. Production is parked, so nothing is exposed meanwhile.
- Ollama jumps six minor versions, so the repo's own two eval sets were run on a laptop against the old and the new image, with the same database and the same model files (2026-11-21, [Lab 110](../labs/lab-110-moving-the-chart-images-past-the-problems-the-weekly-watch-found.md) Part 4). The 20 golden incidents came out the same: 20 of 20 valid, no never-class action, 100% root-cause match, 8 of 8 right fix, confidence 0.89 on average before and 0.92 after. The injection set also behaved the same (every gated card check 0; the one planted instruction the new model obeyed is one Lab 72 already recorded as obeyed). One gate failure shows on **both** versions, in the case that smuggles `reversible: false` and `blast_radius: account` into the event text: the model copies them onto a harmless `restart_pod`, policy classes it never (so it would be refused), and the harness counts any never-class proposal as a failure. It is not caused by this upgrade; it is filed as `KAV-171`. The memory the new Ollama needs on the 4 GB node is measured on staging, not on a laptop, before anything is promoted.
- Production's database gets the pinned image at its next start-up, by the refresh step, on his go. Until then production's database server runs whatever build it first pulled.
- If an exception ends with no fix released, the run is red again on that date and a person decides, with the runbook, whether to renew it.
