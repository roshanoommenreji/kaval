# ADR-0081: A promotion must be a tagged release: every image change moves a version, and the gate refuses an untagged build

**Status:** Accepted
**Date:** 2026-11-08
**Related:** [ADR-0013](0013-component-versions-and-release-naming.md) (what a version is),
[ADR-0035](0035-computed-versions-tags-and-the-generated-change-record.md) (how versions are computed and
tagged; this amends it), [ADR-0032](0032-promote-workflow-and-the-promotion-guard.md) (`promote.yml`),
[ADR-0034](0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md) (rollback), `KAV-135`, Lab 83

## Context

Every image is labelled `sha-<7 characters of the commit>`. The readable version (`v0.1.0`,
`gateway-v0.1.0`) is made only after a person runs `release-prepare.yml` and merges the bump pull request
(ADR-0035). Nothing made anyone run it, and it was last run on 9 Oct. By 8 Nov production had been promoted
twelve times and still read `v0.1.0`, 325 commits later, and the change records were named
`2026-10-31-v0.1.0-2.md` because two promotions on one day shared a version.

There was a second hole behind the first. Only `fix` and `feat` commits moved a version. A library update
(`chore(deps)`) changes the bytes of every image and moved nothing, so two different builds could carry the
same version. A version that does not identify one build is not a version.

Roshan decided on 2026-11-08 that every release is tagged ("yes every release should be properly tagged").

## Decision

**1. A change to a file an image carries always moves a version, a patch at least.** `versions.py` decides
which files an image carries (`is_image_file`): everything under a component's `kaval_<svc>/`, the shared code,
migrations, policy, `pyproject.toml`, `uv.lock`, the Dockerfiles and the backup scripts. A `feat` is still a
minor and a breaking change still moves the minor while a version is 0.x. **Tests do not count** (the
Dockerfiles copy only `kaval_<svc>/`, so a test change changes no bytes), **and neither do the release
robot's own `chore(release): ...` commits**: without that exception the version bump itself (it edits
`pyproject.toml`) would ask for the next bump, forever. A rehearsal on this repository's real history
(Lab 83) shows the plan going from `0.1.0 -> 0.2.0` to "nothing has changed that moves a version" after the
bump and the tags.

**2. `promote.py verify` refuses a build that is not a tagged release.** New check, "build is a tagged
release", passes only when all of these hold, and each refusal says what to do:

| Condition | Why |
|---|---|
| The build's commit is on `main` | A release comes from `main`'s history |
| The commit carries an **annotated** `vX.Y.Z` tag | `release.yml` makes annotated tags with the bill of materials in the message; a bare tag is how someone would fake one |
| The tag equals the product version written in the code at that commit | The label inside the image says the same as the tag |
| That version is newer than the version in the code at the commit prod runs | Two different builds can no longer share a version, and a promotion cannot silently be a rollback |

`promote.yml`'s `propose` job already `needs: gate`, so nothing reaches the pin without this.

**3. The staging smoke test refuses to record a pass for an untagged build** (`staging_smoke.py`, same
check). A pass for a build that cannot be promoted is a staging session wasted, and a "passed" record that
promotes nothing is clutter. With `--no-record` it still reports the failure but exits 0, because that mode
only judges.

**4. The staging pull request says what the build is.** `release.yml` writes "This build is release vX.Y.Z"
or "This build is NOT a release (no version tag)" with the steps to make one, using the new
`versions.py release-of`.

**5. The routine for a release** (runbook `docs/runbooks/release-a-version.md`): merge the changes; run
`release-prepare` (Actions tab, on `main`); approve its checks and merge its pull request; `release.yml` builds
the images with the new version on them and tags the commit; merge the bot's staging pin; staging smoke test;
promote. **One extra pull request and one extra "Approve and run" click per release; $0** (GitHub's free
tier builds it).

**6. Nothing is tagged after the fact.** `sha-ba87981` (what production runs on 2026-11-08) carries
`0.1.0` inside its images; calling it `v0.2.0` would be false, so production reads `v0.1.0` until the next
promotion, which will be `v0.2.0` (all five components moved). The twelve earlier change records keep their
names. The `-2` suffix in `change_record.py` stays as a fallback and should now never fire.

**7. Recommended, not built here: a GitHub tag rule** that stops `v*` tags being moved or deleted. Tags are
the evidence, and a repository setting is outward-facing, so it waits for Roshan's own go.

## Alternatives considered

| Option | Why not |
|---|---|
| **Tag at promotion time** | The image would say `0.1.0` inside and the tag `0.2.0`: the running service reports its version from the code (`--version`, metrics), so they must match. ADR-0035 rejected this and still stands |
| **Work the version out at build time and pass it as a build argument** | Source and image would disagree again; the version in the code is what the services report |
| **Release on every merge (semantic-release style)** | Needs a robot that writes to `main`, which branch protection forbids; every Dependabot merge would also publish a release |
| **A standing release pull request kept up to date by the bot (release-please style)** | The right upgrade if the manual `release-prepare` is still forgotten; more code and a rewritten flow than a 3-point story. Tag names are the same, so nothing here is thrown away |
| **Gate only at promotion** | Catches it, but after a staging session has been spent; hence decisions 3 and 4 |
| **Tests also bump a version** | Would publish a new release for a change that alters no image byte |

## Consequences

**Easier.** Every promotion is a version nobody has used; "which build is v0.2.0" has one answer (tag,
label, record, image); the `-2` records stop; the usual cause of an untagged promotion (a step nobody ran) is
now a plain refusal naming the step.

**Harder / limits.**

- One extra pull request and approval click per release, and one extra release build before staging.
- A library update (Dependabot) now needs a release round before it can be promoted. That is the point.
- `guard` in CI (the hand-edit check on prod's files) still checks only the passed-staging record; the
  record can only be written by the staging smoke test, which now refuses untagged builds, and the gate
  refuses them again. A hand-written record is already a different, louder problem.
- A rollback is unaffected: it goes back to a version prod ran, which was tagged or is grandfathered.
- The first live run of the tag step with a new version is the proof (Lab 83, Part 3). It happened on
  2026-11-08: `v0.2.0` and the five per-part tags were made, and the staging smoke test accepted the build
  as a tagged release. The refusal at the real gate and the promotion are still to prove.

**Revisit when** `release-prepare` is forgotten again despite the refusals (then the standing release
pull request), or a second person releases and "who runs it" needs an owner.
