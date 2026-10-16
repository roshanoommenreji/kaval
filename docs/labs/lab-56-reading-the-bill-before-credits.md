# Lab 56 — Reading the bill honestly: usage before credits

**Phase:** 6 · **Story:** `KAV-91` · **Time:** about 30 minutes · **Cost:** a few cents (each Cost Explorer call is $0.01).

The account holds $140 of credits, which cancel every dollar of usage. Anything that reads the *net* figure (the
budget, the old cost report) sees $0 however much is used. This lab shows how to see the real number, reads what the
budget actually watches, and finds what else lives in the account. The decision is
[ADR-0059](../adr/0059-always-on-cost-model-and-measuring-usage-before-credits.md).

## Prerequisites

- `AWS_PROFILE=kaval`; on Windows with Git Bash also `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` and `MSYS_NO_PATHCONV=1`
- Read access to Cost Explorer and Budgets (the project login has it); Cost Explorer lags about a day

## Step 1 — Usage and credits, side by side

```bash
START=2026-10-01; END=2026-10-17     # END is the day after the last day you want
# usage only: leave credits and refunds out
aws ce get-cost-and-usage --time-period Start=$START,End=$END --granularity MONTHLY --metrics UnblendedCost \
  --filter '{"Not":{"Dimensions":{"Key":"RECORD_TYPE","Values":["Credit","Refund"]}}}' \
  --query "ResultsByTime[0].Total.UnblendedCost.Amount" --output text
# credits only
aws ce get-cost-and-usage --time-period Start=$START,End=$END --granularity MONTHLY --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"RECORD_TYPE","Values":["Credit"]}}' \
  --query "ResultsByTime[0].Total.UnblendedCost.Amount" --output text
```

Seen 2026-10-16: October to the 16th **6.4119** of usage and **-6.4119** of credits; September 1.3896 and -1.3896.
The two lines cancel, which is why the plain total is $0.

## Step 2 — What the budget sees

```bash
aws budgets describe-budgets --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --query "Budgets[?BudgetName=='kaval-monthly'].{limit:BudgetLimit.Amount,spent:CalculatedSpend.ActualSpend.Amount,credits:CostTypes.IncludeCredit}"
```

Seen: limit 50.0, spent **0.0**, `IncludeCredit` **true**. The budget counts credits, so its $42 / $46 alerts and the
$48 hard stop compare against $0, not $6.41. (Do not paste the account number anywhere; the command above reads it
without printing it.)

## Step 3 — The report, fixed

```bash
make cost-report
```

It now filters credits and refunds out and uses the $50 ceiling (it said $40). Seen: **$6.41**, 13% of the ceiling,
by service: EC2-Other 2.69, compute 1.65, network 1.16, container service 0.58, S3 0.28, ECR 0.05, Cost Explorer 0.01.

## Step 4 — Whose is that charge?

The container-service line is not Kaval's (the project uses no ECS). Find out whose it is, read-only:

```bash
aws ce get-cost-and-usage --time-period Start=$START,End=$END --granularity MONTHLY --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Elastic Container Service"]}}' \
  --group-by Type=DIMENSION,Key=USAGE_TYPE
aws ecs list-clusters --region ap-south-1
```

Seen: only Fargate ARM vCPU-hours (0.40) and memory hours (0.18), about $0.058 on most days, and one cluster,
`stock-trader`: another project in the same account. Nothing was changed. This is why Phase 6 limits every cost
action to resources carrying Kaval's tag.

## Step 5 — Credits left

Billing console → Credits. Seen 2026-09-26: $140 in three credits, all expiring 2027-09-11. About $132 remain.
At about $39 a month always-on that is roughly three and a half months.

## Done when

- [x] Usage and credits read separately, and shown to cancel
- [x] The budget's credit setting read from the live account
- [x] `make cost-report` shows usage before credits against the $50 ceiling
- [x] The one charge that is not Kaval's identified and left alone
- [x] The budget now counts usage before credits (ADR-0059, decision 3): see Step 6

## Step 6 — Make the budget count usage

In `infra/modules/budget/main.tf` the budget gets `cost_types { include_credit = false  include_refund = false }`.
Plan only the budget so unrelated pending changes are not swept in, read it, apply the saved plan:

```bash
bash scripts/ops/tf-init.sh prod
terraform -chdir=infra/envs/prod plan -target=module.budget -out=budget.tfplan   # 0 to add, 1 to change, 0 to destroy
terraform -chdir=infra/envs/prod apply budget.tfplan
```

Seen 2026-10-16: the only difference was `include_credit` and `include_refund` going from true to false. Read back with
the Step 2 command: `spent` **6.412**, both settings false; a second targeted plan said `No changes`. Undo: set both back
to true and apply again.
