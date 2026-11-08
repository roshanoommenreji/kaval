# Release a version (so production can be promoted)

Production only takes a build that is a tagged release ([ADR-0081](../adr/0081-a-promotion-must-be-a-tagged-release.md)).
`promote.yml` refuses anything else with the line "build is a tagged release: no version tag on the build's
commit". This is the routine that makes a release. It touches no AWS; every step is a pull request or a
workflow button, and merging is always a person's act.

## Signals

- `promote.yml` fails at the gate with "build is a tagged release" FAIL
- `make staging-smoke` passes but prints "Nothing recorded ... Tag it first"
- The bot's staging pull request says "This build is NOT a release"
- `make version-plan` shows a next version (something has changed since the last release)

## Likely causes

1. Nobody ran `release-prepare` after the last changes were merged (most often)
2. A library update (Dependabot) or other image change merged after the last release
3. The bump pull request is merged but `release.yml` has not finished, so the tag does not exist yet
4. The tag step failed after the images were pushed (rare; re-running is safe)

## Diagnosis

1. `make version-plan`: what the next version would be and which commits move it. "Nothing has changed that
   moves a version" means the last release is current.
2. `python scripts/release/versions.py release-of --commit <commit of the build>`: prints the version tag on
   that commit, or nothing.
3. Actions tab, `release`: did the last run finish, and did its "tag the versions" step print `tagged`?

## Remediation

Reversible: every step is a pull request. Blast radius: none until the promotion pull request is merged.

1. Make sure the changes you want are merged and `main` is green.
2. Actions tab → **release-prepare** → Run workflow (on `main`). It opens `chore(release): Kaval X.Y.Z`.
   It says "Nothing has changed that moves a version; no pull request" when there is nothing to release.
3. Open the pull request, **Approve and run** its checks (the deliberate human step, ADR-0030), and merge it
   when green.
4. `release.yml` starts on the merge: it builds the five images with `X.Y.Z` inside, tags
   `<svc>-vX.Y.Z` and `vX.Y.Z` at that commit, and opens the staging pin pull request. Its text must say
   "This build is release vX.Y.Z". Approve its checks and merge it.
5. Test it on staging (`make staging-up`, then `make staging-smoke`; a pass is recorded). Then
   `promote.yml` with `tag=sha-<7 characters of the commit that carries the tag>`.

If step 4's text says NOT a release, the build came from a commit after the tag: use the build whose commit
the tag points at (`git rev-list -n 1 vX.Y.Z`, first 7 characters).

If the tag step failed: re-run the `release` run on the same commit (Actions → Re-run failed jobs). It only
creates tags that do not exist.

## Do not

- Do not create a `v*` tag by hand to get past the gate: the gate wants an annotated tag that matches the
  version written in the code, and a hand-made one will be refused or, worse, mislabel a build.
- Do not tag `sha-ba87981` or any build from before the first tagged release after the fact; it carries
  `0.1.0` inside ([ADR-0081](../adr/0081-a-promotion-must-be-a-tagged-release.md), decision 6).
- Do not edit `__version__` by hand; `release-prepare` computes it from the commits.
