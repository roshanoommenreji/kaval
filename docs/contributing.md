# Branches, commits, and how work reaches `main`

## The one rule

**`main` is always the verified state.** Anything on `main` has passed its tests and its
Definition of Done (`CLAUDE.md`). Work in progress lives on a branch until it gets there.

## Branches are for work in progress, not for organising things

One short-lived branch per piece of work, usually one Jira story:

```
<type>/KAV-<n>-<slug>

feat/KAV-22-docker-compose
fix/KAV-24-trivy-arm64
docs/KAV-26-phase-1-concept-page
infra/KAV-31-staging-vpc
chore/repo-structure          # housekeeping with no story is fine
```

`<type>` matches the commit prefixes: `feat` · `fix` · `docs` · `infra` · `chore`.

```bash
git checkout -b feat/KAV-24-ci
# ...work, commit...
make lint && make test                   # green locally first; it's faster than waiting for CI
git push -u origin feat/KAV-24-ci
gh pr create --fill
gh pr checks --watch                     # every check must pass; a red one is fixed on the branch
gh pr merge --merge --delete-branch      # only once all green
git checkout main && git pull
python scripts/tracking/jira-sync.py transition KAV-24 Done
```

`--merge` keeps a merge commit, so `git log --first-parent main` reads as one line per finished
story. (Up to `KAV-23`, the same shape came from a local `git merge --no-ff`, then `git push`.)
What CI checks, and why, is in [ADR-0010](adr/0010-ci-pipeline-and-supply-chain.md) and
[Lab 06](labs/lab-06-ci-pipeline.md).

If a change adds or bumps a Python dependency, run `make lock` and commit `uv.lock` with
`pyproject.toml`. CI refuses a lock that doesn't match.

The remote is [github.com/roshanoommenreji/kaval](https://github.com/roshanoommenreji/kaval). It
has been **public since 2026-09-30** (pulled forward from Phase 9 on purpose; see that day's
journal). Push after every merge; an unpushed commit exists only on one laptop.

### Pull requests, green checks, then merge (enforced since the repo went public)

CI exists from `KAV-24` (`.github/workflows/ci.yml`), so the local merge is gone. The branch is
pushed and opened as a pull request (`gh pr create`). It merges into `main` (keeping a merge commit, as `--no-ff` does) **only
after every CI check on it is green**. A red check is fixed on the branch, never merged over.

Until 2026-09-30 this was a written rule only, because GitHub Free offers no branch protection on
private repositories. Going public made it free, and `main` now has branch protection with
required status checks (all ten: lint, test, secrets, terraform, helm, and the five image builds;
`helm`, `image executor` and `image backup` joined on 2026-10-08, `KAV-62`) and "branch must be up
to date". One honest limit: administrators can still bypass it (`enforce_admins` is off, so keep
GitHub 2FA on). `release.yml`'s staging-pin pull request goes through the same protection like any other.

Because the repository is public, three more settings matter (set 2026-10-08, `KAV-61`):
**secret scanning** and **push protection** are on (GitHub refuses a push that contains a
recognisable credential, before it can reach history, which is permanent); and workflow runs from
**every outside contributor** need a person's approval first (`all_external_contributors`, not just
first-time ones). They sit on top of the local pre-commit scan and CI's full-history gitleaks job. Not
done: restricting which third-party actions workflows may use (they are already pinned to commit SHAs),
and `enforce_admins`, so the owner's account stays the weak point: keep GitHub 2FA on.

### What there is deliberately no branch for

- **No `staging` or `prod` branch.** [ADR-0004](adr/0004-environment-strategy-and-promotion.md)
  builds each image once and promotes that exact artifact. Environments differ only by a Helm
  values file in `deploy/environments/` and a pinned image digest. An environment branch would
  put different code in each environment, which is exactly what the ADR rules out.
- **No branch per tool** (Jira, Confluence, AWS...). Tools get a *folder*. See the "Which tool
  lives where" table in the [README](../README.md#repository-map).
- **No long-lived `develop` branch.** A solo project with weekly sessions gains nothing from a
  second integration line, and it would drift.

## Commits

Conventional commits, with the Jira key:

```
<type>[(scope)][!]: <KAV key> <what changed>

feat: KAV-22 docker-compose stack with Ollama, Postgres and gateway
fix(ci): KAV-24 multi-platform Terraform locks
docs: KAV-25 Lab 07 and ADR-0011
chore: tidy the root                       # no story, no key: fine off a story branch
```

| Rule | Why |
|---|---|
| `type` is `feat` · `fix` · `docs` · `infra` · `chore`, then `: ` | The type makes history machine-readable: release notes group by it, and Phase 4 derives the version bump from it |
| Subject at most 100 characters | It has to fit in `git log --oneline` and in a PR list |
| On a `…/KAV-<n>-…` branch, the subject carries a KAV key | The key is what shows the commit on its Jira issue (below) |
| No smart-commit commands (`#comment`, `#time`, `#done`) | They'd be ignored: see below |
| No `fixup!`/`squash!` commits in a pull request | Fine while working; squash them with `git rebase -i --autosquash main` before pushing |

Merge and revert commits are written by Git, so they're exempt. `scripts/dev/check_commits.py`
checks all of this twice, from the same code: the `commit-msg` hook refuses a bad message before
the commit exists, and CI's lint job re-checks every commit a pull request adds. A clone without
the hook still can't get one merged.

Install both hooks once after cloning: `./scripts/dev/install-hooks.sh`. `pre-commit` runs gitleaks
and the account-ID check; `commit-msg` runs the convention check. Never bypass them with
`--no-verify`. If one blocks a commit, fix the thing it found. For a bad message, just commit again
with a better one; nothing was lost.

### Jira sees the code through the key, not through commands

The **GitHub for Atlassian** app (Atlassian's, free, installed on this repo only) puts a Development
panel on each issue: its branches, commits, pull requests and CI result. It links anything whose
branch name, commit message or PR title contains the issue's key. That's why the key is required.

**Smart-commit commands are deliberately not used.** Jira runs `KAV-25 #done` only if the
commit's author email matches a Jira user's email, and this repo commits under the GitHub noreply
address to keep a personal address out of public history. So the commands would silently do
nothing, and the check refuses them. Issue status moves with
`python scripts/tracking/jira-sync.py transition KAV-<n> <status>`.
[ADR-0011](adr/0011-commit-convention-and-jira-link.md) has the reasoning.

Jira's workflow, in board order: **To Do → In Definition → Ready → In Progress → In Review →
In Staging → Ready for Prod → Done**. "In Staging" and "Ready for Prod" are unused until Phase 4
creates a staging environment. Until then a story goes from In Progress (or In Review, while its
PR is open) straight to Done.

### Versions: each component has its own

Each component has its own [semantic version](https://semver.org), in its code:
`services/<svc>/kaval_<svc>/__init__.py` → `__version__`. A release is named by the **product**
version and lists the components inside it
([ADR-0013](adr/0013-component-versions-and-release-naming.md)):

> **Kaval 0.3.0**: gateway 1.2.0 · collector 0.4.1 · agent 0.1.0

- **What bumps a version:** the commits that touched that component's folder. `fix:` bumps the
  patch, `feat:` the minor, and `!` the major. Shared code bumps every service that ships it, so
  keep a commit to one component where you can.
- **Tags:** `gateway-v1.2.0` for a component, `v0.3.0` for the product.
- **Where a version shows:** `/healthz`, `--version`, and each image's
  `org.opencontainers.image.version` label. CI fails if the label and the code disagree.
- **Who bumps them:** `release.yml` computes and applies the bumps from Phase 4. Until then
  everything stays `0.1.0`, because nothing has been released.

### User acceptance (UAT)

A story that changes what an operator sees or decides gets the label `uat` and a checklist of
**UAT scenarios**, written before the work starts
([ADR-0012](adr/0012-user-acceptance-testing.md)). User-facing changes include API responses,
proposals, notifications, mobile screens and the approve/deny flow. Infra, CI, docs and refactors
don't need UAT.

```bash
# at creation: scenarios go in their own section, and the uat label is added
python scripts/tracking/jira-sync.py create --epic KAV-10 --points 3 --service mobile \
    --summary "..." --context "..." --ac "developer-checked criterion" \
    --uat "Given a crashlooping pod, when I open the Inbox, then I see the cause and Approve"

# once it's In Staging (the UAT environment), after trying it as the user:
python scripts/tracking/jira-sync.py uat KAV-40 pass --env staging --note "what I checked"
python scripts/tracking/jira-sync.py uat KAV-40 fail --env staging --note "what broke"
```

- **`pass`** ticks the UAT scenarios, records who, where and when, and moves the story to
  **Ready for Prod**.
- **`fail`** raises a linked **Bug** (`uat-defect`) and moves the story back to **In Progress**.
- **Both** are refused unless the story is labelled `uat` and is **In Staging**. `pass` is also
  refused while the story's UAT defect is open.
- **Where to test:** `--env dev` until staging exists in Phase 4, then `--env staging`.
- **`--service`** fills the Jira **Service** field (repeatable); it says which components the
  story changes.

**One sprint per phase** (decided 2026-09-27). The board shows the active sprint, so the current
phase's stories live in a sprint named after it, e.g. "Phase 2 — The agent loop", with the phase's
exit gate as its goal. When a phase closes, its sprint closes too, and anything unfinished carries
into the next one. Phase 1 ran with no sprint at all, so none of its work ever appeared on the
board: it went straight from the backlog to Done.

## Finishing a piece of work

Merging is step one of the Definition of Done in `CLAUDE.md`. The rest follows in the same
session: lab doc, journal entry, ADR if a decision was made, `jira-sync.py transition`, then
`make docs-sync` to regenerate the dashboard and Confluence.
