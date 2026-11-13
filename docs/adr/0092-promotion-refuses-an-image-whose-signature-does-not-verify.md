# ADR-0092: promotion refuses an image whose signature does not verify

**Status:** Accepted on 2026-11-13 (Roshan: "go 139", the third story of the supply-chain line). Built and tested on 2026-11-13; checked by hand from the laptop against two real releases the same day. The proof inside the workflow itself waits for his typed go and for the next signed release (see Consequences).
**Date:** 2026-11-13
**Related:** [ADR-0090](0090-images-are-signed-keyless-and-the-signature-lives-in-the-registry.md) (the signature this checks), [ADR-0089](0089-every-image-gets-a-parts-list-and-a-signed-build-record.md) (the build record and parts list this checks),
[ADR-0032](0032-promote-workflow-and-the-promotion-guard.md) (the gate this extends), [ADR-0034](0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md) (rollback, left alone, see Decision),
[ADR-0091](0091-the-account-number-is-hidden-in-public-logs-and-role-addresses-are-secrets.md) (why the logs here are safe to be public), [threat model](../architecture/threat-model.md) T-SUPPLY-2,
[Lab 94](../labs/lab-94-the-promotion-gate-checks-the-signature.md), [runbook](../runbooks/verify-an-image.md), `KAV-139` (this story), `KAV-140` (the cluster refuses unsigned images)

## Context

After `KAV-138` every image is signed in the registry, and has a signed build record and parts list in GitHub's store, but **nothing looks at them before an image reaches production**. The promotion
gate asked four questions (is it on the passed-staging record, do ECR's digests still match, is it newer than prod, is every `uat` story signed off) and none of them was "who made this image".

## Options (what real companies do)

1. **Check only in the cluster** (an admission policy). The strongest single place, because nothing can go around it. But the failure shows up late: after the pin is merged, when pods fail to start.
2. **Check only at the promotion gate.** Fails early and where a person is deciding. Does not catch a hand edit of the pin or an image replaced later.
3. **Both** (the usual answer): fail early at the gate so nobody merges a bad pin, and keep the cluster check as the last line.

## Decision

**Option 3, in two stories. This one is the gate.** The cluster is `KAV-140`.

- **What is asked, per image (all four of them):** (a) does a cosign signature on this exact digest verify against the one allowed signer; (b) does the signed **build record** verify; (c) does the signed **parts list**
  verify. (b) and (c) use `gh attestation verify`, which checks the signature, the repository, the workflow and the branch, not just that a record exists (release.yml only proves they exist).
- **The one allowed signer, written out in full, never a pattern:** `https://github.com/roshanoommenreji/kaval/.github/workflows/release.yml@refs/heads/main`, issued by `https://token.actions.githubusercontent.com`.
  For the records the same thing as repository + workflow file + branch. A signature from another workflow, branch, fork or pull request does not verify. `promote.py` holds these as constants; a test fails if they
  ever differ from the ones `release.yml` signs with.
- **Where:** in the `gate` job of `promote.yml`, inside `promote.py verify`, as part of the same report. If any image fails any check the job fails, `propose` never starts, no branch and no pull request are made.
  The checks run on the digests that **passed staging** (from the record), and the ECR check just before them already refuses a digest that has since changed.
- **Fails closed.** A missing tool, an unreachable Sigstore or GitHub, a hang past three minutes, or no registry address all end as "cannot decide" (exit 2), and the workflow fails. There is **no flag to skip the check**.
- **The signing tool:** cosign `3.1.4`, the same as `release.yml`, fetched at that fixed version and refused if its SHA-256 differs. The gate runs on an x64 machine (release's is arm64), so it has its own checksum:
  the `linux-amd64` line of the release's own checksum file, also confirmed by hashing the download. Installed before any AWS access, as in `release.yml`. The download retries like every other one (`KAV-133`; its test now counts eight).
- **No new AWS permission.** The publish role gained `ecr:BatchGetImage` and `ecr:GetDownloadUrlForLayer` in `KAV-138`; reading signatures needs only those, plus the registry login the role already had. No Terraform change.
- **The public log stays clean.** The checks run with their output captured. The report shows the check, the service and a **scrubbed** last line of a failure (the registry address and the account number inside it are replaced
  with `***`; the log also masks the number by itself, `KAV-141`). The pull-request text quotes the same report, so a test also proves a refusal never contains either.
- **A read-only command for the same question:** `promote.py signatures --tag T`. It is how a person (and the runbook) asks it by hand, and how the live proof can ask it without opening a pull request.
- **Rollback is not gated.** `rollback.yml` goes back only to a version production already ran or that passed staging ([ADR-0034](0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md)). Production today runs images built before
  signing existed (they carry no signature, by design), so gating rollback now would block going back to a version known to work, in the moment speed matters. The price, said plainly: a rollback can restore an
  unsigned image. `KAV-140` has to decide how the cluster treats such an image (an exception list for the versions that predate signing, with an end date) before it enforces anything.
- **Not checked in the CI `promotion-guard`.** That job has no AWS access by design. It still blocks a hand edit of prod's tags that skips the record; the cluster check is what will catch an image that skipped signing.
- **`propose` does not check again.** It has no AWS access and cannot. The gate job must pass first (`needs: gate`), and `pin` still re-reads the record on its own.

## Consequences

- **Cost:** $0. About a minute more on the gate job (12 short checks). No new permission, no new storage.
- **Every release already on the passed-staging record is refused** (they were built before signing, on or before 2026-11-12). That is the point, but it means **the next real promotion needs a new release and a staging
  round first**. `v0.4.2`, which production runs, stays as it is; nothing is rolled back or changed.
- **Failure and off switch:** if Sigstore or GitHub's store is down, promotion waits (re-run later); production is untouched because the gate never writes. Turning the check off means removing it from `cmd_verify` in a reviewed pull
  request. There is no runtime switch, on purpose: a switch is the first thing a mistake or an attacker would reach for.
- **Honest limits:** the check says "this digest was published by `release.yml` on `main` as it stands at promotion time". Someone who can change `release.yml` on `main` changes what is signed (branch protection and required checks are
  the guard, T-SUPPLY-3, accepted, unchanged). A signature proves where an image came from, not that it is free of faults; Trivy at release and staging at promotion do that job.
- **Proof so far:** unit tests run the real command against a stand-in checking tool and show: all signed passes; one unsigned image, another workflow, another branch, a missing parts list, a missing tool and a bad registry address
  are each refused or "cannot decide", with the account number never shown; two deliberate breakages of the gate were caught by the tests. By hand from the laptop (read-only): release `sha-152b59e` (signed) passes all three checks
  for all four images; `sha-791c44d` (older, unsigned) is refused on all three. **Done on his typed go (2026-11-13, run 43369948100):** `promote.yml` itself on `main` with `tag=sha-791c44d` (on the record, older than prod, unsigned) failed at verify on all three signature checks for all four images, opened nothing, and the log held the account number 0 times. **Still to do:** the first real promotion of a signed release (the next release plus a staging pass) is the pass case on the real runner. The gate stops at its first question, so a signed tag that never passed staging cannot show the signature checks passing in the workflow.
