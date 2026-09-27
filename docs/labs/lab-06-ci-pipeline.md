# Lab 06 — CI: every pull request proves itself

**Phase:** 1 · **Time:** ~1 hr · **Cost:** $0 (GitHub Free's 2,000 Actions minutes a month for
private repos; one run bills 6 of them, see [Step 5](#step-5--see-what-a-run-costs))

Until now, "green" meant "green on my laptop, with whatever package versions my laptop has".
This lab adds `.github/workflows/ci.yml`. It runs on every pull request, and on `main` after each
merge, and a PR merges only when every check is green
([contributing](../contributing.md#from-kav-24-on-pull-requests-green-checks-then-merge-a-rule-not-yet-enforced)).
The decisions behind it are in [ADR-0010](../adr/0010-ci-pipeline-and-supply-chain.md).

---

## What CI checks

Five jobs, in parallel:

| Job | Runner | What it proves |
|---|---|---|
| **lint** | `ubuntu-24.04` | `make lint` passes: ruff and mypy strict, the same target as on the laptop. On a PR, every commit message it adds follows the convention (added by `KAV-25`, [Lab 07](lab-07-commits-and-jira-link.md)) |
| **test** | `ubuntu-24.04` + a `pgvector/pgvector:pg16` service container | Migrations apply to an empty database, match the models (`alembic check`), reverse, and re-apply. All 61 tests pass (40 against real Postgres, plus 21 for the commit checker since `KAV-25`); none can skip |
| **secrets** | `ubuntu-24.04` | gitleaks finds nothing in the **whole history** |
| **terraform** | `ubuntu-24.04` | Every `.tf` file is formatted, and every environment validates, offline |
| **images** (×2) | `ubuntu-24.04-arm` | gateway and collector build natively for arm64, run as uid 10001, import their code, and have no fixable HIGH/CRITICAL vulnerabilities (Trivy) |

It **builds but never pushes**. Publishing images belongs to `release.yml` in Phase 4, because
only a merged commit should ever produce an artifact.

## Prerequisites

- [Lab 05](lab-05-gateway-api-and-synthetic-signals.md) done: the repo has code worth checking
- `uv` installed (`winget install astral-sh.uv`, or see [docs.astral.sh/uv](https://docs.astral.sh/uv/))
- `gh` (the GitHub CLI) logged in: `gh auth status`

---

## Step 1 — Read the files before running them

| File | Read it for |
|---|---|
| `.github/workflows/ci.yml` | The header comment (why SHAs, why checksums), then each job top to bottom |
| `uv.lock` | Just skim: every package, its exact version, and a sha256 for every file it could download |
| `.github/dependabot.yml` | Four ecosystems, weekly, grouped; and what it deliberately doesn't cover |
| `services/*/Dockerfile` | The two stages: `lock` turns `uv.lock` into a hashed requirements file; the runtime stage installs only that |
| `services/conftest.py` | `KAVAL_REQUIRE_DB`: skip on the laptop, fail in CI |

Three things to notice in `ci.yml`:

- **`permissions: contents: read`.** The token each job gets can read the repo and do nothing
  else. A compromised step can't push code, open releases or change settings.
- **`uses: actions/checkout@3d3c42e…  # v7.0.1`.** The action is pinned to a commit SHA, with the
  tag as a comment. A tag is a movable label: in March 2026 someone with stolen credentials moved
  76 of `trivy-action`'s 77 tags to malware, and every workflow using `@v0.x` ran it
  ([CVE-2026-33634](https://github.com/advisories/GHSA-69fq-xp46-6x23)). A SHA names one exact commit.
- **`sha256sum --check`.** gitleaks and Trivy are downloaded directly, and their checksums are
  written into the file. If someone replaces the release asset, the job fails instead of running it.

## Step 2 — Use the lock on the laptop too

```bash
make sync        # uv sync --locked --all-extras: .venv becomes exactly what uv.lock says
make lint
make test        # 28 pass, 12 skip without the dev server; 40 pass with make dev + make dev-tunnel
```

Adding a dependency is now two steps:

```bash
# edit pyproject.toml, then
make lock        # re-resolves uv.lock
git add pyproject.toml uv.lock
```

Forget `make lock` and CI refuses the PR. You can see that without pushing anything:

```bash
uv lock --check  # "The lockfile needs to be updated" after an unlocked edit
```

## Step 3 — Open a pull request and watch it

```bash
git checkout -b feat/KAV-<n>-<slug>
# ...commit...
git push -u origin feat/KAV-<n>-<slug>
gh pr create --fill
gh pr checks --watch
```

Each check links to its log. A red check is fixed **on the branch** and pushed again. The run
restarts by itself, and `concurrency` cancels the one it replaces.

## Step 4 — Merge only when it's all green

```bash
gh pr checks                       # every line says pass
gh pr merge --merge --delete-branch
git checkout main && git pull
```

`--merge` keeps a merge commit, the same shape `git merge --no-ff` gave before, so
`git log --first-parent main` still reads as one line per finished story.

## Step 5 — See what a run costs

```bash
gh run list --workflow ci.yml --limit 1
gh api repos/{owner}/{repo}/actions/runs/<run-id>/jobs   --jq '.jobs[] | "\(.name)  \(.started_at) → \(.completed_at)"'
```

Measured on the first green run (2026-09-27): **68 seconds** wall-clock, every job under a
minute. GitHub bills each job rounded **up** to a whole minute, so that's **6 billable minutes**
(arm64 runners count the same as x86 on the free minutes). A PR runs CI twice, once on the PR and
once on `main` after the merge, so ~12 minutes a PR, and the free 2,000 cover about 160 PRs a
month. (The older `/timing` endpoint now reports 0 ms for every job, so don't rely on it.)

---

## What broke on the first run (and why that's the point)

The very first CI run went **red on three jobs**, and each failure was a real problem the
laptop had hidden:

**1. Terraform: "the cached package … does not match any of the checksums recorded in the
dependency lock file".** `.terraform.lock.hcl` had been created on Windows, so it only held the
provider's Windows checksum, and the Linux runner couldn't verify its download. Fix: record
checksums for every platform anyone uses.

```bash
terraform -chdir=infra/envs/prod providers lock \
  -platform=windows_amd64 -platform=linux_amd64 -platform=linux_arm64 -platform=darwin_arm64
```

**2. Both images: Trivy, 2 HIGH (CVE-2026-23949 in `jaraco.context`, CVE-2026-24049 in `wheel`).**
Neither is one of our dependencies. The official `python` base image ships `setuptools`, and
setuptools carries its own old copies of both. Nothing uses setuptools, wheel or pip once the
image is built, so the final stage now uninstalls all three. That also means a shell inside the
container has no package installer. A new check in the same job imports the service code, to
prove the stripped image still works.

**3. Before CI even ran, locally: mypy "Error importing plugin sqlalchemy.ext.mypy.plugin".**
Creating the lock resolved SQLAlchemy **2.1.1**, while the old `.venv` still had 2.0.54, and 2.1
removed that plugin. The models already use `Mapped[...]`, so they didn't need it. The plugin
line went, and two `Select[...]` annotations changed to 2.1's form. Without a lock, the laptop,
CI and each image would each have installed whatever was newest on the day they ran.

Also local, also found by the lockfile: **the pre-commit hook refused the commit** because "a
12-digit number is being committed". It was digits inside sha256 hashes in `uv.lock`, not an
account ID. The check in `scripts/dev/install-hooks.sh` now ignores a digit run that's part of a
hex string. It still catches an ARN (`arn:aws:iam::` followed by 12 digits) and an
`"account_id": "…"` value. It proved that by blocking the first draft of this very lab, which
had a made-up account ID in exactly that spot.

---

## Done when

- [ ] `make sync && make lint && make test` pass on the laptop
- [ ] A pull request shows the seven checks (lint, test, secrets, terraform, 2 × images, plus Dependabot's own config check), all green
- [ ] The test job's log ends with `61 passed` (40 before `KAV-25` added the commit checker's tests), and no skips
- [ ] `uv lock --check` fails after an unlocked `pyproject.toml` edit
- [ ] The PR merged with `gh pr merge --merge` only after every check was green
- [ ] Journal entry appended

---

## What to write down

- Why an action pinned by tag isn't pinned at all, and what a SHA pin protects against
- Why CI must be able to *fail* when the database is missing, not skip
- What a lockfile catches that "it works on my machine" doesn't (here: SQLAlchemy 2.1)
- Why a vulnerability scanner flags packages you never installed, and why the fix is removing them
  rather than ignoring the finding
- Why CI builds images but doesn't push them
