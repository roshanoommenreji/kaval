# ADR-0010 — CI pipeline and supply-chain hygiene

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Roshan
- **Jira:** `KAV-24`

## Context

Until `KAV-24`, "green" meant green on the laptop, with whatever package versions the laptop
happened to have. From `KAV-24` on, every change reaches `main` through a pull request that merges
only once every CI check is green ([contributing](../contributing.md)). So CI now decides what
"verified" means, and what it checks is worth settling once.

A CI job also runs with the repository's token, next to the code, so the pipeline itself is part
of the attack surface. This isn't hypothetical. In March 2026, 76 of the 77 version tags of
`aquasecurity/trivy-action` were re-pointed to credential-stealing code
([CVE-2026-33634](https://github.com/advisories/GHSA-69fq-xp46-6x23)). Every workflow that
referenced the action by tag ran it.

## Decision

**What CI checks** (`.github/workflows/ci.yml`: five parallel jobs, on every PR and on `main`):

| Job | Checks | Why this way |
|---|---|---|
| lint | `make lint`: ruff and mypy in strict mode | The same target as the laptop, so the two can't drift apart |
| test | `alembic upgrade head` → `alembic check` → `downgrade base` → `upgrade head`, then pytest | The tests run against a **real `pgvector/pgvector:pg16` service container**, the image compose runs, not SQLite or mocks. `KAVAL_REQUIRE_DB=1` turns the fixtures' "skip if there's no database" into a failure, so 12 database tests can't quietly vanish from a green run |
| secrets | gitleaks over the **full history** | The repo is public (from 2026-09-30; planned for v1 when this was written), so its history has to be clean too, not just the latest commit |
| terraform | `fmt -check`, and `init -backend=false` + `validate` for every environment | Offline, with no AWS credentials. Planning against the real account needs OIDC and belongs with the Phase 4 deploy work |
| images | Both images built on **`ubuntu-24.04-arm`**, checked for arm64 and uid 10001, then scanned by Trivy | Built natively on the architecture that runs them (CLAUDE.md constraint 2), with no QEMU emulation. Trivy fails on HIGH or CRITICAL findings **that have a fix**; unfixed ones are reported, since there's nothing to act on until the base image is patched |

**What CI does not do.** It builds but never pushes. Publishing to ECR, SBOMs (syft), signing
(cosign) and deploys are `release.yml`'s job in Phase 4 ([ADR-0004](0004-environment-strategy-and-promotion.md)).
Only a merged commit should produce an artifact.

**Supply-chain rules:**

| Rule | How |
|---|---|
| Least-privilege token | `permissions: contents: read` for the whole workflow. No job reads a secret; checkout doesn't keep the token (`persist-credentials: false`) |
| Actions pinned to a commit SHA | `uses: actions/checkout@3d3c42e…  # v7.0.1`. A tag can be moved; a SHA can't |
| Tool binaries checked against a pinned checksum | gitleaks and Trivy are downloaded straight from their releases and checked against a sha256 written into `ci.yml`, not through wrapper actions. A replaced release asset fails the job |
| Locked dependencies | `uv.lock` (46 packages, each with its file hashes). CI runs `uv sync --locked`, which fails if the lock and `pyproject.toml` disagree. Both images install from the same lock with `pip --require-hashes`, then our own package with `--no-deps` |
| Pinned base images | `python:3.11-slim-bookworm@sha256:…` and the `uv` build stage, pinned by digest. The build backend (`hatchling`) is pinned too |
| Updates are proposed, never automatic | Dependabot, weekly, one grouped PR per ecosystem (actions, uv, Docker, Terraform), each going through CI like any other change |

## Alternatives rejected

- **pip-tools `requirements.txt` instead of `uv.lock`.** It works, but that's one file per extra,
  kept in step by hand. uv produces one cross-platform lock, is what Dependabot now supports
  natively, and sets up the per-service packaging (uv workspaces) that's due for review before
  Phase 3.
- **Actions pinned by tag (`@v7`).** That's the common way, and the exact failure described in
  the Context above.
- **`trivy-action` / `gitleaks-action`.** Wrappers add a second thing to trust around the binary.
  Downloading the binary and checking its checksum is a few lines and hides nothing.
  The cost: Dependabot can't bump these, so their version and checksum are changed by hand,
  together.
- **Building arm64 under QEMU on x86 runners.** Slower, and emulation has hidden real arm64
  failures in the past. Native arm64 runners have been available for private repos on the free
  minutes since January 2026.
- **Running the database tests against SQLite.** It has no JSONB, no `tuple_` row comparison
  and no `READ ONLY` transactions, and the gateway relies on all three.
- **Enforcing the merge rule with branch protection now.** GitHub Free doesn't offer it on private
  repos, and GitHub Pro (~$4/month) was declined on 2026-09-27. It becomes a Phase 9 task, once
  the repo is public.

## Consequences

- Adding a dependency is now two steps: edit `pyproject.toml`, then `make lock`. Commit both. CI
  refuses a `pyproject.toml` whose lock is out of date.
- The lock surfaced drift immediately: it resolved SQLAlchemy 2.1, which removed the mypy plugin
  the config still loaded. The plugin was dropped (the models already use `Mapped[]`, so they
  don't need it) and the `Select` annotations were updated. Without a lock, the laptop, CI and
  each image would each have resolved whatever was newest on the day they ran.
- The pre-commit account-ID check was matching digit runs inside sha256 hashes, so it would have
  blocked every lockfile. It now ignores digit runs that are part of a hex string.
- Cost: $0. When written, the repo was private and GitHub Free included 2,000 Actions minutes a month for private repos (since 2026-09-30 the repo is public and the minutes are not capped). A run billed 6
  (68 s wall-clock, each job rounded up to a minute), about 12 a PR counting the run on `main`
  (measured in Lab 06).
- `opa test` joins the test job in Phase 2, once `policy/` holds policies.

## Amendment 2026-10-10 (KAV-69): all five images, and one dependency held back

- **Coverage.** Dependabot's Docker entry listed three folders (gateway, collector, agent), so the executor
  (which bundles OPA and is the only component that changes anything) and the backup job never got a base-image
  update. Both are added; all five Dockerfiles are now watched.
- **A major version held.** The weekly python group tried to move `kubernetes` 36.0.3 to 37.0.0. CI refused it:
  version 37 ships type hints, and mypy reported 26 errors in the executor's and the collector's `k8s.py`. The
  executor restarts and deletes real pods, and its unit tests use fakes, so a green build would not prove the
  new client still works against a cluster. `dependabot.yml` now ignores *major* `kubernetes` updates; minor and
  patch updates still arrive. `KAV-70` does the upgrade properly (fix the types, exercise the executor on
  staging) and removes the ignore.
- **Why not merge the other seven separately.** Dependabot groups by ecosystem, so one failing member blocks the
  whole group. Ignoring the one dependency is smaller and keeps the weekly rhythm.
- **Rejected:** loosening the mypy settings to let 37 through (hides the problem the new types are reporting),
  and merging with the failing check bypassed (the 11 required checks are the point of ADR-0010).
- **Closed 2026-10-10 (KAV-70).** The client is on 37.0.1 and the ignore is gone. The executor's `k8s.py` falls back
  to empty metadata/status/spec objects (the library now types every field as optional; a real pod always has them),
  the collector's `poll()` takes a small `EventSource` protocol so its test fake still fits, and the library's two
  untyped config loaders carry a one-line ignore. The executor then ran on staging against a real cluster (Lab 38):
  found, recorded and deleted a pod, saw its controller replace it, and reported a missing pod as "not found".
