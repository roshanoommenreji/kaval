# Phase 0 — Foundations

> **Written from:** experience
> **Lab:** [lab-00-toolchain](../labs/lab-00-toolchain.md) · [lab-01-aws-guardrails](../labs/lab-01-aws-guardrails.md)
> **Decisions:** [ADR-0001](../adr/0001-record-architecture-decisions.md) · [ADR-0002](../adr/0002-k3s-for-always-on-eks-as-a-chapter.md) · [ADR-0003](../adr/0003-aws-region.md)

## Where this sits

Nothing precedes this. The phase exists to make every later phase safe and repeatable, and it
ends before a single container runs.

It unlocks: the ability to create AWS resources without fear, because the mechanism that stops
runaway spend already exists and has been observed working.

## What we're doing

Four things, in this order:

1. A repository with a documentation structure that makes writing docs a merge gate rather than an act of willpower
2. A dedicated AWS identity with MFA and a named CLI profile
3. Budget guardrails in Terraform — alarms at $18 and $22, an automatic shutdown at $24 (raised to $30, $35 and $38 with the $40 ceiling in [ADR-0008](../adr/0008-production-database-on-its-own-server.md), then to $42, $46 and $48 with the $50 ceiling in [ADR-0028](../adr/0028-prod-app-node-on-demand-and-ceiling-50.md))
4. Proof that the shutdown actually fires

The ordering is the interesting part, and it is the subject of most of this page.

## Why this way

**Guardrails before compute.** Every AWS horror story has the same shape: something was left
running and nobody noticed until the statement arrived. The instinctive fix is discipline — "I'll
remember to check." Discipline fails at exactly the moment it is needed, which is when you are
distracted or on holiday.

So the defence has to be a control that acts without you. And a control you have not watched fire
is not a control, it is a hope. That is why the Lambda ships *disarmed* and with nothing to shut
down: you prove the whole chain — budget threshold → SNS → Lambda → log line — at the moment
when a mistake costs nothing.

The rejected alternative was building the interesting part first and adding guardrails "once
there's something worth protecting." The problem is that the window where you most need
protection is precisely the window where you are learning and most likely to leave something
running.

---

## Key concepts

### An identity is not a credential

These get conflated constantly, and the distinction matters.

An **IAM principal** is *who you are* — a user, a role, a service. It is a permanent thing with an
ARN, attached policies, and a name. An **access key** is *proof that you are that principal* — a
bearer token, rotatable, revocable, and disposable.

When `aws sts get-caller-identity` returned `InvalidClientTokenId` at the start of this project,
that error meant "the proof is no longer valid." It did not mean the identity was gone. This is
why the fix was not to repair the old keys but to create a new principal properly and issue fresh
proof.

The practical consequence: **keys are cattle, identities are pets.** Rotating a key should be a
non-event. If rotating a key breaks things, something has hardcoded proof where it should have
referenced identity.

### Why a named profile, not `default`

`aws configure --profile kaval` writes to a named section in `~/.aws/credentials` rather than the
`[default]` block.

This looks like fussiness. It is not. The `default` profile is what runs when you forget to say
which account you meant. In a world where you have a work account, a personal account, and a
client account, an unqualified `aws ec2 terminate-instances` is a loaded gun. A named profile
forces an explicit statement of intent every time.

It is the same reasoning behind not having a `prod` context selected by default in `kubectl`.

### MFA on root is the highest-value two minutes in AWS

The root account of an AWS account can do anything, including close the account and change the
payment method. It cannot be restricted by IAM policy — policies constrain IAM principals, and
root is not one.

So the only defence is authentication strength. MFA on root, then never use root again for
day-to-day work. Everything routine happens through `kaval-dev`.

### `AdministratorAccess` now, narrowed later — and why that's the honest choice

Least privilege says give an identity only the permissions it needs. Correct, and the eventual
target for the `executor` service in particular.

But applying it to your own development identity on day one means you will spend the next six
months hitting `AccessDenied`, guessing which action was missing, adding it, and repeating. That
is not learning security; it is learning frustration.

The honest approach is a broad policy now, a **documented commitment** to narrow it in Phase 8,
and no pretence in between. What makes this legitimate rather than lazy is that it is written
down, scheduled, and scoped to a personal account with a $50 ceiling.

What would *not* be legitimate is granting `AdministratorAccess` to the `executor` service. That
component runs unattended and acts on model output. It gets exactly the permissions it needs and
nothing more. The distinction is between a human identity used interactively and a machine
identity used autonomously — and it is worth being able to articulate that difference.

### AWS Budgets vs Cost Anomaly Detection

Two different services solving two different problems.

**AWS Budgets** compares spend against a number you chose. It answers "am I over $50?" It is
threshold-based, predictable, and free for the first two budgets. It is what you want for a hard
ceiling.

**Cost Anomaly Detection** learns your normal spend pattern and alerts on statistical deviation.
It answers "is something unusual happening?" It would catch a bill going from $14 to $19 — which
no threshold at $18 would flag as urgent, but which represents a 35% jump.

This project uses Budgets because the constraint is a hard ceiling, not a pattern. Anomaly
detection becomes relevant in Phase 6, when the FinOps agent needs to spot waste that is well
inside budget but still waste.

Budgets also distinguishes **ACTUAL** from **FORECASTED** notifications. Actual fires when you
have already spent the money. Forecasted fires when AWS projects you will. The configuration here
uses both: forecasted at the ceiling catches a runaway before it has finished running away.

### SNS, and the confirmation step everyone skips

**SNS** is publish/subscribe messaging. A *topic* is a named channel; *publishers* send to it;
*subscribers* receive whatever arrives. The publisher knows nothing about who is listening.

That decoupling is why one budget notification can reach both an email address and a Lambda
function without the budget knowing either exists.

The trap: **an email subscription is inert until confirmed.** AWS sends a confirmation link and
does nothing further until you click it. Terraform will report the subscription created
successfully — because it was — while it sits in `PendingConfirmation` forever.

This is a genuinely dangerous failure mode, because everything looks correct. `terraform apply`
succeeded, the resource exists, the console shows a subscription. And no alert will ever arrive.

The lesson generalises: **an alerting path is not working until you have received a message
through it.** Not until it is configured. Not until it applied cleanly. Until a message arrives.

### Lambda as event-driven compute

Lambda runs a function in response to an event and bills per invocation and per
gigabyte-second. There is no server to keep alive, so an idle Lambda costs nothing.

That property is what makes the hard stop viable inside a $50 budget. A polling process checking
spend every five minutes would need somewhere to run. A Lambda subscribed to an SNS topic costs
nothing until the day it is needed, then costs a fraction of a cent.

Three details worth internalising:

- **The handler signature is `(event, context)`.** `event` is the payload; for SNS it arrives wrapped in a `Records` array, which is why the code iterates rather than reading a single message.
- **Environment variables configure behaviour without redeploying code.** `DRY_RUN=true` and an empty `ASG_NAME` are how the same function ships disarmed and is later armed by a Terraform variable rather than a code change.
- **Everything goes to CloudWatch Logs.** A Lambda you cannot read the logs of is a Lambda you cannot debug. The log group is created explicitly in Terraform with a 14-day retention, because the default is *never expire*, and logs that never expire are a slow, silent cost.

### Terraform: plan, apply, and why state is the dangerous part

Terraform is declarative. You describe the desired end state; it works out the operations to get
there. This is the same mental model as Kubernetes manifests, and the same as a Helm chart.

The cycle is:

- **`plan`** — compare desired state against recorded state, print the difference, change nothing
- **`apply`** — perform the operations, then update the recorded state

The recorded state is the subtle part. **Terraform state is a JSON file mapping your resource
names to real cloud resource IDs.** It is how Terraform knows that `aws_sns_topic.budget` in your
code is that specific topic in AWS.

Three consequences follow, and they are the ones people learn painfully:

1. **State contains sensitive values.** Not just IDs — resource attributes, sometimes secrets. It is gitignored here, and that is not optional.
2. **Lose the state and Terraform forgets what it owns.** It will try to create everything again, and either fail on name conflicts or create duplicates you now pay for twice.
3. **State is why `plan` is trustworthy.** The diff is real, not a guess.

This project keeps state local for Phase 0, deliberately. Moving it to S3 requires creating an S3
bucket — a resource — which would violate the guardrails-before-compute rule. It moves to a remote
backend in Phase 4, along with everything else.

**Always read the plan.** For Phase 0 it should contain an SNS topic, a topic policy, two
subscriptions, a budget, an IAM role and policy, a Lambda, a permission, and a log group. If it
contains an EC2 instance, stop — something is wrong.

### Git hooks, and why history is what matters

A **hook** is a script git runs at a defined moment. `pre-commit` runs before a commit is
recorded and can reject it by exiting non-zero.

The important insight is about *history*, not the current files. If a secret is committed on
Monday and deleted on Tuesday, it is still in the repository. Anyone who clones it gets the whole
history. `git log -p` finds it in seconds. Rewriting history to remove it is possible but
genuinely unpleasant, and if the repository has been pushed anywhere, the secret must be treated
as compromised and rotated regardless.

So the only cheap moment to catch a secret is *before the first commit that contains it*. Which is
why gitleaks went in before commit one, and why this repository — private today, public at v1 —
had to be strict from the beginning rather than cleaned up later.

Hooks live in `.git/hooks/` and are **not** version-controlled, which is why there is an
`install-hooks.sh` that writes it. A hook nobody installed protects nobody.

---

## Common mistakes

| Mistake | What it costs |
|---|---|
| Never confirming the SNS email subscription | Every alert silently discarded. Discovered when the bill arrives. |
| Trusting an alarm you have not seen fire | Same outcome, more confidence. |
| Using the `default` AWS profile | A command aimed at the wrong account. Recoverable at best. |
| Committing `terraform.tfstate` | Account IDs, ARNs, sometimes secrets, in public history. |
| Removing a secret in a later commit and thinking it is gone | It is in the history. Rotate it. |
| Leaving CloudWatch log retention at "never expire" | Slow, silent, growing cost that nothing alerts on. |
| Building the interesting part first, guardrails "later" | Unbounded exposure during the period you are most likely to make mistakes. |

## Glossary

| Term | Meaning |
|---|---|
| **IAM principal** | An entity that can be authenticated — user, role, or service |
| **Access key** | A credential pair proving you are a principal; rotatable and disposable |
| **ARN** | Amazon Resource Name — the globally unique identifier of any AWS resource |
| **Root account** | The original account identity. Cannot be constrained by IAM policy. MFA it and stop using it. |
| **Named profile** | A labelled credential set in `~/.aws/credentials`, selected with `--profile` |
| **MFA** | Multi-factor authentication — a second proof beyond the password |
| **Least privilege** | Granting only the permissions actually required |
| **AWS Budgets** | Threshold alerting against a spend figure you choose |
| **Cost Anomaly Detection** | Statistical alerting on deviation from learned spend patterns |
| **ACTUAL vs FORECASTED** | Budget notification on money already spent vs money AWS projects you will spend |
| **SNS** | Simple Notification Service — publish/subscribe messaging |
| **Topic** | A named SNS channel that publishers send to and subscribers receive from |
| **Subscription confirmation** | The click required before an SNS email subscription delivers anything |
| **Lambda** | Function-as-a-service; runs on an event, bills per invocation, costs nothing idle |
| **Handler** | The function Lambda calls, taking `(event, context)` |
| **CloudWatch Logs** | Where Lambda output goes; log groups have a retention setting that defaults to forever |
| **Terraform state** | JSON mapping resource names in code to real cloud resource IDs |
| **`plan` / `apply`** | Show the diff without changing anything / perform the operations |
| **Declarative** | Describe the desired end state; the tool derives the steps |
| **Idempotent** | Running it twice produces the same result as running it once |
| **Git hook** | A script git runs at a defined point; `pre-commit` can reject a commit |
| **ADR** | Architecture Decision Record — one file capturing one decision and its reasoning |

## Check yourself

You should be able to answer these without looking anything up:

1. Your access key stopped working. Does that mean your IAM user was deleted? Why not?
2. `terraform apply` succeeded and the SNS subscription exists. Why might no alert ever arrive?
3. Why does the budget Lambda ship with `DRY_RUN=true` and an empty `ASG_NAME`, rather than being wired up properly from the start?
4. You committed an AWS key, noticed an hour later, and deleted it in the next commit. Is the repository safe? What must you actually do?
5. Why is Terraform state kept local in Phase 0 rather than in S3, when S3 is obviously better?
6. What is the difference between an ACTUAL and a FORECASTED budget notification, and why does this project use both?
7. `AdministratorAccess` on `kaval-dev` is defensible. `AdministratorAccess` on the `executor` service is not. Articulate the difference.

## In an interview

**"Walk me through how you'd stop a personal cloud project from running up a bill."**

Answer in terms of *ordering* and *proof*, because that is what distinguishes someone who has
thought about it from someone who has read about it:

> "I provision the guardrails before any compute exists — budget alarms at 72% and 88% of the
> ceiling, and a Lambda that scales the ASG to zero at 96%. Then I fire the Lambda manually and
> read the CloudWatch log, because an alarm you haven't watched fire isn't a control, it's a
> hope. The Lambda deploys in dry-run mode with no target, so the first invocation is a safe
> no-op that still proves the whole path — budget threshold, SNS topic, subscription, function.
> The specific trap is that an SNS email subscription is inert until you click the confirmation
> link, and Terraform reports success either way. I also design around the expensive defaults —
> no NAT Gateway, no ALB, no persistent managed control plane — because those three alone would
> be six times my ceiling before running anything."

The strong part of that answer is naming the failure mode where everything *looks* correct. That
is the difference between having configured something and having operated it.

## Further reading

- AWS IAM User Guide — *Security best practices in IAM* (the root account and MFA sections)
- AWS Budgets documentation — notification types and thresholds
- AWS Lambda Developer Guide — the programming model and environment variables
- Terraform documentation — *State* (particularly *Sensitive Data in State*)
- `git help hooks` — the full list of hook points
- Michael Nygard, *Documenting Architecture Decisions* (2011) — the original ADR essay
