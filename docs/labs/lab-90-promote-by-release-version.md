# Lab 90 — Promote by release version (`v0.4.2`), not by commit code

**Phase:** 8 (hardening) · **Story:** `KAV-136` · **Time:** about 20 minutes for Parts 1 to 3; Part 4 happens at the next real promotion · **Cost:** $0 (Parts 1 to 3 only read Git and the container registry)

Promoting a build to production used to need its image tag (`sha-d1058eb`), which you had to work out from the version you actually know (`v0.4.2`). Now the workflow does that lookup and refuses
clearly when it cannot ([ADR-0088](../adr/0088-promote-takes-a-release-version-and-works-out-the-image-tag.md)). Nothing about the safety checks changed.

## Prerequisites

- The repository checked out with its tags (`git fetch --tags`), `make sync`
- For Part 2 only: AWS access (`AWS_PROFILE=kaval AWS_REGION=ap-south-1`); it makes read-only registry calls

## Part 1 — The old way, for contrast

```bash
git rev-list -n 1 v0.4.2        # the commit the version points at
# take its first 7 characters -> sha-d1058eb, then: -f tag=sha-d1058eb
```

## Part 2 — The new lookup, run by hand

```bash
python scripts/release/promote.py resolve --ref v0.4.2        # sha-d1058eb on stdout; the explanation on stderr
python scripts/release/promote.py resolve --ref sha-14311a9   # an image tag passes straight through
python scripts/release/promote.py resolve --ref v9.9.9        # refused: not a release this repository has (exit 1)
python scripts/release/promote.py resolve --ref latest        # refused: neither a release nor an image tag (exit 1)
```

What you should see for the first: `release v0.4.2 -> commit d1058eb -> image tag sha-d1058eb (all four images found)`. Standard output carries only the tag, because the workflow reads it as a step output.

## Part 3 — The tests

```bash
python -m pytest scripts/release/test_promote.py -q     # includes the lookup, both refusals, and the odd inputs
```

The cases that matter: a release becomes its commit's image tag; a `sha-` tag still works and asks nobody; an unknown release is refused; a release whose images are gone is refused and says nothing was opened;
junk like `latest`, `0.4.2` or `v0.4.2; rm -rf /` is refused before Git or the registry is asked; a lightweight (hand-made) tag is not a release; the pull request text names the version beside the image tag; the
workflow reads the typed text only through the environment.

## Part 4 — The live proof (the next real promotion, on Roshan's own go)

1. Actions → **promote** → Run workflow on `main`, with `tag` = the release, for example `v0.5.0` (after it has passed staging).
2. The `gate` job's first step prints the resolution line; the evidence (and so the pull request text) begins with it. The pull request is titled with the image tag and says `release v0.5.0` in its first line.
3. **Negative, live** (optional, costs nothing): run it once with `tag=v9.9.9`. The `gate` job must fail at its first step with "not a release this repository has", and no branch or pull request may appear.

## What to take away

The human name (`v0.4.2`) is looked up once, at the front door, and the rest of the pipeline keeps using the commit-based tag that staging tested. The lookup can only make a promotion fail earlier; it cannot make
one succeed that the gate would refuse.
