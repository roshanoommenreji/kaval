# Lab 103 — A ceiling over the two roles that act

**Phase:** 8 (hardening) · **Story:** `KAV-161` · **Findings:** A-2, A-3, A-5 in [access-review.md](../architecture/access-review.md) · **Decision:** [ADR-0101](../adr/0101-the-aws-access-review-and-what-it-changes.md) · **Cost:** $0 (a permission boundary is a policy document; the simulator is free)

## What you will build

Three changes to two AWS logins, and a set of tests that keeps them true:

1. The budget brake may only shrink server groups that carry Kaval's label.
2. A **permission boundary** on the brake and on the waste remover: a second policy that is a ceiling. The role may do only what its own policy *and* the ceiling both allow.
3. The remover may never delete a snapshot labelled for another environment.

You need the repository, the project's Python environment (`make sync`) and Terraform to validate. The simulator step also needs the laptop's AWS login (read-only).

## Why

The access review found five things. Three are these: the brake's policy named no group at all, so it could scale down any group in the account (A-2); no role had a ceiling, so a later mistaken edit could widen the remover or the brake without anything stopping it (A-3); and the remover's snapshot delete checked the project label but not the environment label (A-5).

Real teams put a boundary on roles that other people can edit or that act on the account. Here one person's Terraform makes every role, so a boundary on all thirteen would be more to keep right for no gain. Only two roles change anything (the remover and the brake), so only those two get one ([ADR-0101](../adr/0101-the-aws-access-review-and-what-it-changes.md) decision 3).

## Part 1 — Limit the brake to Kaval's groups

`infra/modules/budget/main.tf` had one statement allowing look, update and set-size on every group (`resources = ["*"]`). It is now two:

- `FindGroups`: `autoscaling:DescribeAutoScalingGroups` on `*`. Looking cannot be narrowed (the list call has no one resource to name).
- `ScaleKavalGroupsToZero`: update and set-size only on a group carrying `Project=kaval` (condition key `autoscaling:ResourceTag/Project`, which AWS lists for both actions in its [service authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonec2autoscaling.html)).

Both server groups carry that label: the node module writes it on the group itself, not only on what the group launches.

## Part 2 — The ceiling (permission boundary)

Two documents, each a separate file so the tests can read them:

- `infra/modules/budget/boundary.json`: look, scale only Kaval-labelled groups, stop only Kaval-labelled servers, snapshot only Kaval-labelled disks, write its own log. **Fixed text on purpose:** it does not follow the module's variables, because a ceiling that moves with the thing it limits is not a ceiling.
- `infra/modules/waste-removal/boundary.json.tftpl`: the same shape for the remover, with the environment filled in.

Each is an `aws_iam_policy` attached to its role with `permissions_boundary = ...`. The rule IAM applies: **allowed only if the role's policy allows it, the boundary allows it, and nothing says no.** So even a role edited to "allow everything" stops at the ceiling.

## Part 3 — The snapshot rule (A-5), and one change from the story as written

The story said "require the `Env` label to match". The remover's code does something the story did not know: it also deletes old snapshots that carry **no** environment label at all (the `UNLABELLED_DAYS` rule). A "must match" would silently stop that. So the fix is a **refusal** instead: a `Deny` on `ec2:DeleteSnapshot` for any snapshot labelled with one of the *other* environment names. That is exactly the code's `other_env` rule, now enforced by IAM too. A test keeps the environment list in Terraform equal to the one in the code.

## Part 4 — Prove it

```bash
python -m pytest scripts/dev/test_iam_boundaries.py -q
```

The tests take each boundary, pair it with a role policy **widened to allow everything**, and ask about ~100 actions. Every action the roles really use must be allowed; every action below must be refused:

- ending, stopping or starting a server; detaching a disk
- any change to IAM (a user, a role's policy, the role's own boundary)
- deleting a disk with no label, another project's disk, another environment's disk, Terraform's disk
- relabelling a disk's `Project` label while "marking" it
- deleting another environment's snapshot
- scaling a group with no label, or another project's

Two ways of checking, so neither is taken on trust:

1. A small checker in the test file that follows IAM's rules for the four condition operators the boundaries use. It runs everywhere, CI included. An operator it does not know fails the test.
2. IAM's own policy simulator, on request (`KAVAL_IAM_SIMULATE=1`; read-only, free, changes nothing). It must give the same answer as the checker for every case.

```bash
AWS_PROFILE=kaval KAVAL_IAM_SIMULATE=1 python -m pytest scripts/dev/test_iam_boundaries.py -q
```

Run on 2026-11-17: 116 passed, with the simulator agreeing on every case. The first run found a mistake in **the test**, not the boundary (a made-up log ARN that AWS rejects as malformed); fixing it is the reason the simulator run exists.

**Prove the tests can fail.** Take the label condition out of the brake's boundary, or change the `Deny` in the remover's to `Allow`, and run the file again: five tests fail (three brake cases, one per environment for the remover). Put it back and they pass.

There is also a check the other way: a boundary written too **tight** would silently stop a role's own work. `test_a_boundary_can_never_be_too_tight_for_its_role` checks that every action each role's own policy names is also in its ceiling.

## Part 5 — Going live, and what was seen (2026-11-17)

Nothing was applied by the build itself. It went live in steps, each on the owner's typed go:

1. **A staging session** (`go staging session`, 07:35 to 07:59 IST, about 3 cents): staging built from the plan shown (94 added, 0 changed, 0 destroyed; the 94th is its remover's ceiling), running `sha-fb25ed6`.
2. **The brake's change, applied to production's record** (`apply brake`, 07:47): the saved plan, exactly (1 added, 2 changed, 0 destroyed; the brake only, production's servers and the budget untouched). A fresh plan then said "No changes". The brake lives in production's record but covers staging's group too, so this is the only way to prove it on staging.
3. **The brake fired for real** through the alert topic, as in [Lab 62](lab-62-the-spending-brake.md): `kaval-prod` "already at zero" (left alone), `kaval-staging` "scaled 1 -> 0", the pre-stop snapshot taken (labelled `Project=kaval`, `Env=staging`, `ManagedBy=kaval-lambda`, `Reason=pre-stop`) and staging's database stopped, in 5.8 seconds. Production's database and the dev server did not change. The limit and the ceiling did not get in the way of the one job the brake exists for.
4. **What the deployed brake refuses**, asked of AWS's simulator for the real role: update a group labelled `Kaval` allowed; a group labelled for another project or with no label, refused; ending a server and any IAM change, refused.
5. **The removal worker**, on staging, with its new ceiling: the deployed role and its ceiling were asked all 39 of the test cases (0 mismatches); then two hand-made orders through the real worker on throwaway 1 GiB disks: marking and backing up a loose staging disk went through (status `ok`; the backup labelled `staging` / `kaval-lambda`; the mark on the disk), and deleting a copy labelled `prod` was refused (`other_env`) and the copy still existed. Everything the proof made was deleted afterwards and the sweep after teardown was clean.
6. **`KAV-162` rode the same session:** the staging smoke test passed 7 of 7 (the only failure was the known "this build has no version tag"), the gateway pod showed no `kube-api-access` volume, and the database came up fully migrated.

`make iam-review` after step 2 shows the brake with "permission boundary: yes", and the old wide group statement is gone (only "look at groups" remains, which AWS cannot narrow). **Still to do:** production's remover ceiling (its plan: 1 added, 2 changed, 0 destroyed, shown on the owner's go); then the review shows "yes" on both roles and `KAV-161` closes.

## What to take away

- A boundary is a ceiling, not a grant. It protects against the next edit, not the last one.
- Test a boundary with a widened role: the question is "what could this still do if someone made the role as wide as possible", not "what does the role say".
- Write the refusals as tests. A boundary that has never been seen refusing something has not been shown to work.
- Check the story against the code before copying its wording. "Must match" would have broken a rule the code already had.
