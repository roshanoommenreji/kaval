# Lab 101 — The AWS half of the access review

**Phase:** 8 (hardening) · **Story:** `KAV-154` · **Evidence:** [access-review.md](../architecture/access-review.md) · **Decision:** [ADR-0101](../adr/0101-the-aws-access-review-and-what-it-changes.md) · **Cost:** $0, nothing is started and nothing is changed

## What you will build

A list of every IAM role Kaval owns, which statements are wider than they should be, whether IAM Access Analyzer exists, and what the calling user may do. You will run one read-only command and read its output.

You need the repository, the project's Python environment (`make sync`) and an AWS login for the project's account (profile `kaval`). The command only lists and reads.

## Why read the live account and not only the Terraform

Real teams do both. Terraform shows what the code asks for; the account shows what is there. They differ when someone changes a role by hand, or when a module forgets a restriction the reader assumed. Reading the live roles is the check that nothing drifted, and it is the only way to see the roles' *trust* (who may take them) and whether Access Analyzer was ever switched on.

## Part 1 — Run the scan

```bash
make iam-review
```

It prints, for each of Kaval's roles, whether a permission boundary is attached, which managed and inline policies it has, and every Allow statement that is wider than it should be. It counts another project's roles (the account is shared) and does not read them. Account numbers are masked, and the user's name and keys are not printed.

Expected on 2026-11-16 (production's roles; staging was down):

```
ROLES  Kaval's: 13   another project's (counted, not reviewed): 7
...
kaval-budget-hard-stop
  permission boundary: none   managed: -   inline: ['kaval-budget-hard-stop']
  RESOURCE        kaval-budget-hard-stop/ScaleAsgToZero: autoscaling:UpdateAutoScalingGroup, autoscaling:SetDesiredCapacity
  RESOURCE-KNOWN  kaval-budget-hard-stop/FindInstances: ec2:DescribeInstances
...
ACCESS ANALYZER
  none exists in ap-south-1
THE CALLING USER (name and keys are not printed)
  attached policies: ['AdministratorAccess']   inline: []
  second factor on the user: none
```

## Part 2 — Read the three kinds of line

| Label | Meaning | What to do |
|---|---|---|
| `ACTION` | An allowed action ending in `*` (or `*` alone) | Always a finding. None on 2026-11-16 |
| `RESOURCE` | A resource of `*` for an action AWS *can* limit to one resource | A finding unless explained. One on 2026-11-16: the brake (A-2) |
| `RESOURCE-KNOWN` | A resource of `*` for an action AWS *cannot* limit (the `Describe` calls, the registry login token, the bill) | Listed so it can be explained, not a fault. Nine on 2026-11-16, each explained in access-review.md |

A statement with a `Condition` (for example the heartbeat's metric, limited to one namespace) is limited by it and is not listed.

## Part 3 — Read who may take a role

The scan does not print trust policies; read the two that matter by hand:

```bash
aws iam get-role --role-name kaval-ci-publish --query 'Role.AssumeRolePolicyDocument' --profile kaval
```

You should see one statement: the GitHub sign-in provider, limited to this repository **on `main`**, by its fixed numeric identifiers, with the audience `sts.amazonaws.com`. A role that GitHub can take for *any* branch or *any* repository would be a finding. The other GitHub role (`kaval-backup-watch`) reads the same.

## Part 4 — The negative case

The scan's rules are tested on made-up policies, with no AWS call (`scripts/ops/test_iam_review.py`, 10 cases): a narrow policy has nothing wide; `ec2:*` and `*` are flagged; scaling a group on `*` is flagged because AWS can name the group; `ec2:Describe*` on `*` is listed as known; a Condition makes `*` acceptable; a Deny is never a finding; a one-statement policy that is not in a list is read.

```bash
python -m pytest scripts/ops/test_iam_review.py -q
```

## What this found

Five things, all in [access-review.md](../architecture/access-review.md): no Access Analyzer (A-1), a brake that may scale down any group (A-2), no permission boundary on any role (A-3), the laptop key's breadth (A-4) and a snapshot delete that ignores the environment label (A-5). The decision on what to change is [ADR-0101](../adr/0101-the-aws-access-review-and-what-it-changes.md).

## Still to do

The changes themselves: `KAV-160` (the analyzer, done: [Lab 105](lab-105-a-watcher-for-what-outsiders-can-reach.md)), `KAV-161` (the brake, the two boundaries, the label check), `KAV-162` (the cluster half's finding C-1). Each is staging first, then production, each on the owner's go.

## Clean up

Nothing to clean up: the command changes nothing.
