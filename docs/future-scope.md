# Future scope — beyond v1

Phases 0–9 in [../ROADMAP.md](../ROADMAP.md) are the committed build. This page is the
holding pen for work that is **out of scope for v1** but worth doing afterward, and the
reasoning for why it matters.

The framing question: *does building Kaval make Roshan ready for a Forward Deployed
Engineer role, or the adjacent AI-platform role he is actually aiming at?*

---

## What Kaval already builds

Roughly 70% of the hard-skills bar for both roles:

| Skill | Where Kaval covers it |
|---|---|
| Kubernetes ops, Helm, multi-env promotion | k3s, umbrella chart, `local → staging → prod` with digest pinning |
| Cloud + IaC under real constraints | Terraform modules/envs; the $50/mo ceiling forces the same trade-off reasoning FDEs use daily (Graviton/arm64, no NAT GW, ephemeral EKS) |
| LLM integration | Self-hosted Gemma, Pydantic-validated output, runbook RAG, Bedrock escalation |
| Safe agent design | Read-only `agent` vs. scoped `executor`, human-in-the-loop, evidence-gated autonomy |
| Release engineering | Build-once-promote-artifact, `promote.yml` gate, gitleaks, clean history |
| Written artifacts left behind | ADR / lab / journal discipline as a merge gate |

This is a credible portfolio piece and interview narrative on its own. Stopping after
Phase 5 still yields that.

---

## What a solo greenfield project structurally cannot teach

These are the gaps. None of them are reasons not to build Kaval; they are the reason
Kaval is *necessary but not sufficient*.

1. **No customer.** The core FDE loop is someone else's problem, someone else's
   stakeholders, someone else's definition of done. A solo project can't simulate the
   "walk in with a vague brief" muscle.
2. **No integration hell.** Real FDE work is 50–60% plumbing into systems you don't
   control — legacy APIs, unfamiliar auth, data you can't fully see. Every interface in
   Kaval is one we designed.
3. **Wrong tempo.** Kaval is deliberately slow, gated, and documented. FDE prototyping is
   fast and often disposable, then you harden the 20% that survives. Different reflex.
4. **Narrow stack.** Python + TypeScript only. FDEs often switch stacks per engagement.
5. **Thin frontend, no data engineering, single-tenant, single-node.** No scale,
   multi-tenancy, or performance work anywhere in the plan.

---

## Candidate work to close the gap

Post-v1, not committed. Each is small and deliberately faster / messier than the main build.

### FS-1 — Integration mini-projects (highest value)

Two weekend-sized builds that wire Kaval's agent into a **real third-party system we did
not design**, timeboxed, minimal docs. The point is the plumbing reflex, not the polish.

- Candidate A: ingest a real external billing/metrics API (e.g. a SaaS usage API) as a
  new signal source, mapped into the existing `signal` model.
- Candidate B: push proposals into a real ticketing system (Linear / Jira / PagerDuty)
  instead of only the mobile app, via that system's real API and auth.

Exit: agent acts on data from a system whose API shape we had to reverse-engineer from docs.

### FS-2 — Demo craft

A 5-minute "here is the problem, here is the thing working" walkthrough, recorded and
iterated. FDEs live or die on this. Overlaps with the Phase 8 demo video but is a distinct
skill — practice it as its own thing, against a stopwatch, more than once.

### FS-3 — Stack breadth spike

One small service in a third language (Go for the executor, or a Rust signal parser) to
prove stack mobility. Only if it doesn't compromise the "build once, promote the artifact"
and "environments differ only by values" constraints.

### FS-4 — Client-facing reps

Not code. Use internal "customers" at the current Release Manager job — treat a real team
as a stakeholder, take a fuzzy ask, ship something, demo it back. The only way to build
the dimension items 1–2 above call out.

### FS-5 — Multi-tenant / scale chapter

A dedicated chapter (like the EKS one) that runs Kaval against two simulated clusters with
separate tenancy and a shared control plane. Exercises the parts of platform engineering
Kaval currently skips.

### FS-6 — Petti: a box for the cloud

A small tool that puts any project in a standard box for the cloud: one short box file becomes
plain OpenTofu, is shown in plain English with its monthly cost, and is built only after a human
approves.

Jira epic: [KAV-99](https://roshanoommenreji.atlassian.net/browse/KAV-99). Planned 2026-10-28.
**Not started until the current Kaval phase closes.** It would be its own repository.

**The idea.** Kubernetes gave apps a standard box (the container) that runs anywhere. Petti
(Malayalam for "box") would do the same for the cloud: drop any project into a box, give it a
few settings, and have it come up on AWS or another cloud, scaled as asked. Even with AI writing
the code, cloud setup is slow because of *decisions* (which service, size, network, permissions),
*fear* (what will this cost, what will it break) and *day 2* (upgrades, half-failed builds,
leftovers). Petti targets those, not the typing.

**What already exists** (checked 2026-10-28):

| Kind | Tools | Lesson for Petti |
|---|---|---|
| Open "box file" standards | [Score](https://docs.score.dev/docs) (CNCF Sandbox), [Radius](https://opensource.microsoft.com/blog/2025/06/30/expanding-platform-engineering-capabilities-with-radius-resource-types/) recipes, OAM/KubeVela | Score already *is* the box shape: build on it, don't invent one |
| Kubernetes-based bundles | [Crossplane v2](https://blog.crossplane.io/announcing-crossplane-2-0/), [kro](https://pkg.go.dev/github.com/kubernetes-sigs/kro@v0.9.3) | Powerful, but need a cluster: too heavy for "anybody" |
| "Your project → your cloud" products | [Defang](https://github.com/DefangLabs/defang-docs/pull/282/files) (Compose → AWS/GCP), [Encore](https://encore.dev/resources/cloud-infrastructure-automation-tools), Nitric, [HCP Terraform no-code modules](https://developer.hashicorp.com/terraform/cloud-docs/no-code-provisioning/module-design), [ECS Express Mode](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html) | Try these first (Stage 0): one may already be enough |
| AI infrastructure agents | [Pulumi Neo](https://www.pulumi.com/docs/ai/neo/), [System Initiative](https://www.businesswire.com/news/home/20250827291941/en) | Propose → human approves is becoming the norm |
| The graveyard | Wing (a cloud *language*, $20M, [shut down 2025](https://thenewstack.io/?p=22784696)); Acorn ([mothballed 2024](https://pipedot.org/6KJNP)); AWS [Proton](https://docs.aws.amazon.com/proton/latest/userguide/proton-end-of-support.html) (retired 2026-10-07), [Copilot CLI](https://aws.amazon.com/blogs/containers/announcing-the-end-of-support-for-the-aws-copilot-cli) (retired 2026-06-12), [App Runner](https://docs.aws.amazon.com/ja_jp/apprunner/latest/dg/apprunner-availability-change.html) (closed to new customers 2026-04-30) | No new language; expect the clouds to move under any box layer |

**The design.** No new language, no Terraform fork, no Kubernetes needed. A small CLI reads a
Score-based box file with a few extra fields and turns it into **ordinary OpenTofu** through
vetted per-cloud recipes:

```yaml
name: my-shop
run:    { build: ./Dockerfile, port: 3000 }
needs:  { database: postgres, files: bucket, domain: shop.example.com }
size: small            # small | medium | large, mapped per cloud
scale: { min: 1, max: 5 }
budget: 20             # $/month; the box refuses to build above it
cloud: aws             # the only line that changes between clouds
secrets: [STRIPE_KEY]  # names only; values live in the cloud's secret store
```

`petti init` (AI **drafts** the box file and never holds cloud credentials, the same split as
Kaval's agent and executor) → `petti plan` (plain-English plan + cost estimate + policy check;
over budget is refused) → human approval → `petti up` (everything tagged) → `petti down` /
`petti eject` (hand back the plain OpenTofu, so no lock-in). "Scale up" means changing one word:
`size: small` might be one cheap server behind Cloudflare Tunnel, while `large` might be ECS
Express Mode behind a load balancer (about $18/mo more for the ALB).

**Stages** (each is an acceptance criterion on KAV-99):

| # | Stage | Gate |
|---|---|---|
| 0 | Try Defang, Score and ECS Express Mode on one sample app | If one fits, use it and close the epic |
| 1 | Box file format + 5 example boxes | Readable by a non-expert |
| 2 | AWS translator: 4 recipes (web service, Postgres, bucket, domain), `plan`/`up`/`down`/`eject` | Up and down with nothing left behind |
| 3 | Safety net: cost estimate, budget refusal, policy (rules from [FS-8](#fs-8--security-from-code)), plain-English plan, approval | Over-budget box refused before anything is created |
| 4 | AI drafts the box file, built as [FS-7](#fs-7--infrastructure-from-code): evidence from the code first, AI for the gaps | Correct for 5 real projects |
| 5 | GCP recipes | The 5 boxes work unedited except `cloud:` |
| 6 | Web page: boxes, cost, plan, approve | — |

Rough size: an AWS-only version is ~10–12 sessions, and each extra cloud ~4 more.

**Hard problems, stated up front.** The box can only promise what every cloud offers, so it
needs an escape hatch (plain OpenTofu beside it). Moving a box moves the setup, not the data.
Permissions and networking differ per cloud and are where the time really goes. Day 2 work is
most of the job. As a business the space is crowded. As a platform-engineering portfolio piece
it is a strong fit.

**Planning standard, in short.** Tested in a separate throwaway AWS account, never Kaval's.
Every box takes `--env staging|prod`. The AI only drafts files. The tool's role is scoped to its
own tag, and a leftover scan (like `make tag-scan`) catches orphans. Recipes switch on logs and a
health alarm by default. Test boxes are created and destroyed in the same session (cost measured
when built, not guessed). Tests: box → OpenTofu golden files; up → smoke → down; and the negative
case, proving over-budget and dangerous boxes are refused.

---

## From code to running system

Added 2026-11-01. FS-1 to FS-5 close the gaps a solo project leaves. These four ask a different
question: how much of the cloud setup, the security and the operations can follow from the code,
instead of being written separately afterwards? Kaval has paid for that separation twice. Prometheus
was built with no rollout planned, which is why the planning standard exists, and the approval route
ran with no login until the Phase 8 threat model found it (`KAV-115`).

**All four connect to Petti (FS-6), which sits in the middle of one chain:**

```text
spec (FS-9)        what it is for, how much traffic, how safe, what budget
code               what it really uses, proved by file and line
   |
   v
box file, diagram and cost (FS-7) + security rules and checks (FS-8)
   |               written into the pull request; no cloud touched
   v
a person approves
   |
   v
Petti builds it (FS-6): staging, then production
   |
   v
Kaval watches it and learns (FS-10)
   |
   +--> what it learns becomes the next spec (FS-9)
```

| Idea | Where it meets Petti | What each side gets |
|---|---|---|
| [FS-7](#fs-7--infrastructure-from-code), infrastructure from code | Stage 4, "AI drafts the box file" | FS-7 **is** that stage, done with evidence first and AI second. Petti keeps the translator, so neither builds the other's half |
| [FS-8](#fs-8--security-from-code), security from code | Stage 3, the safety net | FS-8's rules become Petti's policy pack, and Petti's recipes make the safe setting the default |
| [FS-9](#fs-9--ai-dlc-spec-and-context-driven-development), AI-DLC | Stage 1 onward | Petti is the full pilot, because a new repository has nothing to migrate. The spec's numbers fill `size`, `scale` and `budget` |
| [FS-10](#fs-10--aiops-for-kaval), AIOps | "Day 2 work is most of the job" | Every Petti recipe switches on logs and a health alarm, and Kaval reads them. The box file gives Kaval a map of what depends on what, and a budget per box |

Petti's scope does not change: FS-7 and FS-8 are how two of its stages get built.

**Suggested order, cheapest and most useful first.** None of it starts before the current Kaval
phase closes.

1. FS-9, Stages 0 and 1: a one-page spec on the next real stories. It costs nothing and helps
   everything after it.
2. FS-8, Stages 0 to 2: score Kaval, finish the scanners (Stage 1's code scanner, CodeQL, is `KAV-110`, built 2026-11-07),
   widen the route check.
3. FS-10, Stages 0 to 2: a baseline, then early warnings. This improves the product itself.
4. Petti, Stages 0 to 3, alongside FS-7, Stages 0 to 2. The two meet at Petti's Stage 4.

### FS-7 — Infrastructure from code

The code and a short spec work out what an app needs: servers, network, database, cache. The plan,
diagram and cost stay current as you code, and nothing is built in the cloud until a person approves.

Jira epic: [KAV-118](https://roshanoommenreji.atlassian.net/browse/KAV-118). Planned 2026-11-01.
**Not started until the current Kaval phase closes.** It sits in front of Petti: this writes the box
file, Petti builds it.

**The idea.** Today the app is written first and its cloud setup separately, even when AI writes both:
`services/` first, then `infra/`, `deploy/`, `compose.yaml`, the diagram and the cost plan. This idea
makes the setup follow from the code, so the two cannot drift apart.

**Decided: when does the infrastructure come up? Later, not while typing.** The *description* updates
as you type. The real thing waits for approval.

| Moment | What happens | Cloud cost |
|---|---|---|
| While writing | The list of needs, the diagram, the cost estimate and the security rows update. Local stand-ins (Docker) start, so the code can run | $0 |
| Pull request | The plan and cost appear on the pull request. CI fails if the code needs something undeclared | $0 |
| Approved for staging | Built on staging, tested, destroyed | Staging's rate |
| Approved for production | The same artifact is promoted | The real bill |

Building while typing would be wrong for four reasons. Half-written code would create half-right
servers. A save button that spends money does not fit a $50 ceiling. The AI writing the code would need
cloud credentials, which breaks the rule that the part that thinks never holds the power to act. And it
would skip staging. [Encore](https://encore.dev/docs/platform/infrastructure/infra), one of the two tools
in this space still doing well, draws the same line: Docker and in-memory stand-ins locally, cloud
resources at deploy.

**What code can say, and what it cannot.** This is the limit of the whole idea.

| Question | Answered by |
|---|---|
| Does it need a database, cache or queue? Which ports? Which secrets? | **The code:** imports, connection settings, migrations, Dockerfile, Compose |
| How big? How much traffic? | **The spec** (FS-9), then measurements |
| How much downtime or data loss is acceptable? Which data is sensitive? | **The spec** |
| Which cloud, which region, what budget? | **The box file:** `cloud`, `size`, `budget` |
| Shared things: the network, logins, monitoring | **Recipes and platform defaults:** no single app owns them |

A cache is the good example. Code that calls Redis needs one. Code that is merely slow might, and only a
measurement can say, so the tool never adds Redis by itself: it asks, and shows the monthly cost. Kaval
has no cache or queue server today, and the first test is that the tool does not invent one. The spec
and the code also check each other: if the spec says no cache and the code imports Redis, CI says so.

**What real companies do** (checked 2026-11-01):

| Pattern | Examples | What happened | Lesson |
|---|---|---|---|
| The platform reads the framework | [Vercel's framework-defined infrastructure](https://vercel.com/blog/framework-defined-infrastructure) | Works well inside one platform | Reading code works when its shape is known |
| Infrastructure-from-code products | [Encore](https://github.com/encoredev/encore) (a compiler reads Go or TypeScript), [Nitric](https://github.com/nitrictech/nitric) (an SDK in the code); Wing and Klotho stopped | [The New Stack, 2025](https://thenewstack.io/infrastructure-from-code-what-went-wrong/): developers would not hand control to a vendor, and the vendor's "best practices" often did not fit their needs | Keep control: generate plain files a person can read and change |
| Infrastructure code beside the app | [SST](https://github.com/anomalyco/sst), Pulumi, AWS CDK | In wide use | Explicit, nothing inferred, but every decision is still yours |
| Platform teams and golden paths | [Checkout.com](https://thenewstack.io/infrastructure-from-code-what-went-wrong/): Backstage plus Terraform modules for about 200 engineers | The usual answer at larger companies | Vetted recipes beat clever inference |
| AI proposes, a person approves, the cost is shown | Pulumi Neo, System Initiative, [Infracost](https://www.infracost.io/docs/) (cost in the editor and on each pull request) | Becoming normal | The AI drafts and never holds the keys |

**The recommendation:** read the code (the first pattern), write ordinary files from vetted recipes (the
fourth), and let a person approve (the fifth). The tool proposes; a file in the repository decides.
Neither live product fits Kaval as it is: Encore and Nitric need code written their way, Kaval is Python
with ordinary libraries, and Encore's [AWS defaults](https://encore.dev/docs/platform/infrastructure/infra)
(Fargate or EKS, RDS or Aurora, ElastiCache) include services Kaval's ceiling rules out without a
conversation. [Kief Morris](https://infrastructure-as-code.com/posts/infra-effectiveness-part1-new-tools.html)
names the other limit: shared networks, identity and monitoring belong to no single app, so they stay
in recipes.

**The design.** No new language. A detector with no AI reads the repository and lists each need with the
file and line that proves it. The short spec adds the numbers. Together they produce the box file, the
diagram and the cost, each choice with its reason. AI may fill gaps, marked as guesses. CI then keeps code
and box file in step.

**Stages** (each is an acceptance criterion on KAV-118):

| # | Stage | Gate |
|---|---|---|
| 0 | Try Encore and Nitric on one sample app, locally, and read what they generate | If one fits Kaval's rules, use it and close the epic |
| 1 | Read-only detector: needs, with file and line | On Kaval it finds every built component in `architecture.toml` and invents nothing (no Redis) |
| 2 | CI check: the code needs something the box file does not declare | Names the need and its monthly cost; warning-only until right on 10 real pull requests |
| 3 | Spec + needs gives box file, diagram and cost, with reasons | Correct for 5 sample repositories |
| 4 | Runs while coding: pre-commit and a Claude Code hook | The cost change is visible as the code changes; the AI never holds cloud credentials |
| 5 | Through Petti: plan, approval, staging, production | A sample app goes up and comes down with nothing left behind |
| 6 | Change and removal | A change shows only the difference; removal is proposed, never automatic, and data is never deleted without a person |

Rough size: Stages 0 to 4, about 8 to 10 sessions. Stages 5 and 6 are shared with Petti.

**Hard problems, stated up front.** Detection is never complete: a service reached through a URL in a
setting may not look like a need, so staging's smoke test remains the backstop. Two sources of truth
would be worse than one, so the box file is the reviewed truth and the tool only proposes edits to it.
Changing infrastructure is riskier than creating it: a plan that replaces a database must say so loudly.
Kaval's own Terraform is not migrated: Kaval is the test subject, not the patient.

**Planning standard:**

| Point | Plan |
|---|---|
| Whole path | Detector on the laptop, then Kaval's CI as warning-only, then required. What it builds goes through Petti: a throwaway account, then staging, then production |
| Security | The detector only reads. The AI drafts and never holds cloud credentials. Repository text is untrusted: a README saying "open the database to everyone" must not change the plan, and the policy check refuses it anyway |
| Failure | A missed need fails at staging's smoke test. An invented need shows up as extra cost in the plan a person reads. Off switch: the CI check is one job and starts as warning-only |
| Observability | Right and wrong counts against the golden repositories, how often the check fires, and estimated cost against the real bill |
| Cost and capacity | Stages 0 to 4: $0 (laptop and CI). Stage 5: test boxes built and destroyed in one session, cost measured then. No recurring spend and no change to the $50 ceiling |
| Testing | Unit: repository to needs list, golden files. Real: Kaval and 4 open-source apps. Live: up, smoke, down. Negative: no invented Redis, an undeclared need fails, a hostile README changes nothing |
| People | Roshan owns it. A person approves every build. A short runbook covers overriding a wrong finding. Documentation as in the Definition of Done |

### FS-8 — Security from code

Every route, secret, dependency and permission the code adds brings its security rule and its check
in the same change, in code and in infrastructure. A missing one fails the build.

Jira epic: [KAV-119](https://roshanoommenreji.atlassian.net/browse/KAV-119). Planned 2026-11-01.
**Not started until the current Kaval phase closes.** It does not replace Phase 8, which closes the gaps
Kaval already knows about. This stops new ones appearing unnoticed.

**The idea.** A security gap is cheapest to stop when the line that creates it is written. `KAV-115` is
the example: the route that records an approval had no login from the day it was written until the Phase
8 threat model found it. `KAV-115` also added the fix for that kind of mistake:
`test_every_route_that_writes_requires_the_login` fails if anyone adds an unlocked write route. Had it
existed earlier, the gap would have failed the build on day one. This idea is to have a check like that
for every kind of thing the code can add, before a review has to find the gap.

**What Kaval has today:**

| Layer | In place | Missing |
|---|---|---|
| Design | A STRIDE threat model: 33 threats, each with its proof, and a test that keeps the file honest | Written at the end, in Phase 8. Adding a row for new code is a rule, not a check |
| Code | Types and lint, model output checked against a schema, a prompt-injection test set, CodeQL on every pull request (`KAV-110`, [ADR-0080](adr/0080-codeql-scans-every-pull-request-and-starts-as-advice.md)) | CodeQL is advice, not yet a required check; nothing scans the infrastructure code |
| Secrets | gitleaks before commit and over all history, Parameter Store | A rotation schedule |
| Dependencies | Lock file, pinned actions, Dependabot, Trivy on images | Four unhashed installs (`KAV-111`) |
| Infrastructure | No inbound ports, network rules in the chart for staging and production, a database role per service | Terraform and Helm are checked for validity, not security. IAM review (Phase 8 line). Outgoing pod traffic is not limited |
| Release | The digest that passed staging is promoted, and a guard blocks hand edits | SBOM, signing and checking the signature before deploy (Phase 8 line) |
| Running | Alert on login guessing, a spending brake, the `never` list, restore drills | Nothing else watches for attacks while running; not needed at this size yet |

**What has to happen, in code and in infrastructure.** This is the list to plan from while writing:

| When the code adds | In the code | In the infrastructure | Checked automatically by |
|---|---|---|---|
| A web route | Decide who may call it, deny by default. Limit input size, set timeouts | A network rule naming its callers; a tunnel, not a public address | A test that walks the route table; chart tests on the rendered rules |
| A secret | Read it at start-up, never log it or put it in an error | Parameter Store, encrypted, with a rotation date | gitleaks; a test that it never reaches a log or metric |
| A table or query | Parameters only, no SQL built from strings. A role with only the rights it needs | Database reachable only from the app server, encrypted on disk and in transit, backed up and restore-tested | A code scanner rule; a test of each role's rights (`KAV-116`) |
| Outside text reaching the model | Flatten it, cap it, label it as data. Check the reply against a schema | Limit where that pod can connect | The injection set and `test_threat_model.py` |
| A call to AWS or Kubernetes | Only the executor writes, never the agent | A role that allows exactly those calls, no wildcards | Calls compared with the role; IAM Access Analyzer |
| A dependency or image | Pin it, hash it | SBOM, signature checked before deploy, runs as non-root | Trivy, Dependabot, an admission policy |
| A cloud resource | Nothing in the code: declared in the box file | Private by default, encrypted, tagged, inside a budget | Checkov or Conftest on the plan; `make tag-scan` |
| A privileged action | A record of who and when, and a person's approval | An alert on repeated refusals | Policy tests; `KavalApprovalRefused` |

**How safe is "the safest"?** There is no finished state. A working definition: every known threat is
covered or accepted in writing with a date, a new one cannot enter without a check noticing, and the
layers overlap, so one miss does not open everything. `KAV-115` works that way: a login, network rules
and an alarm.

**What real companies do** (checked 2026-11-01):

| Pattern | Examples | Lesson |
|---|---|---|
| Safe by default | Netflix's [paved roads](https://www.oreilly.com/library/view/97-things-every/9781098152161/ch31.html); Google's [Safe Coding](https://research.google/pubs/safe-coding-rigorous-modular-reasoning-about-software-safety/) moves the burden to languages and frameworks, and reports nearly eliminating XSS and SQL injection in its code | The strongest measure is making the bad thing hard to write |
| Standards as the checklist | [NIST SSDF](https://csrc.nist.gov/pubs/sp/800/218/final), [OWASP ASVS 5.0](https://owasp.org/www-project-application-security-verification-standard/), [SLSA](https://slsa.dev/spec/v1.2/) for builds | Score against these rather than inventing a list |
| Threat models early, and kept current | [OWASP's guidance](https://cheatsheetseries.owasp.org/cheatsheets/Threat_Modeling_Cheat_Sheet.html); [pytm](https://github.com/OWASP/pytm) and [Threagile](https://github.com/Threagile/threagile) keep the model as code | Kaval's model is good; it needs to happen earlier |
| Scanners in the pipeline | [Semgrep](https://github.com/semgrep/semgrep) for code, [Checkov](https://github.com/bridgecrewio/checkov) for infrastructure, [Conftest](https://github.com/open-policy-agent/conftest) for policy on configuration | Too many false alarms get ignored: start warning-only and tune |
| AI review of each change | Claude Code's `/security-review` and similar | An extra layer, never the only one |

**The recommendation:** reuse the FS-7 detector, so one list of needs drives both the infrastructure and
its security rows. Finish the scanners first, because they are cheap; the one for code, `KAV-110`, is built.

**Stages** (each is an acceptance criterion on KAV-119):

| # | Stage | Gate |
|---|---|---|
| 0 | Score Kaval against NIST SSDF and OWASP ASVS Level 1 | Every gap links to a story; Phase 8's lines are not duplicated |
| 1 | Scanners in CI: CodeQL for code (`KAV-110`, built 2026-11-07; Semgrep stays an option for rules CodeQL lacks), Checkov or Trivy's config scan | Warning-only, then required, with dated exceptions like `.github/trivy-ignore.yaml` |
| 2 | Widen the route check: every route declares who may call it, reads included | A test pull request with an undeclared route is refused |
| 3 | The FS-7 list drives a security map | A new secret, table, connection, AWS call or outside text with no rule fails CI, naming what is missing |
| 4 | Threat rows proposed from the same list | Try pytm and Threagile first, else keep today's Markdown and test; a new component with no row fails CI |
| 5 | Policy on infrastructure: Conftest on the plan and the rendered chart | No public database, encryption on, no wildcards, tags present, each with a refused example |
| 6 | Least privilege from code | Real calls compared with each role; unused rights listed for a person; nothing changes automatically |
| 7 | Petti's policy pack | A dangerous box is refused before anything is created |

Rough size: about 6 to 8 sessions, and Stage 1 is already Phase 8 work.

**Hard problems, stated up front.** Scanners see patterns, not intent: a route with a login can still let
the wrong person do the wrong thing, so the threat model stays a human job. False alarms kill trust, so
every rule starts as a warning. A tool cannot know which data is sensitive; the spec has to say. And AI
writes code quickly, including insecure patterns, which is the reason the checks must be automatic.

**Planning standard:**

| Point | Plan |
|---|---|
| Whole path | Fast checks in pre-commit, then CI warning-only, then required. Policy rules are tried on staging's plan before production's. Kaval first, then Petti |
| Security | Tools pinned and checksum-verified like the existing ones, CI tokens read-only, no scanner sees a secret. An exception needs a date and a reason, so nothing is excused silently |
| Failure | A required check that cannot run fails closed. A false alarm gets a dated exception. Off switch: the job returns to warning-only in one commit |
| Observability | The threat model's covered, partly and gap counts over time; open findings and their age; exceptions close to expiry |
| Cost and capacity | $0 on AWS. These tools have free open-source versions (licences checked before use), and they run in CI, so they use no memory on the 4 GB node |
| Testing | Every rule has a refused example. Every check is run against a real past gap: a route with no login must fail. Live: IAM Access Analyzer's result kept as evidence |
| People | Roshan owns it. Reporting stays in `.github/SECURITY.md`. One short runbook covers a finding that blocks a merge, and exceptions are reviewed when they expire |

### FS-9 — AI-DLC: spec and context driven development

Work starts from a short written spec in the repo that the AI and a person agree on, then the plan,
tasks and code. Possible here: Kaval already has most of the habits, so a measured trial decides what to keep.

Jira epic: [KAV-120](https://roshanoommenreji.atlassian.net/browse/KAV-120). Planned 2026-11-01.
**Not started until the current Kaval phase closes.** Dropping it after the trial is an allowed outcome.

**In plain words.** AI-DLC is AWS's "AI-Driven Development Life Cycle". In
[AWS's words](https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle/), AI creates a plan,
asks clarifying questions, and implements only after human validation, through three phases: Inception,
Construction and Operations. Spec-driven development is the wider movement: write what you want first,
and let the AI work from that. Context engineering is choosing what the AI reads; Anthropic's rule is
"[the smallest set of high-signal tokens](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)".

**Can we introduce it? Yes, lightly, because most of it is already here:**

| The idea | Kaval today | Gap |
|---|---|---|
| A "constitution": rules that always apply | `CLAUDE.md`, the planning standard, the Definition of Done | None |
| A spec for each piece of work | A Jira story with context, acceptance criteria and UAT; a plan shown in chat | Not in the repo. The local plan file still describes a task from 2026-10-06 |
| Design recorded | ADRs and labs | None |
| Humans approve | A go at every live step, a person merges | None. Stricter than most |
| An audit trail | Journal, change records, Jira, pull requests | None |
| Each requirement linked to its test | Only for the threat model, through `test_threat_model.py` | Nothing checks that an acceptance criterion has a test |
| Context the AI can resume from | `CLAUDE.md`, memory notes, the vault | Long sessions are compacted and details are lost; a story cannot yet be resumed from the repo alone |
| "Mob Elaboration": the whole team answers the AI's questions | One person | Cannot apply. The AI asks and Roshan answers, which is already how plans work |
| The Operations phase | Kaval itself | Connects to FS-10: an incident can become the next spec |

**What real companies do** (checked 2026-11-01):

| Who | What | Honest note |
|---|---|---|
| AWS | [aidlc-workflows](https://github.com/awslabs/aidlc-workflows): 5 phases, 33 stages, 14 agents, 11 workflow profiles, approval gates and an audit trail; runs in Claude Code, among others | Made for teams, and large. Its profiles include bug fixes, so it is not only for big work |
| GitHub | [Spec Kit](https://github.com/github/spec-kit): constitution, specify, plan, tasks, implement; works with many coding agents; about 145,000 stars | Stars measure interest, not results |
| AWS Kiro | Requirements, design, tasks, inside its own editor | [A review on Martin Fowler's site](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html): a small bug became 4 user stories with 16 acceptance criteria |
| Tessl | The spec is what people maintain; the code is generated | The furthest out; in beta when reviewed in late 2025 |
| The critics | [Thoughtworks Radar](https://www.thoughtworks.com/radar/techniques/spec-driven-development): "We may be relearning a bitter lesson". The Fowler review: "I'd rather review code than all these markdown files". [Scott Logic](https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html): "Radical Idea or Reinvented Waterfall?" | These are from late 2025 and the tools move fast, so measure rather than trust either side |

**The recommendation:** match the spec to the size of the work.

| Size of work | Spec |
|---|---|
| Small fix | None: a test and a commit message |
| Normal story | One page in the repo, written before the code: intent, what is out of scope, requirements with IDs, the numbers (traffic, budget, data sensitivity), and the test for each requirement |
| New system, such as Petti | The full workflow, with the spec kept up to date as the code changes |
| Generating code from the spec and editing only the spec | No. The code is what runs, and it has to stay readable |

**Stop rule:** if a spec takes longer to review than the code it produces, or two trial stories in a row
catch nothing the current way would have missed, stop and keep only what worked.

**Stages** (each is an acceptance criterion on KAV-120):

| # | Stage | Gate |
|---|---|---|
| 0 | Try Spec Kit and aidlc-workflows on a scratch repository | Never on Kaval's main branch; what they install is read first; a written comparison |
| 1 | A one-page template from the planning standard, by hand, on the next 3 real stories | Hours, pages to review and defects found recorded |
| 2 | Requirement IDs that tests cite | A requirement with no test fails a check, the same pattern as `test_threat_model.py` |
| 3 | Resume from the repo alone | A fresh session with no chat history picks up a story; what was missing is added |
| 4 | A tool trial on the next 2 stories | Measured against Stage 1, token cost included |
| 5 | Petti's first stage built spec-first in its own repository | The full pilot, from intent to running |
| 6 | The decision, in an ADR | What is kept and for which size of work; `CLAUDE.md` and `docs/contributing.md` follow |

Rough size: about 5 sessions spread across real work, because the trial runs on stories being done anyway.

**Hard problems, stated up front.** A spec is not a test: only code can be run, so a requirement without a
test is a wish. AI can pad a spec with confident detail nobody asked for, and a person then has to read it.
Agents do not always follow what they were given, however long the file. Two sets of rules (`CLAUDE.md` and
a tool's own) can contradict each other, so `CLAUDE.md` wins.

**Planning standard:**

| Point | Plan |
|---|---|
| Whole path | A scratch repository, then real Kaval stories by hand, then a tool on a branch, then the full pilot in Petti's repository. Nothing about staging or production changes |
| Security | These tools install rules and hooks that steer the AI and run on the laptop: read them, pin the version, try them outside Kaval first. They never replace the gates in `CLAUDE.md`: secrets, approval at live steps, staging first |
| Failure | A wrong spec is caught by review and tests, as today. Too heavy: the stop rule above. Rollback: delete the files, because the old way still works |
| Observability | Per story: hours, pages to review, defects after merge, whether the live proof passed first time, requirements with a test, tokens used |
| Cost and capacity | $0 on AWS. The real cost is time and AI usage; heavy workflows use more, so that is measured per story |
| Testing | Unit: the requirement-to-test check, with a missing test as the negative case. Real: 3 stories, then 2. Live: the fresh-session resume |
| People | Roshan answers the AI's questions. The template lives in the repo, and the decision goes in an ADR |

### FS-10 — AIOps for Kaval

Kaval already is AIOps at its core: it watches, groups, diagnoses, proposes and waits for approval.
This adds what is missing: early warnings, learned normal ranges, a number for noise, and learning from outcomes.

Jira epic: [KAV-121](https://roshanoommenreji.atlassian.net/browse/KAV-121). Planned 2026-11-01.
**Not started until the current Kaval phase closes.** It is also Day 2 for whatever Petti builds.

**In plain words.** Gartner's definition, [as quoted by Datadog](https://datadoghq.com/knowledge-center/aiops):
"AIOps combines big data and machine learning to automate IT operations processes, including event
correlation, anomaly detection and causality determination." That means grouping alerts, spotting the
unusual, and finding the cause.

**Can we introduce it? It is already the product.** What is left is filling gaps:

| What AIOps does | Kaval today | Gap |
|---|---|---|
| Collect signals | Kubernetes events, Prometheus, a timed probe, the daily bill, a daily waste scan | No traces, and logs are not read |
| Group alerts | Rule-based fingerprints: one incident per cause (`KAV-39`) | Rules rather than learning, which has been enough so far |
| Spot the unusual | Cost: a day far above the week's median (`KAV-92`). Health: fixed limits | No learned normal for health numbers |
| Warn early | Memory near its limit (`KAV-75`), disk at 85% (`KAV-81`), AWS's budget forecast | Fixed lines: no "this disk fills in 6 hours" |
| Find the cause | Local model, runbook search, recent changes, escalation to Bedrock | No map of what depends on what |
| Propose and act | Policy (`auto`, `ask`, `never`), a person's approval, the executor, a check that the fix held | The `auto` list is empty, on purpose: nothing is born auto |
| Measure | `make mttr`, the evals, the savings report | No number for noise |
| Learn | Outcome rows record whether each fix held, and earned autonomy must cite them | Nothing turns past incidents into prevention |

**What real companies do** (checked 2026-11-01):

| Pattern | Examples | Lesson |
|---|---|---|
| Group and reduce alerts | Classic AIOps platforms, such as PagerDuty's | Noise reduction is the proven, boring part |
| AI agents that investigate, and a person approves | [AWS DevOps Agent](https://docs.aws.amazon.com/ai/responsible-ai/aws-aidevops-agent/overview.html) ([generally available 2026-03-31](https://aws.amazon.com/blogs/mt/announcing-general-availability-of-aws-devops-agent/)): read-only on your resources, with a human approval gate before any mitigation. [Azure SRE Agent](https://azure.microsoft.com/en-us/products/sre-agent): recommends or acts within guardrails and human approval. [Datadog Bits AI SRE](https://www.datadoghq.com/blog/dash-2026-new-feature-roundup-keynote/): investigation first, with a remediation step added | The same shape as Kaval's agent and executor, which is good evidence for the design |
| Statistics inside Prometheus | [Grafana's PromQL anomaly framework](https://github.com/grafana/promql-anomaly-detection): recording rules that form normal bands from the last day and the weekly pattern. [`predict_linear`](https://prometheus.io/docs/prometheus/latest/querying/functions/#predict_linear) for trends | No machine-learning service to run, which fits a 4 GB server |
| The cloud's own cost monitor | [AWS Cost Anomaly Detection](https://docs.aws.amazon.com/cost-management/latest/userguide/manage-ad.html) | Finds changes, and Kaval finds waste that never changes ([ADR-0059](adr/0059-always-on-cost-model-and-measuring-usage-before-credits.md)) |

**The recommendation: build on what exists, with statistics rather than a new model, and do not buy.**
[AWS DevOps Agent costs $0.0083 per agent-second](https://aws.amazon.com/devops-agent/pricing/). AWS's own
small-team example, 10 investigations a month at 8 minutes each, comes to $39.84, most of the $50 ceiling.
It does have a 2-month free trial for new customers, so it could be a one-off benchmark on a staging game
day, with its own go, if it is available to this account.

**Stages** (each is an acceptance criterion on KAV-121):

| # | Stage | Gate |
|---|---|---|
| 0 | Score Kaval against this table, with a baseline from real rows | Numbers for alerts, incidents, approvals, false alarms and time to diagnose |
| 1 | A second opinion on cost: AWS's monitor against Kaval's spike rule | Price confirmed first; compared for a month; result written down |
| 2 | Early warnings with `predict_linear` on disk and memory | The disk game day warns before the 85% line; a steady disk stays quiet |
| 3 | Learned ranges for health numbers, through recording rules | Replay in `promtool`; memory measured on the node; a normal swing is quiet and a game day fires |
| 4 | Noise measured and reduced | Alerts per useful proposal on the dashboard; a restart storm stays one incident |
| 5 | A map of what depends on what, from `architecture.toml` and the box file | Stays only if `make evals` scores better with it than without |
| 6 | Learning from outcomes: a monthly report of prevention suggestions | A person accepts or rejects; it changes nothing by itself |
| 7 | The first earned-autonomy decision | An ADR citing outcome rows, or a written no |

Rough size: about 8 to 10 sessions. Stages 0 to 2 are the useful first half.

**Hard problems, stated up front.** A learned normal needs history, and Kaval's servers are parked most of
the time. Grafana's bands use a day and a weekly pattern; AWS's monitor needs at least 10 days. Keeping
production on for two weeks costs about $18 (always-on is about $39 a month, ADR-0059) and needs its own
go. That is why cost and early warnings come first: they do not need it, and `promtool` can test the rules
by replay. A cluster this small also has too few incidents to train a model on, so this uses statistics.
A learned normal can be taught: a slow leak becomes "normal", so fixed limits stay alongside it. And more
alerts are not better, which is the reason noise gets a number.

**Planning standard:**

| Point | Plan |
|---|---|
| Whole path | `promtool` replay and `make evals` locally, game days on staging, then the same rules promoted to production, read-only |
| Security | Rules only read metrics, and the signals they make hold numbers and fixed words, not text. They can only raise a signal: the agent stays read-only and any fix stays in `ask`, with a person approving |
| Failure | A wrong band means a false alarm, which the noise number shows. If the rules go missing, an alert on the rules says so. Off switch: one chart value; rollback through `rollback.yml` |
| Observability | False alarms, how early each warning came, alerts per useful proposal, and the memory the rules use |
| Cost and capacity | The rules cost $0 on AWS, and their memory is measured on the 4 GB node before production. The optional two-week run is about $18. The optional benchmark is free during the trial, then about $4 per investigation at AWS's 8-minute example |
| Testing | Unit: `promtool` cases. Real: replayed metrics. Live: the disk game day. Negative: a normal daily swing and a steady disk stay silent |
| People | Roshan owns it. Each new alert gets a runbook, which also feeds the agent's retrieval. Alerts reach a person through the existing health-alert path |

---

## Verdict

For a **pure FDE role**: Kaval covers the technical foundation; FS-1, FS-2, and FS-4 are
what actually make the difference, and none of them are in v1.

For the **AI-platform role Roshan is aiming at**: Kaval is better aligned as-is — safe
agent architecture, model deployment, and eval discipline are the platform-team core.
FS-5 is the most relevant add. FS-6 (Petti) is the natural next project after it: it reuses
Kaval's propose-then-approve, policy gate and cost ceiling in a self-service "golden path" tool,
which is what platform teams build. FS-7 to FS-10 are the same direction: golden paths from code,
secure defaults, how AI-assisted teams work, and AIOps are what those teams are hired to build. FS-9
and FS-10 also improve Kaval itself, so they can come first.
