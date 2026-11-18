# ADR-0104 — A pull request shows its Terraform plan, through a read-only role that only a pull request can use

- **Status:** Accepted 2026-11-18; built, applied and proven live the same day (see "Measured")
- **Date:** 2026-11-18
- **Deciders:** Roshan
- **Story:** `KAV-89` (Phase 8) · follows [ADR-0058](0058-terraforms-record-lives-in-a-private-versioned-bucket.md) (the record in a bucket; it named this story) · kept in v1 by [ADR-0099](0099-roadmap-clean-up-what-v1-still-needs.md) · the role pattern is [ADR-0049](0049-backups-watched-and-restores-drilled.md)'s (OIDC, no stored key) · public-log rules from [ADR-0091](0091-the-account-number-is-hidden-in-public-logs-and-role-addresses-are-secrets.md)

## Context

Until now an infrastructure change was judged by `terraform validate` in CI (does it parse?) and by a plan run
on the laptop after the merge. Nobody saw what a change would actually do (add, change, replace, destroy) while
the pull request was still open. Real pipelines show that on the pull request.

Three facts make it harder than it sounds here:

1. **Reading the record means reading secrets.** Terraform's record holds the generated database passwords and
   the Slack tokens (ADR-0058). A "read-only" role on it can read those.
2. **The repository is public.** A job's log and its summary page are public. A plan's own text names the
   account number inside every ARN, the state bucket and the alert email.
3. **Production's settings are not in git.** The alert email, the server key and the budget numbers sit in a
   gitignored file. A plan without them shows false changes.

## What real companies do

| Pattern | One line | Fits here? |
|---|---|---|
| Plan on the pull request with a read-only cloud role (GitHub Actions or Atlantis) | The usual one: the role is tied to the pull request trigger and can only read | **Yes** |
| A hosted platform (Terraform Cloud, Spacelift) | A vendor runs plans and applies | No: a vendor and a recurring bill for one person |
| Plan only on the laptop | What we had | No: that is the gap this story closes |

## Decision

1. **A new role, `kaval-ci-plan`, in the `state` folder** (with the bucket and the analyzer, so it outlives
   production's parking and staging's destruction). Trust: this repository's `pull_request` trigger only
   (`repo:<repository>:pull_request`), `StringEquals`, no wildcard. Not `main`, not a branch push, not a tag.
2. **It can only read:** list the state bucket; read the two records it plans (prod's and the state folder's),
   nothing else in the bucket; read one Parameter Store folder (`/kaval/plan/`) and the bucket's name
   parameter; read the public Amazon Linux image name; describe the GitHub sign-in provider (and list the providers, to find it) and the subnets. It
   cannot write the record (no `PutObject`, so no lock file either), cannot reach the backups, and has no
   action that changes anything.
3. **The plan is made with `-refresh=false -lock=false`.** It compares the code with the record. It does not
   look at the live account, which is why the role needs no wide `Describe` list. A change made by hand in the
   console is not shown; catching that is a separate nightly check (filed as its own story).
4. **Forks and Dependabot cannot use it.** A fork's pull request gets no token and no secret from GitHub; the
   job also refuses it by name. The role's address is a repository **secret**, which Dependabot's jobs cannot
   read, so a bumped action can never run here with the role.
5. **Nothing from the plan's own text reaches the log.** `terraform plan` writes to a file; `terraform show
   -json` feeds `scripts/ops/plan_summary.py`, which prints each changed resource's address, the action, and
   for a change only the *names* of the settings that differ. Never a value. On top of that every twelve-digit
   number is replaced and the bucket name and the alert email are masked. If a step fails, only the first
   lines of its message are printed, with the same masking.
6. **What the pull request adds is told apart from what is already pending.** The first live run (2026-11-18)
   showed 4 changes on production that a laptop plan, made the same way, did not: GitHub's machine builds a
   Lambda's zip file a little differently from Windows, so its fingerprint differs on every plan, and the
   scheduler policy that names the function follows. Left alone, every pull request would show four false
   changes. So the job also plans the pull request's **base commit** (`main`'s code) against the same record,
   and `plan_summary.py` shows as the pull request's own only the rows the base plan does not have; the rest
   are listed apart as "already pending without this pull request". It also cancels a change merged but not
   yet applied. If `main` cannot be planned, every row is shown and a warning says why.
7. **Production's private settings** live in one Parameter Store entry, `/kaval/plan/prod-tfvars`, written
   by the owner by hand (the role cannot write it). The plan assumes production is parked
   (`app_node_desired_capacity = 0`) and says so in its summary.
8. **Production and the state folder only.** Staging is destroyed between sessions; its plan would say "all
   new", which tells nobody anything (its code is still validated by the existing check).
9. **Not a required check.** A broken AWS, an unset secret or a changed API must never block a merge. The job
   goes red on a real failure and says "not run" when the secret is missing. `terraform validate` stays the
   required guard.
10. **One test file keeps it this way** (`scripts/dev/test_terraform_plan_workflow.py`): the trust is exactly
   one `pull_request` sub with no wildcard; the policy allows only the six read actions; the workflow uses
   `pull_request`, never `pull_request_target`, an empty workflow token, a minimal job token, the secret,
   `-refresh=false`, `-lock=false`, pinned actions, no apply. Each rule has a broken copy that must fail.

## Security

- **What it can read:** Terraform's record of prod and state, which holds passwords and tokens. That is the
  risk this decision accepts and narrows: only a pull request of this repository can assume the role, from a
  job that the repository owner's own pull request starts, and the job prints no value.
- **What it can never do:** change the record, take the lock, read the backups or any other bucket, change any
  resource. If its trust were widened to `main` or `*`, `test_terraform_plan_workflow.py` fails.
- **What a hostile pull request could do:** anyone who can push a branch to this repository could edit the
  workflow and print the record. Today that is one person. The role's address cannot be read by a fork or by
  Dependabot. If a second person is ever given write access, add a GitHub environment with a required reviewer
  before the role is trusted (the sub claim then changes to the environment's) and revisit this ADR.
- **Why not a GitHub environment now:** [ADR-0099](0099-roadmap-clean-up-what-v1-still-needs.md) rejected
  required reviewers for the same reason: with one maintainer it adds a click, not a second person.

## Failure and rollback

- **AWS or the role is unavailable:** the plan check goes red with the first lines of the error. Merging is not
  blocked. Nothing in the account depends on the role.
- **Turn it off:** delete the repository secret `AWS_PLAN_ROLE_ARN` (the job then says "not run" and passes), or
  remove the module block and apply.
- **A missing permission:** found by the first live run (the plan fails at the lookup); one line added to the
  module and applied (IAM only, free).

## Observability

The run's summary page is the product. The role appears as an expected finding in IAM Access Analyzer
(`make iam-review` calls a GitHub-sign-in role with one trust "expected"). No alert: a plan that does not run
shows as a red or "not run" check on the pull request, which is where it is looked at.

## Cost and capacity

**$0.** IAM roles are free; GitHub's minutes are free on a public repository; the S3 reads and Parameter Store
reads are a few requests per pull request (a fraction of a cent a month). Nothing runs on the app node.

## Testing

Unit: `scripts/ops/test_plan_summary.py` (9: names only, a password never printed, account numbers and masked
strings hidden, a pipe cannot break the table, a long plan is cut) and `scripts/dev/test_terraform_plan_workflow.py`
(trust, policy, workflow, each with broken copies). Local: a real plan of the state folder run through the
summary script (2 to add). Live: after the apply, a throwaway pull request with a harmless `infra/` edit shows
the summary; a permission check shows the role can read the record and cannot write it.

## People and process

[Lab 106](../labs/lab-106-the-terraform-plan-on-every-pull-request.md) builds it from zero and lists the two
things only the owner does (the repository secret and the Parameter Store entry). Ownership: Roshan. A plan
check that says "not run" means one of those two is missing.

## What this does not do

- It does not apply anything. Applies stay on the laptop, after a plan is shown.
- It does not catch a change made by hand in the account (a nightly drift check is a separate story).
- It does not plan staging, dev or the EKS lab.
- It is not a second reviewer. On a one-person repository the person reading the table is the person who wrote
  the change; the table makes a mistake visible, it does not make one impossible.

## Measured

2026-11-18, through a throwaway draft pull request (closed without merging):

| Step | Result |
|---|---|
| Apply | role and policy, 2 added; second plan no differences |
| Policy simulator | read of prod's and state's record, the settings and the bucket name: allowed. Write, delete, lock file, staging's record, other bucket, other parameter, write parameter, RunInstances, CreateRole: all implicitly denied |
| First run | login and record read worked; the plan stopped on `iam:ListOpenIDConnectProviders`; one read action added (a list has no resource-level permission), apply 2 = 1 changed |
| Second run | production showed 4 changes the laptop plan did not (a Lambda zip builds differently on GitHub's machine); decision 6 follows |
| Third run | production: "adds no change", 4 listed as already pending; state: only the throwaway edit; log checks: 0 twelve-digit numbers, 0 bucket names, 0 lines of plan text |
| Time | production 64 s, state 47 s (two plans each) |

