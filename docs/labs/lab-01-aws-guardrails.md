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

## Step 8 — Set a calendar reminder

Monthly, on the 1st: run `make cost-report` and record actuals in `docs/cost/actuals/`.

Automation catches runaway spend. A recurring human check catches the slow drift that stays under
every threshold — which is the more likely failure mode on a project this small.

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
