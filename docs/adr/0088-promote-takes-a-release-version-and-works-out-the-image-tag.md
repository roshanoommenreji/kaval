# ADR-0088: `promote.yml` takes a release version and works out the image tag

**Status:** Accepted on 2026-11-13 (Roshan: "start next", taking `KAV-136`). Built and tested; the first real use is the next promotion.
**Date:** 2026-11-13
**Related:** [ADR-0081](0081-a-promotion-must-be-a-tagged-release.md) (a promotion must be a tagged release),
[ADR-0032](0032-promote-workflow-and-the-promotion-guard.md) (the promote workflow),
[ADR-0004](0004-environment-strategy-and-promotion.md) (build once, promote the artifact),
[Lab 90](../labs/lab-90-promote-by-release-version.md), `KAV-136`

## Context

Since ADR-0081 a promotion has to be a tagged release, and the person promoting thinks of it by its version: `v0.4.2`. The workflow asked for the image tag instead (`sha-d1058eb`), the first seven
characters of the commit the version points at. Working that out meant a Git command (`git rev-list -n 1 v0.4.2`), and the runbook said so. A typing slip there is caught by the gate, but only
after a run; a human should not have to translate a name the system already knows.

## Options (what real companies do)

1. **Leave it.** Zero risk; the person keeps doing a lookup the machine can do.
2. **Retag the images with the version** (`kaval/gateway:0.4.2`) and promote that. Common, but it adds a second tag to the same digest after the build, and the repository's rule is that the artifact
   is identified by the commit (ADR-0004). Two names for one thing is how "which one did staging test" gets argued about.
3. **Look the version up, then carry on with the image tag.** The usual pattern in release tooling: the human-facing name is resolved to the immutable identifier at the front door, and everything behind the door
   uses the immutable one.

## Decision

**Option 3.** A new first step in the `gate` job, `promote.py resolve --ref <what was typed>`, turns the input into the image tag, and every later step (the five gate checks, the pin, the pull request text, the change record) uses
that tag only. The workflow input keeps its name, `tag`, so the old command lines still work.

- `vX.Y.Z` is looked up as an **annotated** tag (the same rule the gate already applies to release tags: a lightweight tag can be pushed by anyone), peeled to its commit, and becomes `sha-<first 7 characters>`.
- The image tag must **exist in the registry**. Otherwise the job stops with a plain message ("... no image tagged sha-... is in the registry ... Nothing was opened"). This is a read of what the gate already reads later; it only moves
  the refusal earlier, with a better sentence.
- A `sha-...` tag passes through **unchanged and unchecked here**; the gate judges it exactly as before.
- Anything else (`latest`, `main`, `0.4.2`, a typo) is refused before Git or the registry is asked.
- **Both names are on the record.** The resolution line (`release v0.4.2 -> commit d1058eb -> image tag sha-d1058eb`) is the first line of the gate's evidence, so it appears in the pull request text and in the change record's
  evidence block. The pull request text also says `release v0.4.2` beside the image tag. The change record already carries the version in its title.
- The resolved tag is passed from the `gate` job to the `propose` job as a job output. The script prints that tag and nothing else on standard output, after checking its shape, so it cannot carry anything into the workflow.
  The typed text reaches a script only through the environment, as before.

## What did not change

The five gate checks, the "newer than prod" direction rule, the passed-staging record, the guard in CI, the roles of the two jobs (the `gate` job still cannot write; `propose` still cannot reach AWS or Jira) and the
rule that merging the pull request is the go/no-go. `rollback.yml` is untouched: it takes an image tag, and a rollback goes back to a build, not forward to a release.

## Consequences

- `gh workflow run promote.yml --ref main -f tag=v0.4.2` is the whole command. The runbooks say so, and say that `sha-...` still works.
- A release whose images were removed by the registry's retention rule is refused at the front, with the reason, instead of failing a later check with a less direct line.
- No new access, no new cost, no new component. Tested by unit tests and a read-only dry run against the real tags and registry; the workflow's own change is first exercised by the next real promotion.
