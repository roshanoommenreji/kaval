# Lab 94 — The promotion gate checks the signature

**Phase:** 8 (hardening) · **Story:** `KAV-139` · **Decision:** [ADR-0092](../adr/0092-promotion-refuses-an-image-whose-signature-does-not-verify.md) · **Cost:** $0 · **Time:** 30 minutes (Parts 1-4), one workflow run for Part 5

## What you will build

Before `promote.yml` opens a pull request that points production at a release, it asks the registry and GitHub: does each of the four images carry a signature, a build record and a parts list from **this repository's
release workflow on `main`**? If any image fails any of the three, the promotion stops, nothing is opened, and the log says which image failed which check. No new AWS permission, no Terraform.

## Part 1 — Read the change

In `scripts/release/promote.py`:

| Piece | What it does |
|---|---|
| `SIGNER_IDENTITY`, `SIGNER_ISSUER`, `SIGNER_WORKFLOW`, `SIGNER_REF` | who may have signed, written out in full. Never a pattern |
| `cosign_command(ref)` | `cosign verify <image>@<digest> --certificate-identity … --certificate-oidc-issuer …` |
| `record_command(ref, kind)` | `gh attestation verify oci://… --repo … --signer-workflow … --source-ref refs/heads/main --predicate-type …` for the build record and the parts list |
| `check_signatures(digests, registry)` | runs the three questions over the four images; runs all of them even after a failure; a failure's reason is shown with the registry address hidden |
| `registry_from_env()` | the registry address comes from the login step; none means "cannot decide" |
| `cmd_signatures` | `promote.py signatures --tag T`: only this question, read-only |
| `cmd_verify` | now also calls `check_signatures` on the digests that passed staging, so the report that decides the promotion includes it |

In `.github/workflows/promote.yml`, job `gate`: **install cosign (checksum-verified)** comes before the AWS login (as in `release.yml`), an `amazon-ecr-login` step comes after it, and the `verify` step receives `REGISTRY`,
`COSIGN` and `GH_TOKEN`. The gate still cannot write to the repository; `propose` still cannot reach AWS.

## Part 2 — The tests

```bash
python -m pytest scripts/release/test_promote_signatures.py scripts/dev/test_workflow_promote_signature.py -q
```

The first file runs the **real command** against one small stand-in program in place of `cosign` and `gh`. The stand-in says yes only to the exact signer, branch and record kinds it was told about, for the digests it was told are signed:

- all four images signed → three passes; every call names the full signer and no pattern; the digest is what is checked;
- **negative:** one image unsigned → refused, and only that image is named; signed by `ci.yml` instead of `release.yml` → refused; signed on another branch → refused; no parts list stored → only that check fails;
- **negative:** a refusal never shows the registry address or the account number (the stand-in's error text contains them on purpose; the report shows `***`);
- the tool missing, no registry address, or an address that is not an ECR address (it would be passed to a tool otherwise) → "cannot decide" (exit 2) and no tool is run.

The second file reads `promote.yml` (cosign installed first with a checksum, the right architecture, the registry login in place, the gate cannot write, `propose` cannot reach AWS) and runs the same check on **made-up copies
that break each rule**. It also pins that the signer in `promote.py` is word for word the one `release.yml` signs with.

## Part 3 — Make sure the tests can fail

Break the gate on purpose and run the tests again; they must go red:

1. in `check_signatures` change `if code != 0:` to `if False:` → six tests fail;
2. in `cosign_command` replace the identity with `"--certificate-identity-regexp", ".*"` → five tests fail.

(Both were done on 2026-11-13 and reverted.)

## Part 4 — Ask the question by hand (read-only, from the laptop)

This needs your AWS login, `cosign` (the version in `COSIGN_VERSION`), `gh` logged in, and a registry login. The registry address contains the account number, so build it in the shell and do not paste it anywhere:

```bash
export AWS_PROFILE=kaval AWS_REGION=ap-south-1
export REGISTRY="$(aws sts get-caller-identity --query Account --output text).dkr.ecr.ap-south-1.amazonaws.com"
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"
export COSIGN=/path/to/cosign          # if it is not on your PATH
python scripts/release/promote.py signatures --tag sha-152b59e
python scripts/release/promote.py signatures --tag sha-791c44d
```

Result on 2026-11-13: `sha-152b59e` (the release that first carried signatures) printed three `PASS` lines and "carries valid signatures", exit 0. `sha-791c44d` (older) printed three `FAIL` lines (`no signatures found`, and two `HTTP 404`
from GitHub's store) and "does NOT carry valid signatures", exit 1.

## Part 5 — The proof inside the workflow (on the owner's typed go)

`promote.yml` runs only on `main`. Note the gate stops at its first question: a tag that never passed staging is refused there, before any signature is looked at. So the two halves of the proof are different:

1. **Refusal now (read-only, opens nothing):** dispatch with `tag=sha-791c44d`. It is on the passed-staging record and older than prod, so the gate gets as far as the signature checks. Expect the **verify** step to fail and its report to show
   three `FAIL` signature lines (alongside the "newer than prod" refusal), and no pull request. Confirm the log shows `***` and not the account number (`gh run view <id> --log | grep -c <the number>` must print 0; type the number into
   the shell yourself, do not save it).
2. **Pass, at the first real promotion of a signed release:** the next release is signed, so after a release round and a staging smoke pass its promotion's verify step must show three `PASS` signature lines, and the pull request
   body (which quotes the report) must contain no account number. That promotion is also the test that the registry login, the pinned cosign and `gh attestation verify` all work on the real runner.

Result: _to be filled in after each._

## What to take away

A record you have to go and look for protects nobody. The gate is where a person is about to say yes, so that is where the question "who made this image" gets asked, in code, with the answer written out in full. The earlier
stories made the stamp; this one stops the unstamped from going further; the cluster story makes it hold even if someone goes around the gate.
