# Lab 01 — AWS credentials and budget guardrails

**Phase:** 0 · **Time:** ~90 min · **Cost:** $0 (Budgets and SNS are free; the Lambda will not run)

The first AWS work of the project, and deliberately the *only* thing provisioned before compute:
the mechanism that stops the bill. Nothing that can cost money gets created until this has fired
once and been observed.

---

## Why this order

Every AWS horror story is the same shape — something was left running and nobody found out until
the statement arrived. The defence is not discipline, it is a control that acts without you.

So: alarms first, compute later. By the end of this lab the account can shout, and there is
nothing for it to shout about. That is the correct state to be in.

---

## Prerequisites

- [Lab 00](lab-00-toolchain.md) complete
- Access to the AWS console for this account

---

## Step 1 — Diagnose the existing credentials

```bash
aws sts get-caller-identity
```

Currently returns:

```
An error occurred (InvalidClientTokenId) when calling the GetCallerIdentity
operation: The security token included in the request is invalid.
```

The keys in `~/.aws/credentials` are expired, deleted, or belong to a closed account. Do not try
to repair them — Step 2 replaces them properly.

---

## Step 2 — Create a dedicated IAM user

In the console: **IAM → Users → Create user**.

- Name: `kaval-dev`
- **No console access** — this identity is for the CLI only
- Attach `AdministratorAccess` **for now**, and note in the journal that it must be narrowed in
  Phase 9. Starting least-privilege here means fighting permission errors during every lab; the
  honest approach is a broad policy now, a documented narrowing later, and no pretence in between.

Then **Security credentials → Create access key → Command Line Interface**.

Enable **MFA** on the root account if it is not already on. Root without MFA is the single
largest risk in any personal AWS account, and it is a two-minute fix.

---

## Step 3 — Configure a named profile

Never use the `default` profile for a project. A mistyped command against the wrong account is a
lot easier when everything shares one nameless identity.

```bash
aws configure --profile kaval
# AWS Access Key ID:     <from step 2>
# AWS Secret Access Key: <from step 2>
# Default region name:   ap-south-1
# Default output format: json
```

Verify:

```bash
aws sts get-caller-identity --profile kaval
```

You should see an account ID and the `kaval-dev` user ARN. **Do not paste that output into any
committed file** — it contains your account ID.

---

## Step 4 — Answer the free-tier question

Open **Billing → Free tier** in the console.

AWS restructured the free tier in 2025 and terms differ between older and newer accounts. Record
what you actually see:

- [ ] Free tier active? Expiry date?
- [ ] Any credit balance?
- [ ] EC2 hours remaining, if any?

Write the answer into `docs/cost/budget-plan.md` under "Open question". It can only move the
projected cost down, but a guess is worse than a check.

---

## Step 5 — Settle the region

Read [ADR-0003](../adr/0003-aws-region.md). It proposes `ap-south-1` and leaves one thing open:
which Bedrock models are actually available there.

Open **Bedrock → Model access** in `ap-south-1` and look. Then:

- Available directly → single region, done
- Not available, but a cross-region inference profile covers it → use that
- Neither → call Bedrock in another region for the escalation path only

Update ADR-0003 with what you found and change its status from `Proposed` to `Accepted`. Use the
`claude-api` skill for current model IDs rather than recalling them.

---

## Step 6 — Provision the guardrails

```bash
cd infra/envs/prod
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars — your real email address
terraform init
terraform plan
```

Read the plan properly. It should create exactly: an SNS topic, a topic policy, two subscriptions,
a budget, an IAM role and policy, a Lambda function, a Lambda permission, and a log group.
**Nothing that bills.** If the plan contains an EC2 instance, a NAT gateway, or a load balancer,
stop — something is wrong.

```bash
terraform apply
```

Then **check your inbox and confirm the SNS subscription**. AWS sends nothing until you click it.
This is the step people skip, and it is the step that makes the whole control inert.

---

## Step 7 — Prove it works

An untested alarm is not a control. Fire the Lambda by hand:

```bash
FN=$(terraform output -raw hard_stop_function_name)

aws lambda invoke \
  --function-name "$FN" \
  --payload '{"Records":[{"Sns":{"Subject":"TEST","Message":"manual guardrail test"}}]}' \
  --cli-binary-format raw-in-base64-out \
  --profile kaval \
  /tmp/out.json

cat /tmp/out.json
```

Expect `{"status": "no-op", "reason": "no asg configured"}` — correct for Phase 0, because there
is no compute to stop yet. The point is that the wiring works.

Read the log to confirm it actually ran:

```bash
aws logs tail "/aws/lambda/$FN" --profile kaval --since 5m
```

**On Windows Git Bash**, this fails with a confusing `InvalidParameterException` about the
`logGroupName` regex, even though the string is clean — the actual cause is MSYS silently
rewriting a leading-`/` argument into a Windows path before it reaches `aws.exe`. Fix:

```bash
MSYS_NO_PATHCONV=1 aws logs tail "/aws/lambda/$FN" --profile kaval --since 5m
```

---

## What actually got created, and why each piece exists

Ten resources, all in `ap-south-1` (check the console's region selector — an empty list there
usually means you're looking at the wrong region, not that nothing was created).

| Resource | Console location | What it's for |
|---|---|---|
| SNS Topic `kaval-budget-alarm` | **SNS** → Topics | The single channel every alert flows through. Budgets publishes here; anyone or anything subscribed gets a copy — that decoupling is why the same threshold breach can reach both an inbox and a Lambda without either knowing the other exists. |
| SNS Topic Policy | Same topic page → Access policy tab | Grants `budgets.amazonaws.com` permission to publish to the topic. Without it, AWS Budgets could not deliver its alerts here at all. |
| SNS Subscription — email | **SNS** → Subscriptions | You, the human in the loop, for this specific alarm. Inert until confirmed — see the note above about `PendingConfirmation`. |
| SNS Subscription — Lambda | Same place | The machine in the loop. Fires automatically on every threshold breach, no confirmation needed for this protocol. |
| Budget `kaval-monthly` | **Billing and Cost Management** → Budgets | The actual $25 ceiling and its four thresholds ($18, $22, $24 actual; $25 forecasted). This is what AWS evaluates daily against real spend — everything else exists to react to what this decides. |
| IAM Role `kaval-budget-hard-stop` | **IAM** → Roles | The identity the Lambda runs as. Deliberately narrow — see the policy below — rather than reusing a broad role out of convenience. |
| IAM Role Policy (inline) | Same role → Permissions tab | Exactly two permission groups: `autoscaling:*` (to zero out an ASG later) and `logs:*` (so it can write its own audit trail). Cannot touch billing, cannot touch IAM, cannot touch anything outside its one job. |
| Lambda Function `kaval-budget-hard-stop` | **Lambda** → Functions | The actual hard stop. Ships disarmed (`DRY_RUN=true`, no ASG target) on purpose — Phase 0 has nothing to protect yet, so the safe behaviour is a no-op that still proves the path works. **Since 2026-09-26 it also stops running `Project=kaval` servers outside an ASG, armed for real.** See "Arming the hard stop for standalone servers" below |
| Lambda Permission (resource-based) | Same function → Configuration → Permissions tab | The other half of the SNS→Lambda link — grants SNS itself permission to invoke the function. The subscription alone isn't enough; both sides have to agree. |
| CloudWatch Log Group `/aws/lambda/kaval-budget-hard-stop` | **CloudWatch** → Log groups | Where every invocation's reasoning ends up — "why did/didn't I scale anything down." Retention set to 14 days explicitly; the default is *never expire*, which is its own slow, silent cost. |

This table is also in `KAV-18` (Jira) and the Confluence Labs section — same content, generated
from this file, not retyped by hand.

---

## Step 8 — Set a calendar reminder

Monthly, on the 1st: run `make cost-report` and record actuals in `docs/cost/actuals/`.

Automation catches runaway spend. A recurring human check catches the slow drift that stays under
every threshold — which is the more likely failure mode on a project this small.

---

## Arming the hard stop for standalone servers (added 2026-09-26, `KAV-30`)

Phase 0 built the hard stop to scale the Phase 4 server group to zero, and shipped it with no
target and in dry-run. Correct at the time, but it meant the $24 stop **protected nothing**. When
the AWS dev server arrived ([Lab 03](lab-03-aws-dev-server.md)), the Lambda was extended:

- **Stop running servers tagged `Project=kaval` that aren't in an ASG.** Stop, never terminate,
  so disks survive. ASG members are skipped; the group would just replace them, and the ASG path
  handles those.
- **Armed for real** with `stop_tagged_instances = true` in `infra/envs/prod/main.tf`. The ASG
  path stays dry-run until Phase 4 gives it a target.
- **Narrow permissions.** `ec2:DescribeInstances` (read-only; IAM can't scope it by tag), and
  `ec2:StopInstances` only where `aws:ResourceTag/Project = kaval`. No terminate.

Fire it by hand exactly as the alarm would, and watch the server stop:

```bash
aws lambda invoke --function-name kaval-budget-hard-stop --cli-binary-format raw-in-base64-out \
  --payload '{"Records":[{"Sns":{"Subject":"manual test","Message":"simulated"}}]}' out.json
cat out.json
make devbox-status
```

2026-09-26 result: `{"asg": {"status": "no-op", ...}, "instances": {"status": "stopped",
"instances": ["i-…"]}}`. The server went running → stopping → stopped in about 15 seconds.
The log line reads `HARD STOP APPLIED. Stopped i-…`. `make devbox-up` brought it back intact.

Then prove the permission boundary with the IAM policy simulator, which evaluates without acting:

```bash
aws iam simulate-principal-policy --policy-source-arn <hard-stop role ARN> \
  --action-names ec2:StopInstances --resource-arns arn:aws:ec2:ap-south-1:<acct>:instance/i-0123456789abcdef0 \
  --context-entries ContextKeyName=aws:ResourceTag/Project,ContextKeyValues=kaval,ContextKeyType=string
```

| Request | Decision |
|---|---|
| Stop, tagged `Project=kaval` | `allowed` |
| Stop, tagged `Project=other` | `implicitDeny` |
| Stop, untagged | `implicitDeny` |
| Terminate, tagged `Project=kaval` | `implicitDeny` |

**Found along the way: the SNS email subscription had vanished.** The plan unexpectedly wanted to
*create* the email subscription. `aws sns list-subscriptions-by-topic` showed only the Lambda
subscribed. The email one, confirmed by hand on 2026-09-15, was gone, and Terraform's state still
described a stale pending one. The alerts themselves never stopped reaching you, because every
budget notification also emails you **directly** from AWS Budgets. What was missing was the
SNS copy. The apply re-created it, and the new "Subscription Confirmation" email was confirmed.
Then both checks passed: `list-subscriptions-by-topic` showed email and Lambda confirmed, and a
refreshed `terraform plan` reported "No changes". It's the same trap as Step 6, and the same
lesson: re-check a control after the fact, don't assume it stayed put.

---

## Done when

- [ ] `aws sts get-caller-identity --profile kaval` succeeds
- [ ] MFA enabled on root
- [ ] Free-tier status recorded in `docs/cost/budget-plan.md`
- [ ] ADR-0003 updated to Accepted with the Bedrock finding
- [ ] `terraform apply` created guardrails and nothing billable
- [ ] SNS email subscription **confirmed** from the inbox
- [ ] Lambda invoked manually, log read
- [ ] Monthly cost-review reminder set
- [ ] Journal entry appended
- [ ] Jira story moved to Done

---

## What to write down

For the course, the interesting parts of this lab are the arguments, not the clicks:

- Why guardrails precede compute
- Why `AdministratorAccess` now and narrowed later is more honest than pretending to
  least-privilege while fighting permission errors
- Why an unconfirmed SNS subscription makes the entire control useless
- Why a named profile matters more than it looks
