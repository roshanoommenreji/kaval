# ADR-0096: The frozen list is not matched, so a listed release is never checked

**Status:** Accepted on 2026-11-13 (Roshan: `fix it`, after production's start-up showed the list refusing a listed release). **Built 2026-11-13** and shown on a laptop against Kyverno 1.20.0 itself; **proved live on production the same night** (the merge reached production while it was up; the real pod, the signed, frozen and unsigned checks all behaved, [Lab 99](../labs/lab-99-the-frozen-list-lets-old-releases-through.md) Part 6); **proved live on staging 2026-11-14** (the staging-first rule had been bypassed by merging with production up; the same five checks passed there, Lab 99 Part 5).
**Deciders:** Roshan
**Related:** [ADR-0093](0093-the-cluster-checks-image-signatures-with-kyverno-watching-first.md) (the checker and the frozen list), [ADR-0094](0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md) (refusing and failing closed), [ADR-0095](0095-production-refuses-unsigned-images-from-its-next-start-up.md) (production), [ADR-0034](0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md) (rollback, which the list protects), [Lab 99](../labs/lab-99-the-frozen-list-lets-old-releases-through.md)
**Story:** `KAV-146`

## Context

The cluster refuses any Kaval image that is not signed by `release.yml` on `main`, **except** the 19 releases built before signing existed, so that production and staging (which both pin one of them) can start pods and so that a rollback to one of them works until the list ends on 2026-12-31.

The first design put the list inside the rule: *a signature check passes, or the tag is on the list*. On 2026-11-13, at production's start-up, a throwaway pod from `sha-791c44d` (on the list, unsigned) was **refused**: "failed signature or attestation verification". The checker's own log showed the signature check running and finding no signatures, and then the refusal.

Reproduced for nothing on a laptop (Lab 99): Kyverno 1.20.0's own command-line tool, the real policy file, a real public image that Kaval did not sign. Listed image: refused. Unlisted image: refused with the policy's own message. The two messages differ, and that is the finding: **when a signature check ran and failed, Kyverno refuses the image by itself, whatever the rule says.** The rule can never rescue it, so the list inside the rule could never have worked.

It looked as if it worked, twice, and both were mistakes of mine in what to test with:

- The laptop trial and the live staging session ("a frozen old release is let in", "a real gateway pod, deleted, was recreated") used `sha-d1058eb`. Its gateway image is the **same image** as the signed `sha-152b59e` ([ADR-0095](0095-production-refuses-unsigned-images-from-its-next-start-up.md) found this for the planted name and fixed it there, but not for the frozen test). It passed on its signature, not on the list.
- `sha-791c44d` is a truly unsigned, different image. It was chosen for the new `frozen` step precisely because of that, and it was the first time the list was ever asked to do its job.

## Decision

The list stops being an exception inside the rule and becomes part of **which images the policy looks at**. `matchImageReferences` (Kyverno: "any image that does not match a rule is skipped") now says: *an image in Kaval's registry path, whose tag is not on the list*. A listed release is not matched, so Kyverno never checks it, so there is no failed check to refuse. Every other Kaval image is checked as before.

1. **Where:** `deploy/image-policy/image-validating-policy.yaml`. The glob is replaced by one expression on `ref` (the image as Kyverno holds it). The `frozen` variable and the `|| ...exists` in the rule are gone; the rule is just *the signature from `release.yml` on `main` verifies*.
2. **Both forms of the tag.** Kyverno rewrites `repo:tag` to `repo:tag@sha256:...` before it looks (`mutateDigest`, default on), so the expression matches `:<tag>@` and `:<tag>` at the end of the reference. A tag cannot contain `:`, so this names that tag and nothing else. A reference with no tag at all (a bare digest) is not on the list, so it is checked.
3. **One list, one file, the same shape.** One tag per line, quoted, trailing comma, as before: the tests, the laptop trial script and the runbook's `grep` still read it unchanged. The end date (2026-12-31, a test fails the build after it) and the rule that every tag is on the passed-staging record with nothing from the signed era are unchanged.
4. **Nothing else changes.** Same namespace scope, same signer (exactly `promote.py`'s), `Deny` + `Fail` + 30 s, same alarm rules. Production and staging still get the same files.

Industry practice: an admission checker keeps *which images it applies to* separate from *what it requires of them*, and the usual way to exempt images is to leave them out of what the policy matches. Hiding the exception inside the rule that verifies is the shape that does not work here.

## Alternatives rejected

- **Put the list first in the rule (`listed || verify > 0`).** Tried on the laptop in October's trial (`test_image_policy.py` carried the comment until now): Kyverno then reports "no policy performed a signature check on it" for a listed image, a different refusal. A check has to run on every image the policy matches, and must not run on one it does not.
- **A second policy for the list.** Two policies for one fact, which can drift; the list would also need the same placeholders and a test of its own.
- **Turn the refusal off for listed releases with `validationActions: [Audit]`.** That turns it off for everything.
- **Drop the list and let the cluster refuse old releases.** Production and staging pin listed releases today, so they could not start a pod; a rollback would fail. The list exists for exactly this and ends on a date.
- **Pin Kyverno to something older.** Not a version problem; the behavior is by design.

## Consequences

- **Cost:** $0. No new data, memory or process; one policy file changes.
- **Security:** a listed release is no longer checked at all. That was always the intent of the list (the check on them failed and the rule let them through); now it is true on purpose. What protects it is unchanged: tags are immutable in ECR, the list only shrinks, every entry is on the passed-staging record, none is from the signed era (all tested), and it ends on 2026-12-31. A new entry still needs an ADR line.
- **The alarm:** `UnsignedImageSeen` counts the checker's `fail` results; a listed release now produces no result at all, so it cannot ring it. Same as before for the signed and the unsigned-and-unlisted cases.
- **Tests:** the existing policy tests are changed to the new shape and three new ones go red against the old policy (the list is not in the rule that judges an image; the rule matches the tag in both forms; the match is an expression, not a glob). A real-dependency test runs the actual file through Kyverno's own tool in Docker (`KAVAL_KYVERNO_CLI=1`, skipped in CI because it needs the internet): listed with digest, listed tag only, listed beside an outside image are let in; unlisted, listed beside unlisted, and a bare digest are refused.
- **Proved on production, 2026-11-13:** frozen pass accepted, signed accepted, an unsigned unlisted image refused (and the alarm rang), and a real pod of an unsigned listed image deleted and recreated with no `FailedCreate`. **Proved on staging, 2026-11-14:** the same four results plus the unsigned alarm (Lab 99 Part 5). KAV-143 stays Done; its frozen-list line closed with this story, and the other leftover (the six-hour login refresh) moved to `KAV-147`.
- **The risk this closed:** production pins `sha-d1058eb`. Checked in ECR on 2026-11-13: its gateway and backup images are the same images as the signed `sha-152b59e` (so they pass on their signature), but its **executor, agent and collector images are not signed**. Under the old policy a recreated pod of any of those three would have been refused and would have stayed missing. Pods started in a session start before the checker is ready, which is why nothing went wrong, and production is parked between sessions. The emergency off-switch is in [the runbook](../runbooks/image-flagged-by-the-cluster-check.md).
- **Lesson, to repeat in every test of an exception:** prove it with the one thing the rule would refuse if the exception were absent. A frozen pass is only a pass if the image would fail without the list. `checker_proof.py` already refuses a base whose digest equals the signed one; the laptop trial script picked `sha-d1058eb` by accident.
