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
stays **private until v1**, then goes public in Phase 9. Push after every merge; an unpushed
commit exists only on one laptop.

### From `KAV-24` on: pull requests, green checks, then merge (a rule, not yet enforced)

CI exists from `KAV-24` (`.github/workflows/ci.yml`), so the local merge is gone. The branch is
pushed and opened as a pull request (`gh pr create`). It merges into `main` (keeping a merge commit, as `--no-ff` does) **only
after every CI check on it is green**. A red check is fixed on the branch, never merged over.

The industry-standard version *enforces* this with a required status check. GitHub Free doesn't
offer branch protection or rulesets on private repositories
([GitHub docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)),
and GitHub Pro (~$4/month) was declined on 2026-09-27. So until v1 it's a written rule, followed
every time. When the repo goes public in Phase 9, protection becomes free, and turning on "require
status checks to pass" on `main` is a Phase 9 task.

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

## Finishing a piece of work

Merging is step one of the Definition of Done in `CLAUDE.md`. The rest follows in the same
session: lab doc, journal entry, ADR if a decision was made, `jira-sync.py transition`, then
`make docs-sync` to regenerate the dashboard and Confluence.
