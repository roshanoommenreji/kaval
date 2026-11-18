# Lab 105 — A watcher for what outsiders can reach

**Phase:** 8 (hardening) · **Story:** `KAV-160` · **Decision:** [ADR-0101](../adr/0101-the-aws-access-review-and-what-it-changes.md) (change 1; finding A-1 of the [access review](../architecture/access-review.md)) · **Status:** applied to the account 2026-11-18; first reading: three findings, all expected (Part 4).

## What you will build

One AWS service switched on: **IAM Access Analyzer**, the *external access* kind, for the whole account in `ap-south-1`. It looks at everything in the account that can be shared (a storage bucket, a role, a key, a queue, a secret, a container registry) and lists each one that an outsider could reach: someone on the public internet, or another AWS account. Each such case is a *finding*.

You need the repository, Terraform 1.11 or newer, and the owner's AWS login. Nothing here changes any server, role or bucket: it adds a watcher and nothing else.

## Why

The access review of 2026-11-16 ([Lab 101](lab-101-the-aws-half-of-the-access-review.md)) read the account and found no analyzer (finding A-1). That means a role or bucket opened to an outside account by mistake would not be noticed by anything. The review's own text says the outside-access part of it "cannot be done until it is switched on".

What real companies do:

- **One analyzer per account, per region, on from day one** (the AWS security baseline). Cheap, quiet, and the first thing an auditor asks to see.
- **An organisation-wide analyzer in a central security account.** The same thing, but it needs AWS Organizations and a second account; Kaval has one account.
- **The paid kinds as well** (unused access, internal access): they find permissions nobody uses and who inside the account can reach a resource. Useful at scale; priced per role and per resource, so not here.

Chosen: the first, the free kind only.

## Part 1 — Look before you change (read-only)

```bash
make iam-review          # prints "ACCESS ANALYZER: none exists in ap-south-1" before this lab
```

## Part 2 — The change

In `infra/envs/state/main.tf`:

```hcl
resource "aws_accessanalyzer_analyzer" "account" {
  analyzer_name = "kaval-account"
  type          = "ACCOUNT"
}
```

Three choices worth knowing:

- **`type = "ACCOUNT"`** is the free kind (external access). AWS charges for `ACCOUNT_UNUSED_ACCESS` (per role and user) and `ACCOUNT_INTERNAL_ACCESS` (per resource). One word separates free from a monthly bill, so `scripts/dev/test_access_analyzer.py` reads every Terraform file and fails if any analyzer has another type. Its checker is also run against broken input, so a checker that stopped working would fail loudly.
- **The `state` folder, not staging.** Staging is destroyed at the end of every session. An analyzer inside it would vanish each time and keep no history of findings. The `state` folder is the one place that is never destroyed.
- **One region.** Every Kaval resource is in `ap-south-1` ([ADR-0003](../adr/0003-aws-region.md)). The review checked `us-east-1` too and found nothing there either.

The same change makes `make iam-review` list each active finding and sort it into `expected` or `NEW` (Part 4 says what counts as expected).

Run the tests: `python -m pytest scripts/dev/test_access_analyzer.py scripts/ops/test_iam_review.py` (no AWS needed).

## Part 3 — Apply it (the owner's typed go, plan shown first)

```bash
scripts/ops/tf-init.sh state
cd infra/envs/state
terraform plan -out=kav160.tfplan     # Plan: 1 to add, 0 to change, 0 to destroy
terraform apply kav160.tfplan         # on the owner's go
terraform plan -detailed-exitcode     # exit 0: no drift
```

There is no staging step: the analyzer is account-wide and reads nothing of any environment's data, only the access rules of what exists. This was decided in ADR-0101 before it was built.

Undo: `terraform destroy -target=aws_accessanalyzer_analyzer.account` (nothing depends on it).

## Part 4 — Live record (2026-11-18)

- Applied 12:02 IST on the owner's `apply state`: **1 added, 0 changed, 0 destroyed**; a second plan said no differences.
- `make iam-review` now prints `kaval-account (ACCOUNT, ACTIVE)`.
- **First reading, 12:07 IST (five minutes after it was switched on): 3 active findings, all of one kind.** Each is a role that GitHub's sign-in service may assume, which the analyzer counts as outside access because GitHub is outside the account: `kaval-ci-publish`, `kaval-backup-watch` (both Kaval's) and `stock-trader-github` (the other project's, only read, never changed). Their own trust rules were read by hand: each accepts one thing only, the sign-in token of one repository on one branch (`main` for Kaval's two, `master` for the other project's) for the audience `sts.amazonaws.com`. That is exactly what the access review said in November ("the two roles GitHub uses can only be taken by this repository, on `main`"). Nothing is public, nothing is shared with another AWS account, no bucket, key, queue, secret or registry was flagged.
- **So the analyzer works and the account is as the review described.** The three are *accepted with a reason*, not hidden: the analyzer keeps listing them, and `make iam-review` now prints each active finding as `expected` (an exact match to the GitHub-sign-in shape: not public, one action, one principal, GitHub's provider) or `NEW` (anything else). A fourth finding, or one of these changing shape (made public, shared with another account), prints `NEW` and is a decision for the owner. `scripts/ops/test_iam_review.py` has six cases for this, including each way a finding must be `NEW`.
- **Not done on purpose:** AWS's *archive rules* (which hide expected findings inside AWS). They would need the role names in the Terraform and add a thing to keep in step; the script's rule is in git and tested instead. Revisit if the list of expected findings grows past a handful.
- **Honest limit:** the analyzer looks at the *rules* of access, not at who has actually used them; it does not say the GitHub roles are safe, only that nothing else is open.

## Failure, observability and cost

- **If it is wrong or down:** nothing running depends on it; the only loss is visibility. It is switched off by destroying the one resource.
- **How we notice:** `make iam-review` lists the analyzer and each active finding, `expected` or `NEW`, every time it is run (the monthly access check, [ADR-0100](../adr/0100-secrets-are-changed-yearly-and-the-laptop-key-every-90-days.md)). A `NEW` finding is a decision for the owner, not an automatic action; AWS also keeps the finding in its console.
- **Cost:** $0 a month. Checked on AWS's pricing page on 2026-11-18: external-access analysis has no charge.
- **Security:** it is read-only by nature (it analyses policies, it does not touch data). The login that created it is the owner's own, as for every other change in the `state` folder.
