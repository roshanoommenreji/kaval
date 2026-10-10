# Lab 36 — Versions worked out from commits, tags, and a change record written by the pipeline

**Phase:** 4 · **Story:** `KAV-67` · **Cost:** $0. Nothing here touches AWS; it reads Git and writes files, a
branch and tags. (The first real run of the workflow changes is at the next release.)

[Lab 32](lab-32-release-publish-stage.md) builds and publishes images, [Lab 34](lab-34-promote-workflow.md)
gates the way to prod and [Lab 35](lab-35-rollback-workflow.md) the way back. This lab gives a release a name a
person can say (`Kaval 0.2.0: gateway 0.2.0 · collector 0.1.0 …`) and a document that explains it. The decisions
are in [ADR-0035](../adr/0035-computed-versions-tags-and-the-generated-change-record.md), which settles the
open part of [ADR-0013](../adr/0013-component-versions-and-release-naming.md).

**Result:** `make version-plan` works out versions from the commits; the baseline `v0.1.0` marks the version
prod is pinned to; running the generator on this repository's real history wrote
`docs/releases/2026-10-09-v0.1.0.md` (39 commits, Jira keys, risk **high** because of a migration, the rollback
plan naming the migration it would cross); a rehearsal in a clone bumped a gateway fix to `0.1.1` and the
resulting `uv.lock` passed `uv lock --check`. 145 release-script tests pass.

---

## 1. What exists

| Piece | Where | Job |
|---|---|---|
| The version maths | `scripts/release/versions.py` (+ `test_versions.py`, 34 tests) | `plan`, `bump`, `tag`, `show` |
| The change record | `scripts/release/change_record.py` (+ `test_change_record.py`, 25 tests) | `render` writes `docs/releases/<date>-vX.Y.Z.md` |
| The bump pull request | `.github/workflows/release-prepare.yml` | run by hand from `main`; opens `chore(release): Kaval X.Y.Z` |
| Tagging | a step in `release.yml`'s `propose` job | after the images are pushed, tags any version that has no tag |
| The record in the promotion | `promote.yml`'s `propose` job | adds the record to the same pull request as the prod pin |
| A shortcut | `make version-plan` | prints the plan; writes nothing |

## 2. The rules

- `fix` is a patch, `feat` a minor, `!` or a `BREAKING CHANGE:` footer a major (a minor while a version is 0.x).
  `docs`, `chore`, `infra`, `ci` and the rest move nothing.
- A commit moves a component if it touched a file the component's image carries: its own folder;
  `services/shared/`, `pyproject.toml` and `uv.lock` for the four deployed services; `migrations/` for the
  gateway; `policy/` for the agent and executor; four scripts for `backup`. The table is `SHIPS` in
  `versions.py`, and `test_ships_agrees_with_what_the_dockerfiles_copy` fails if a Dockerfile disagrees.
- The product moves by the largest move among its components.
- Counting starts at the tag named by the version the code has now (`gateway-v0.1.0`). No such tag means
  "cannot decide" (exit 2), not "nothing changed".

## 3. Try it

```bash
make version-plan
# Kaval 0.1.0: nothing has changed that moves a version.        (once the baseline tags exist)

python scripts/release/versions.py show --rev 2459417
# Kaval 0.1.0: gateway 0.1.0 · collector 0.1.0 · agent 0.1.0 · executor 0.1.0 · backup 0.1.0
```

The baseline, made once (it needs your approval to push, which is why it is not in a workflow):

```bash
python scripts/release/versions.py tag --commit 24594177dfc5a3cb8ccde837bf1b9b01c5d04a1b --push
# gateway-v0.1.0 ... backup-v0.1.0, v0.1.0: tagged 2459417
```

A release from then on: **Actions → release-prepare → Run workflow** (on `main`). If a commit since the last tags
moves a version it opens `chore(release): Kaval 0.2.0` with a table of what moves and why; merge it, approve the
bot's first CI run, and `release.yml` builds the images with the new versions on them and tags them. If nothing
moves the job says so and opens nothing.

The change record needs nothing from you: `promote.yml` writes it into the promotion pull request, and
merging that pull request is both the go/no-go and the record.

## 4. The rehearsal in a clone (what proves the bump)

```bash
git clone . /tmp/clone && cd /tmp/clone && git checkout 2459417
python scripts/release/versions.py tag --commit HEAD              # the baseline, in the clone only
echo "x = 1" >> services/gateway/kaval_gateway/__init__.py
git commit -am "fix(gateway): test fix (KAV-67)"
python scripts/release/versions.py plan     # Kaval 0.1.0 -> 0.1.1 ; gateway: 0.1.0 -> 0.1.1 (1 commit)
python scripts/release/versions.py bump     # writes the gateway's __init__.py, pyproject.toml, uv.lock
uv lock --check                             # passes: the lock agrees with pyproject.toml
```

`uv.lock` records the project's own version, and CI runs `uv sync --locked`, so a bump that forgot the lock
would turn every later pull request red. The first version of `bump` did forget it; the test fixture and this
rehearsal caught it.

## 5. The real record

```bash
python scripts/release/change_record.py render --tag sha-2459417 --previous sha-ffb436b \
  --actor Roshan --date 2026-10-09 --note "Backfilled ..."
# docs/releases/2026-10-09-v0.1.0.md
```

It lists 39 commits grouped by component, nine Jira keys (`KAV-32` … `KAV-61`), **risk: high** (a migration, the
executor, infrastructure, shared code and dependencies each add a reason), the seven checks of the staging pass
and what that pass did not cover, and a rollback plan that names the one migration going back would cross and
points at the runbook. Its verification section says "not recorded yet", because the file is written before
anything has run. `scripts/tracking/dashboard.py` parses it (a test does exactly that), so the Releases panel and
the delivery measures have their first row.

## 6. What went wrong, in order

1. **The dependency check.** My first `bump` rewrote `pyproject.toml` only. `uv.lock` holds `kaval`'s version
   too, and `uv sync --locked` (CI, and the Dockerfiles use `--frozen`) fails on a mismatch. Found by building the
   scratch fixture, fixed by a `rewrite_uv_lock`, confirmed with `uv lock --check` against the real lock file.
2. **The mapping test failed first time.** `COPY pyproject.toml uv.lock ./` copies two files; my parser read
   only one. Fixed, and the test is now the reason the table can be trusted.
3. **The previous release lacked a component.** Generating the record against `sha-ffb436b` failed because
   `backup` did not exist then. Older releases are now read tolerantly and show `(new)`.
4. **The pull request text had been wrong since the first promotion.** `promote.yml` rewrites prod's files and
   then writes the text; the text read the rewritten files, so pull request #83 said "Prod runs sha-2459417
   now". Found because the new generator needed "what did prod run before" and got the same wrong answer. Fixed
   with `committed_prod_tag` (reads the last commit) and a test.
5. **A formatting accident in my own edits** (line breaks inside strings in a test file) cost a few minutes; the
   linter and the tests caught it before it was committed.

## What this does not do

- **Two of the three workflow changes have not run in GitHub Actions yet.** `release-prepare.yml` has: after
  the baseline tags were pushed (2026-10-10) it was dispatched on `main` and reported "nothing has changed that
  moves a version", which proves the checkout, the tags and the plan in CI. `release.yml`'s tag step and
  `promote.yml`'s record step run at the next real release and promotion.
- A component version describes its own folder, not the whole image: every image also holds the gateway,
  collector and agent code.
- "Approved by" is who started the promotion; "Rolled back" is `no` until `rollback.yml` marks it (KAV-71); no Jira Release is created yet
  (all in ADR-0035's limits).

## Reproduce

1. `make version-plan` and read the output; run `python scripts/release/versions.py show --rev <sha>` for an old
   commit.
2. Run section 4 in a throw-away clone and confirm `uv lock --check` passes after `bump`.
3. Run section 5 into a scratch copy and open the file; check it against `docs/releases/README.md`.
4. `pytest scripts/release -q` (145 tests).
