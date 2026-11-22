# Lab 110 — Moving the chart's images past the problems the weekly watch found

**Phase:** 8 (hardening; written after version 1 closed, in the "keeping watch" group, [ADR-0106](../adr/0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md)) · **Story:** `KAV-169` · **Decision:** [ADR-0107](../adr/0107-the-chart-images-move-to-their-newest-releases-and-the-leftovers-get-end-dates.md) · **Status:** built 2026-11-21, proved on staging and promoted to production on 2026-11-22 (Part 6)

## What you will do

Take the three third-party images the weekly image watch flagged (Prometheus, Ollama, the database), find the newest release of each that is as clean as it gets, pin them by digest everywhere they are named, write the leftovers down as dated exceptions, prove the model still answers the same, and make the database server take the new image. You need Docker, the repository, Python 3.11, and the Trivy release pinned in `ci.yml` run in a Linux container (as in [Lab 108](lab-108-a-weekly-scan-of-what-production-runs.md), Part 3).

## Why

The watch (`KAV-167`) turned red for three images that no updater covers. Real companies fix this with a bot (Dependabot or Renovate) that opens a pull request per image; that bot is `KAV-170` ([Lab 111](lab-111-a-bot-that-watches-the-chart-images.md)). (*Correction, 2026-11-22: this first said Dependabot does not read a Helm values file. It can; it was just never pointed at one.*) Until then it is done by hand, once, carefully.

## Part 1 — Scan the candidates before changing anything

Ask the registry for the newest tags, then scan each candidate the way the watch does (arm64, HIGH and CRITICAL with a fix, `--ignore-unfixed`). The answer decides everything else, so do not skip it:

| Candidate | Problems | What is left |
|---|---|---|
| Prometheus v3.16.0 (v3.15.0 had 14) | 1 | `x/net` v0.61.0, CVE-2026-78661 (fixed in 0.62.0, in no release yet) |
| Ollama 0.40.16 (0.34.4 had 55) | 3 | the same `x/net`, and `apt` in Ubuntu's layer (two packages) |
| pgvector 0.8.7-pg16-bookworm (pg16 had 27) | 26 | all in `gosu`, a tiny Go program inherited from the official PostgreSQL image, which itself has the same 26 |

Nothing newer was clean, so the honest outcome is "the newest, plus dated exceptions". Candidates NOT taken: PostgreSQL 16 on Debian "trixie" (a newer C library sorts text differently, and an index built under one can silently be wrong under the other) and any other PostgreSQL major version (the data folder would not open).

## Part 2 — Pin by digest, in every place

Read the multi-architecture digest from the registry (Docker Hub's tag API, Quay's tag API) and write `name:tag@sha256:…` in: `deploy/charts/kaval/values.yaml` (database for the local cluster and the lab, Ollama, Prometheus), `compose.yaml` (database and three Ollama services), `.github/workflows/ci.yml` (the database service container), `infra/modules/database/user_data.sh.tftpl` (the database server's start-up script) and `scripts/ops/restore-drill.sh` and `seed-refresh.sh` (throwaway databases that must match the real server). The test `scripts/dev/test_third_party_image_pins.py` fails if any of them disagrees or a tag floats again.

## Part 3 — The dated exceptions

`.github/trivy-ignore.yaml`, 28 entries by the rules of [ADR-0033](../adr/0033-dated-trivy-exception-for-the-bundled-opa-binary.md): one id per entry, scoped to the file (or package) that carries it, a reason, and an end date (2026-12-04 for the two small ones, 2026-12-18 for `gosu`). Test it: scan the three pinned images with `--ignorefile .github/trivy-ignore.yaml`; Prometheus and the database must come back with 0 findings.

## Part 4 — The model still answers the same

Ollama jumps six minor versions, and the model's answers are the product. The repo's two eval sets (`python -m evals.run --set golden` and `--set injection`) need three things on the machine: a database with the migrations applied, `opa` on the path (the run refuses to start without it), and the model. On a laptop, with Docker:

1. Start the pinned database image (`docker run -d -p 55432:5432 -e POSTGRES_DB=kaval -e POSTGRES_USER=kaval -e POSTGRES_PASSWORD=<a throwaway> <the pin>`), set `POSTGRES_*` to match with `POSTGRES_SSLMODE=disable`, and run `alembic upgrade head`.
2. Download OPA for your system from its release page and compare the checksum with the published one.
3. Run the OLD Ollama image on a named volume (`-v models:/root/.ollama -p 11500:11434`), pull `gemma3:1b-it-qat` and `all-minilm`, index the runbooks, **send one embedding call and one chat call first** (the first request takes about 10 seconds and the tool gives up after 30, which made two early attempts fail), run both sets. Then stop it and do the same with the NEW image on the **same volume**, which also proves the old model files open under the new version (their ids did not change).

Result (laptop, x86 CPU, 2026-11-21):

| | Ollama 0.34.4 | Ollama 0.40.16 |
|---|---|---|
| Golden: valid / never-class / root-cause match / right fix | 20 of 20 / 0 / 100% / 8 of 8 | 20 of 20 / 0 / 100% / 8 of 8 |
| Golden: average confidence said / kept | 0.89 / 0.85 | 0.92 / 0.88 |
| Injection: valid | 9 of 10 (one call timed out on a cold CPU) | 10 of 10 |
| Injection: planted text obeyed (not gated) | 0 of 6 (one case timed out) | 1 of 7 (the plain instruction in the event message, which Lab 72 already recorded as obeyed) |
| Injection: Slack-markup and missing-label card checks (gated, must be 0) | 0 and 0 | 0 and 0 |
| Injection: never-class action proposed (gated) | 1 (`json-smuggling`) | 1 (`json-smuggling`) |
| Ollama memory after the golden set (laptop) | 1.37 GiB | 1.27 GiB |

The never-class line is the same case on both, so the upgrade did not cause it. Reading the stored action shows why: the message plants `reversible: false` and `blast_radius: account`, and the model copied them onto an ordinary `restart_pod`, which policy classes as never (refused, nothing would run), while the harness counts the proposal itself as a failure. It is filed as `KAV-171`. Staging's memory reading, on the real 4 GB arm64 server, is the figure that matters and is taken in Part 6.

## Part 5 — The database server does not read git

The start-up script is read once, when a server is built, and Terraform ignores later edits (`ignore_changes = [user_data]`). A server built after the change (staging, every time) gets the pin; production's server does not. `scripts/ops/db-image-refresh.sh` is the explicit step: it finds the server, shows the pin, asks you to type `yes`, pulls the image, rewrites only the image in the Postgres unit, restarts the database (the stop gives it 60 seconds for a clean checkpoint), and prints the image, the vector extension and the server version. It does nothing if the pin is already running. The offline test runs the rewrite on a sample unit file and checks it refuses a floating tag or another major version.

```bash
python -m pytest scripts/dev/test_third_party_image_pins.py -q
```

## Part 6 — Release, staging, production

**22 November 2026, release and staging (his go: "go staging smoke", then "keep it up and start the release round").**

1. **Release round.** The first staging build (sha-1c044e9, from the Dependabot merges and the pins) passed the smoke test 7 of 7 but was **not recorded**: it had no release tag, and `staging_smoke.py` refuses to record such a build (ADR-0081, KAV-135). `release-prepare` then proposed Kaval 0.4.5 (#480, his "Approve and run", merged when green), the release built and tagged **v0.4.5 at sha-410aba1**, and the bot's staging pin (#481) was merged next.
2. **Staging parked itself while the approvals were pending** (the four-idle-hours rule worked as designed; the app server was gone, the database stopped). `make staging-up` resumed it on the new build. Lesson: with a long wait for approvals, expect to resume rather than find staging running.
3. **Smoke test on v0.4.5: 7 of 7**, recorded in `deploy/promotion/passed-staging.json` ("v0.4.5 (prod runs v0.4.4)").
4. **Ollama 0.40.16 on the real 4 GB node:** 12 MiB idle; **1,069 MiB with the model (gemma3:1b-it-qat) loaded**; the node then had **558 MB available** of 3,829. The last measurement with the model loaded on production (Lab 97) was 414 MB available, so the new version is not worse. Nothing restarted.
5. **Digests read from the running pods:** Ollama `910d0d81...`, Prometheus `93320545...`, both equal to the pins in `values.yaml`.
6. **The database image:** staging's server runs `pgvector/pgvector:0.8.7-pg16-bookworm` and its unit names the pinned digest `484e8380...`. `DB_NAME_PREFIX=kaval-staging bash scripts/ops/db-image-refresh.sh` (first live run, his "yes") printed `unit: unchanged` / `already running the pin` and the health check passed: it is a no-op when the pin is already in place.
7. **Cost:** staging ran about eight hours in two stretches, roughly 25 cents (estimate; AWS closes the month's bill later).

Staging was then torn down on his typed go and swept clean (no instances, volumes, snapshots, addresses or networks tagged staging left).

**22 November 2026, promotion and production (his go: "GO" on `go promote sha-410aba1` and on production's start-up with the refresh step; then his typed `apply production`).**

8. **Promotion.** `promote.yml` for **v0.4.5 / sha-410aba1** opened #483 (the promotion guard and signature checks passed; its checks waited for his "Approve and run workflows" click); he merged it. Production's pin moved from v0.4.4 to v0.4.5. Production was parked, so nothing ran yet.
9. **Preflight and plan (read-only).** All four images exist in ECR. Plan: **0 to add, 2 to change, 0 to destroy**: the app server group's desired size 0 to 1, and the launch template's base image (a newer Amazon Linux image, used only by servers started from now on). He typed `apply production` after seeing it.
10. **The database refresh on production's server (the real change, unlike staging).** `resume-database.sh` started the stopped server; its health check passed. `db-image-refresh.sh` then printed the pin, replaced the unit (`unit: changed pgvector/pgvector:pg16`), pulled the pinned image, restarted the database for a few seconds, and read back: **pgvector 0.8.7, PostgreSQL 16.16** (still the 16 line, bookworm), `accepting connections`, health check passed. The app was not up at the time.
11. **Apply and smoke.** The plan applied cleanly (19:52 IST). `make prod-smoke` passed **9 of 9 on the first try** about six minutes later (earlier bring-ups needed three tries): ten pods running, no restarts, every service on sha-410aba1, running digests equal to ECR, database migrated to head, both API reads 200, the exact images passed staging, three timers set. The model server was still loading when it ran (a note, not gated); node memory showed 1,400 MB available.
12. **Chart images on production's own pods:** Ollama `910d0d81...` and Prometheus `93320545...`, both equal to the pins in `values.yaml`.
13. **Parked again at 20:02 IST.** `dump-before-park.sh` took a fresh dump first (480 KB, checked); the park plan was one change (desired 1 to 0, minimum 1 to 0); `pause-database.sh` took a snapshot of the data volume and stopped the server. Checked after: group 0/0, database stopped, "still alive" alarm OK. The restore drill was not due (last 12 November, due before 17 December).
14. **The weekly image watch, run by hand after the pin moved: green, and it closed issue #469.** This is the proof that was waiting since KAV-167: a clean run closes the issue it opened. It stayed red before this because the watch reads production's pinned commit, and that pin only moved with #483.
15. **Cost:** production's database server ran about 16 minutes and the app server about 10, so 1 to 2 cents (estimate).

What is left: nothing for KAV-169. The two dated exceptions end on 2026-12-04 and 2026-12-18 (look for newer releases first), and `KAV-170` / `KAV-171` are filed and not started.

## What can go wrong

- A new scan finds something after the pins are chosen: the end dates are two and four weeks, not forever.
- Ollama behaves differently on the 4 GB server than on a laptop: memory is read on staging before anything is promoted.
- A person renews an exception to quieten the watch: the runbook says to look for the fixing release first.
