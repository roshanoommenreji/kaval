# Lab 83 — Every promotion is a tagged release

**Phase:** 8 (hardening) · **Story:** `KAV-135` · **Time:** about 45 minutes · **Cost:** $0 for Parts 1 to 4 (local and CI). Part 5 is the live proof: about 1 cent for a staging smoke test, and production's next start-up (about 2 cents, shared with other work).

By 8 November 2026 production had been promoted twelve times and still read `v0.1.0`. The readable version tags come only from a step a person must remember (`release-prepare`), and nothing checked that it had been run. A second hole sat behind it: a library update changed every image and moved no version, so two different builds could share a name. This lab closes both: the version rule, and a gate that refuses a build that is not a tagged release. Read [ADR-0081](../adr/0081-a-promotion-must-be-a-tagged-release.md) for the reasoning.

## Prerequisites

- The repository checked out, `make sync`
- Git history **and tags** fetched (`git fetch --tags`)

## Part 1 — See the problem

```bash
make version-plan
git tag --list "v*"                 # only v0.1.0
ls docs/releases/ | grep -- "-2"    # records that needed a "-2" because a version was reused
```

`make version-plan` says `0.1.0 -> 0.2.0` on all five components: dozens of changes since the 9 Oct baseline, never released.

## Part 2 — The rule (tests first)

`scripts/release/test_versions.py` gained tests that failed first:

| Test | What it pins down |
|---|---|
| `test_a_library_update_is_a_patch_for_every_service_that_ships_it` | `chore(deps)` on `uv.lock` and `pyproject.toml` is a patch for the four services |
| `test_a_docs_commit_that_edits_an_image_file_still_moves_a_patch` | if the bytes differ the version differs |
| `test_a_change_to_tests_alone_moves_nothing` | tests are not in any image |
| `test_the_release_robots_own_commit_moves_nothing` | otherwise each release would ask for the next one |
| `test_a_library_update_after_a_release_asks_for_the_next_version` | the gap that caused the `-2` records |

```bash
python -m pytest scripts/release/test_versions.py -q
```

The code is `is_image_file`, `is_release_commit` and `levels_by_component` in `scripts/release/versions.py`.

## Part 3 — The gate (tests first)

`promote.py` gained `check_release(release_tags, code_version, prod_version, on_main)`, called by `verify`. It passes only for a build whose commit is on `main`, carries an **annotated** `vX.Y.Z` tag, whose tag equals the version in the code, and whose version is newer than the one prod runs. `staging_smoke.py` runs the same check before it records a pass; `release.yml` writes "release vX.Y.Z" or "NOT a release" into the staging pull request using `versions.py release-of`.

```bash
python -m pytest scripts/release/test_promote.py scripts/release/test_staging_smoke.py -q -k "release or tagged or untagged"
```

The negative cases are the point: no tag, a tag that disagrees with the code, the version prod already runs, an older version, a commit off `main`, a lightweight (bare) tag. Each refusal tells you which step to run.

## Part 4 — Rehearse on the real history, in a throwaway clone

Nothing here pushes or touches AWS. Clone, then do by hand what `release-prepare` and `release.yml` do:

```bash
git clone --no-hardlinks . /tmp/kav135-rehearsal && cd /tmp/kav135-rehearsal
python scripts/release/versions.py plan        # Kaval 0.1.0 -> 0.2.0
python scripts/release/versions.py bump
git commit -am "chore(release): Kaval 0.2.0"
python scripts/release/versions.py tag --commit HEAD
python scripts/release/versions.py plan        # "nothing has changed that moves a version": no loop
python scripts/release/versions.py release-of  # v0.2.0
```

Then ask the gate about the tagged commit and about the commit before it. On 2026-11-08 the first said `v0.2.0 (prod runs v0.1.0)` and the second said `no version tag on the build's commit. Run release-prepare ...`.

## Part 5 — The live proof (each step on Roshan's own go)

1. Actions → **release-prepare** → Run workflow on `main`. Approve and merge its pull request. `release.yml` makes the first real tags (`v0.2.0`, `gateway-v0.2.0`, ...) and its staging pull request must say "This build is release v0.2.0".
2. `make staging-up`, `make staging-smoke`, which now prints `PASS build is a tagged release: v0.2.0 (prod runs v0.1.0)` and records the pass; `make staging-down`.
3. **Negative, live:** `promote.yml` with the previous (untagged) staging build must stop at the gate with the plain message.
4. `promote.yml` with the tagged build (since `KAV-136` you can type the version, `tag=v0.2.0`, instead of `sha-...`; [Lab 90](lab-90-promote-by-release-version.md)); merge the pull request. The change record is named `...-v0.2.0.md`, no `-2`.
5. At production's next start-up, `make prod-smoke` runs against it, and the running gateway reports the version: `curl <gateway service>/healthz` from the app server (over Session Manager) answers `"version":"X.Y.Z"`.

**Step 5, seen 10 November 23:37 IST (all five steps done).** Production's next start-up came with the release after `v0.2.0`: `v0.3.0` (`sha-5ec8e6a`). `make prod-smoke` passed on its third try, 5 minutes after the server was started: ten pods running with no restarts, every service on `sha-5ec8e6a`, all four running digests equal to the registry's, the database migrated to `d4f7a2c9e815`, the two API calls returned 200, the build had passed staging (2026-11-10 16:01 UTC), and the server's three timers were set. The gateway's health answer read `"version":"0.3.0"`. So production reads the version tag that was promoted.

**Result of steps 1 to 4 (2026-11-08 and 2026-11-09).** The release round ran: pull request #350 (`Kaval 0.2.0`) made all five parts 0.2.0, and `release.yml` made the first real tags (`v0.2.0` plus one per part). Its staging pull request (#351) said "This build is release `v0.2.0`". The staging smoke test on `sha-1ff61c8` passed 7 of 7 at about 00:18 IST on 9 November, including the new line `PASS build is a tagged release: v0.2.0 (prod runs v0.1.0)`, and recorded the pass; staging was deleted afterwards (about 1 cent, sweep clean). **Step 3 (negative, live), 9 November 22:19 IST:** a read-only `promote.py verify --tag sha-ba87981` said no, then `promote.yml` with `tag=sha-ba87981` failed at the gate job with two plain lines, `prod already runs sha-ba87981` and `no version tag on the build's commit. Run release-prepare (Actions tab), merge its pull request, and promote the build that one makes (code says 0.1.0)`; the propose job was skipped, no pull request opened, production untouched. **Step 4, 22:23 IST:** the same dry run for `sha-1ff61c8` passed all five checks (`build is a tagged release: v0.2.0 (prod runs v0.1.0)`), `promote.yml` opened #353 (production's two pin files and the change record `docs/releases/2026-11-09-v0.2.0.md`, no `-2`), and Roshan merged it. Step 5 is above.

Results are in `docs/journal/2026-11-08.md` and `docs/journal/2026-11-09.md` as they happen.

## What to take away

- A version has to identify one build. The rule that every image change moves one is what makes that true; the gate is what makes it checked rather than hoped.
- A gate that says "refused" without saying what to do just moves the confusion. Every refusal here names the step.
- The earlier a check runs, the cheaper it is: the staging pull request says it, the smoke test refuses to record it, and only then does the promotion gate refuse.
