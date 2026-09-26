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
git checkout -b feat/KAV-22-docker-compose
# ...work, commit...
make test && make lint                   # must be green
git checkout main
git merge --no-ff feat/KAV-22-docker-compose
git branch -d feat/KAV-22-docker-compose
git push                                 # main -> GitHub, the off-laptop copy
python scripts/tracking/jira-sync.py transition KAV-22 Done
```

`--no-ff` keeps a merge commit, so `git log --first-parent main` reads as one line per finished
story.

The remote is [github.com/roshanoommenreji/kaval](https://github.com/roshanoommenreji/kaval). It
stays **private until v1**, then goes public in Phase 9. Push after every merge; an unpushed
commit exists only on one laptop. Once CI exists (`KAV-24`), branches get pushed too and merge
through pull requests, so the checks run before anything reaches `main`.

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

Conventional commits, with the Jira key when there is one:

```
feat: KAV-22 docker-compose stack with Ollama, Postgres and gateway
fix: KAV-21 drop ENUM types on downgrade
docs: Lab 03 — local stack
```

The pre-commit hook (`scripts/dev/install-hooks.sh`) runs gitleaks on every commit. Never bypass
it with `--no-verify`. If it blocks a commit, the thing it found needs to come out.

## Finishing a piece of work

Merging is step one of the Definition of Done in `CLAUDE.md`. The rest follows in the same
session: lab doc, journal entry, ADR if a decision was made, `jira-sync.py transition`, then
`make docs-sync` to regenerate the dashboard and Confluence.
