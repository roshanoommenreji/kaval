# Lab 108 — A weekly scan of what production runs

**Phase:** 8 (hardening; written after version 1 closed, in the "keeping watch" group, [ADR-0106](../adr/0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md)) · **Story:** `KAV-167` · **Decision:** [ADR-0106](../adr/0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md) · **Status:** carried out; the live proof is in Part 5

## What you will build

A scheduled job that asks, every Monday, "is anything production runs, or the next release would ship, newly known to be vulnerable?", and opens one GitHub issue when the answer is yes. It reads git and public registries only: no AWS login, no secret, $0.

You need the repository, Python 3.11, Docker (to run a Linux copy of the job on a laptop), and the Trivy release pinned in `.github/workflows/ci.yml` (0.74.0).

## Why

Trivy already scans every image, but only when someone changes something (a pull request, a release). A flaw announced a month after a release is invisible until the next change, and Kaval is parked most of the time. Real companies close this either by scanning the registry continuously (accurate, but needs access into the account and a product to buy) or by a scheduled job in CI. This is the second, and it reads sources, not images.

Why sources are enough: every Kaval image is made from a base image pinned by digest, the locked libraries in `uv.lock`, and Kaval's own code (CodeQL's job). All three are in git. Reading the live images from ECR would need a second CI login into AWS, the reason the nightly drift check was parked ([ADR-0105](../adr/0105-version-1-closes-and-the-nightly-drift-check-moves-to-future-scope.md)).

## Part 1 — The rules, as tested functions

`scripts/ops/image_watch.py`, with `test_image_watch.py` (offline: Trivy's answer is a small dictionary). The decisions are:

- **Which commits.** `main`, and every `sha-…` tag that `deploy/environments/prod/values.yaml` pins (a commented-out tag is not a pin).
- **Which images.** The base image of the **last** `FROM` of each service's Dockerfile (earlier stages are thrown away), and every whole-image line in `deploy/charts/kaval/values.yaml` (pgvector, Ollama, Prometheus).
- **What counts.** HIGH and CRITICAL with a fix available, minus the dated exceptions in `.github/trivy-ignore.yaml`: the same bar as CI.
- **Base images are read for their operating-system packages only.** The Dockerfiles remove pip, setuptools and wheel from the finished image (their last stage), and the libraries that stay are in `uv.lock`, scanned separately. Reading the base image whole reported two findings that are not in any Kaval image (`jaraco.context` and `wheel`, both inside setuptools): a false alarm the first run showed.
- **A scan that read too little is not clean.** A lock file must show at least 20 packages, an image at least 10; fewer is "broken", and broken beats found in the verdict.
- **One target that cannot be scanned does not hide the rest.** It is reported as such and the others still run.
- **Grouped by package.** The first run found 108 vulnerabilities; as one line each that is an issue nobody reads. They become 18 lines (one per package: "27 problems in stdlib v1.26.0, worst HIGH; fixes in …"), and the whole list stays in the run log.

```bash
python -m pytest scripts/ops/test_image_watch.py -q
```

## Part 2 — The workflow

`.github/workflows/image-watch.yml`, with `scripts/dev/test_image_watch_workflow.py` to keep it honest. Two jobs, the shape of `backup-watch.yml`:

- `scan` (token: `contents: read` only): checks out the full history (production's pinned commit is older than a shallow clone), installs Trivy from its release and checks the pinned checksum, downloads the vulnerability list with five retries, runs the script.
- `tell` (token: `issues: write` only): opens or edits **one** issue labelled `image-watch` (GitHub e-mails it when it is opened), turns the run red (a second e-mail every week it stays), and closes the issue when a run is clean. If `scan` could not run, `tell` says so: silence is never "all fine".

The report is CVE ids and package names; it reaches `tell` only through an environment variable and is never pasted into a command, and the test fails if an expression appears inside the script.

A hand run has one input, `plant`: it adds a requirements file with three deliberately old libraries, built at run time and never committed (a vulnerable file in git would raise real alerts), to prove the watch can find something.

## Part 3 — Prove it on a laptop, in Linux

The scan has to behave as it will on GitHub's Linux runner, and the laptop is Windows (the first local run failed on an image layer Windows cannot unpack). So run it in a Linux container. Download the Linux Trivy release and compare its checksum with the release's published list before using it:

```bash
docker run --rm -v "$PWD:/repo" -v "<folder with the linux trivy>:/tools:ro" -w /repo python:3.11-slim-bookworm bash -c '
  apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null &&
  git config --global --add safe.directory /repo && cp /tools/trivy /usr/local/bin/ &&
  trivy image --download-db-only --quiet &&
  python scripts/ops/image_watch.py --plant --report /repo/report.txt; echo exit=$?'
```

Exit 0 is clean, 1 is findings, anything else is "the check broke". It takes about 8 minutes, most of it pulling the language-model image.

## Part 4 — What the first real runs showed (2026-11-19)

| Run | What happened | What changed |
|---|---|---|
| Windows laptop | `failed to analyze layer`: one image would not unpack on Windows. The script said "broken", not "clean" | Run it in Linux (Part 3). The "broken" path worked first time |
| Linux, run 1 | Trivy's 5-minute limit ran out on the language-model image (several gigabytes of GPU libraries); that one error threw away everything else | `--timeout 20m`; skip the folder of copied-in GPU libraries; one failing target no longer hides the others |
| Linux, run 2 | 108 lines in one report, and two "findings" in the Python base image that Kaval's finished images do not contain | Group by package; read base images for operating-system packages only |
| Linux, final | `uv.lock` (77 packages) and the base image (105 packages) clean, for `main` and for production's pin; the planted file found 10 problems in 3 libraries; **three real groups of findings** (below) | — |

The real findings, none of them reachable from the internet and none touching production's running state today (production is parked):

- **Prometheus v3.15.0** (switched on in staging and production): 14 problems in the Go standard library and `x/net` it was built with. Fix: a newer Prometheus release.
- **Ollama 0.34.4** (switched on in staging and production): 55 problems: the Go standard library, `x/crypto`, `x/net` and others, and Ubuntu's `apt` and `openssl`. Fix: a newer Ollama release.
- **pgvector pg16** (the in-cluster database image used where no separate database server exists, such as staging): 27 problems in the Go standard library of a bundled tool. Fix: a newer pgvector image.

**No updater covers these three.** Dependabot reads the Dockerfiles, and these images are named in the chart's values, not in a Dockerfile. This is the most useful thing the watch found, and it was found before it was ever merged. `KAV-169` moves them.

## Part 5 — The live proof

After the pull request is merged:

1. Run the workflow by hand (Actions, image-watch, Run workflow) with `plant` ticked. It must turn red and open one issue that starts "THIS WAS A TEST RUN".
2. Run it again with `plant` unticked. It will still find the three real groups above, so the issue stays open and is edited to say so (not a second issue).
3. When `KAV-169` clears the real findings, the next run must close the issue by itself.

The record of steps 1 and 2 is in the journal for the day they were done.

## Check yourself

1. Why does the scan read `uv.lock` and the Dockerfiles instead of the images in ECR?
2. A scan reads 4 packages and finds nothing. Is that clean? What does the script say, and why does "broken" beat "found"?
3. Why is the planted vulnerable file built at run time and not committed?
4. Why are the Python tools in the base image ignored for Kaval's images, and which scan covers the libraries that stay?
5. Name two things this watch cannot see (ADR-0106), and what covers each.

## Cleanup

Nothing to tear down: it creates no cloud resource. A leftover test issue is closed by the next normal run that finds nothing, or by hand with `gh issue close`.
