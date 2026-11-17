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

## Part 5 — Why this is not live yet

Nothing here is applied. The change goes live in three steps, each on its own typed go:

1. The brake lives in **production's** Terraform record (it covers both environments' groups), so it is applied there first, with the plan shown and only the brake in it. The risk of this change is the brake itself: the nightly 02:00 stop and the $48 stop use the same function and role, so a mistake here breaks both. That is why step 2 follows at once.
2. A staging session fires the brake for real and checks it stops staging's group, and runs the remover's refusals again on staging's own copy (applied by the same session).
3. Production's remover gets its ceiling at production's next start-up.

`make iam-review` then shows "permission boundary: yes" on both roles. The record of each step goes in the journal and in this lab's last section.

## What to take away

- A boundary is a ceiling, not a grant. It protects against the next edit, not the last one.
- Test a boundary with a widened role: the question is "what could this still do if someone made the role as wide as possible", not "what does the role say".
- Write the refusals as tests. A boundary that has never been seen refusing something has not been shown to work.
- Check the story against the code before copying its wording. "Must match" would have broken a rule the code already had.
