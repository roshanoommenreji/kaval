# Course outline

Working title: **An AI Ops Agent You Can Trust, on a Hobby Budget**

This file is a record, not a script. Every lab in `docs/labs/` is a candidate episode and every
ADR is a candidate "why" segment. It was first written before the work (2026-08) and brought up
to date with all 101 labs (Lab 00 to Lab 100) on 2026-11-15 (`KAV-158`); Labs 101 to 103 were added on 2026-11-16 and 2026-11-17. What changed against the
first version is listed at the end, because the differences are part of the story.

---

## Why anyone would watch

Most AI-infrastructure content is either a toy on a laptop or an enterprise demo on someone
else's budget. This sits between: a real system, on real AWS, doing real work, under a $50
ceiling and usually for a few dollars a month, where the cost limit is the teaching method
rather than an apology.

The other differentiator is the guardrail story. Plenty of tutorials hand an LLM a shell.
Almost none show how to bound one: an agent that can only write a proposal, a separate executor
with scoped rights, a policy engine, a person approving in Slack, and a cluster that refuses an
image it cannot verify.

---

## Structure

Roughly one episode per lab or small group of labs, grouped by part. Each has the same shape:

1. What we are trying to do, and why now
2. The decision, with the alternative that was rejected and the number that decided it
3. The build
4. What broke
5. What it costs

Segment 2 is the differentiator. Segment 4 is what makes it trustworthy.

---

## Episodes

Lab numbers are in brackets. A part is a season; an episode is one bullet.

### Part 1 — Foundations (Labs 00–08)

- The toolchain, and why the budget alarm is built before any server [00, 01]
- The data model: signals, incidents, proposals, decisions, outcomes [02]
- A dev server in the cloud that stops itself, and the stack on it [03, 04]
- A gateway and fake incidents to feed it [05]
- CI from day one, commits tied to Jira, and what "accepted by the user" means [06, 07, 08]

### Part 2 — The agent (Labs 09–14, 51–53)

- Many signals, one incident: correlation and fingerprints [09]
- What the model is shown: runbooks, history and recent changes [10]
- Making an LLM return JSON it cannot wriggle out of [11]
- The policy engine: `auto`, `ask`, `never`, and why everything starts at `ask` [12]
- Evals: 20 golden incidents, and how you know the thing is not just confident [13]
- A bigger model for the hard cases: built, tested, and blocked by the account (an honest dead end) [14]
- Teaching a 1B model the fixes, moving it into the cluster, and confidence that follows the evidence [51, 52, 53]

### Part 3 — Kubernetes (Labs 15–18)

- One Helm chart, and a real cluster on a laptop [15]
- The privilege split: why the thinking half has no hands [16]
- Real Kubernetes events instead of fake ones [17]
- Rehearsing a promotion before there is anything to promote to [18]

### Part 4 — Landing on AWS (Labs 19–31)

- Network, registry, roles and one server, with no NAT gateway and no load balancer [19]
- GitOps: the cluster builds itself from the repository [20]
- Approval in Slack with no inbound port [21]
- A database on its own server, and secrets that come back after a rebuild [22, 23]
- Stopping safely: a snapshot before every stop, a nightly auto-stop, a start-up health check [24, 25, 26]
- A restore drill, and a full down-and-up drill [27, 28]
- Staging you can afford: built on demand, parked when idle [29, 30]
- The exit test: end the server by hand and time the rebuild (3 min 36 s) [31]

### Part 5 — Release engineering (Labs 32–42, 54, 83, 90)

- Build once: five images, scanned, pushed by a login that lasts one job [32]
- A smoke test that writes down what passed [33]
- The promotion gate, and a guard against hand edits [34]
- Rollback, measured: 80 seconds, and the migration that stalls it [35]
- Versions worked out from commits, and a change record nobody writes by hand [36, 42]
- Keeping the registry small [37]
- A dependency upgrade done properly [38]
- Staging data from a cleaned copy of production [39, 41]
- The nightly dump, taken by the server itself [40]
- A new version on an old database [54]
- Only a tagged release can be promoted, by version number [83, 90]

### Part 6 — Proof (Labs 43–50)

- Prometheus as a signal source, and watching the watcher [43, 45]
- Recording whether a fix held [44]
- Breaking things on purpose: memory, a bad release, a lost server, a full disk, a slow service [46, 47, 48]
- People and process: approvals nobody answers, backups nobody checks [49]
- Closing the phase: measuring time to restore honestly [50]

### Part 7 — Money (Labs 55–64)

- Terraform's record off the laptop [55]
- Reading the bill before the credits hide it [56]
- A cost reader with a login that can only read the bill [57]
- Labels on everything, and a bill split by them [58]
- A daily waste scan that reports to a person [59]
- Removing waste safely: two approvals, a wait, a separate worker [60]
- A brake that stops spending at the ceiling [62]
- Production for real, the savings counted honestly, and the phase closed [61, 63, 64]

### Part 8 — The same chart on EKS (Labs 65–70, 76)

- Planning a lab cluster before spending anything [65]
- Two live sessions and nine findings [66]
- Pods that pass the restricted standard, and the first install that waited on itself [67, 69, 70]
- Only one gateway copy notifies at a time [68]
- What Flux does when its retries run out [76]

### Part 9 — Hardening in public (Labs 71–82, 84–89)

- A security policy, owners and a weekly scorecard [71]
- A threat model, and a prompt-injection test set [72]
- Fixing what it found: a login on the approval route, escaped and labelled model text, a decision written once [73, 84, 85, 86, 87]
- Tools that fail safely: a sync that cannot hang, a login that never follows a redirect, retries on downloads [74, 75, 82]
- Passwords that stay out of logs, and backups that belong to the right environment [77, 78]
- Building a dependency ourselves when its scanner findings will not stop [79, 80]
- Code scanning: turning it on and reading what it says [81]
- Packing our own code with a hash-checked tool, and a dump at the moment production is parked [88, 89]

### Part 10 — A supply chain you can check (Labs 91–99)

- A parts list and a signed build record for every image [91]
- Signing every image, and hiding the account number in public logs [92, 93]
- The promotion gate checks the signature [94]
- The cluster checks, then refuses, then production refuses [95, 96, 97]
- The alarm's blind spot after a restart [98]
- The exception list that never worked, and two "passes" that were not passes [99]

### Part 11 — Who is allowed to do what (Labs 100 to 103, more to come)

- The cluster half of an access review, from the chart [100]
- The AWS half: every role, what AWS cannot narrow, the missing analyzer, the brake that was too wide, and why a boundary goes on two roles and not thirteen [101]
- Taking away a login nothing uses, and a test that stops it coming back [102]
- A ceiling over the two roles that act: a permission boundary, proved with a role widened to everything and checked against IAM's own simulator [103]
- The rotation schedule and the ownership page (`KAV-155`)

---

## What is still to be written

- **Episode scripts** (`episode-scripts/` is empty). Not planned for v1.
- **The closing episode:** the retrospective from Jira and the journal, written 2026-11-16 (`KAV-158`): [retrospective-v1.md](../retrospective-v1.md).
- **A demo video** would be the trailer for all of this. Its script is written (`KAV-157`); the
  recording is parked in future scope (FS-2).

---

## What changed against the first outline

| First outline (August 2026) | What happened |
|---|---|
| "Ship an AI Ops Agent on $14 a Month" | The ceiling went from $25 to $40 to $50 as the database got its own server and the app server left Spot (ADR-0008, ADR-0028). Real spend stays low because production is parked; "$14" was a guess |
| "Part 4 — The phone": push notification to approval in one tap | No phone app was built. Approval moved to Slack in Phase 4 and the app was retired (ADR-0026, ADR-0097) |
| "Earning autonomy: promoting an action from `ask` to `auto`, with the data" | Not done. Every action is still `ask` or `never`; the risk rating that would feed it is parked (ADR-0098) |
| "Spot instances, and building for a machine that can vanish" | Built, then left: Spot capacity ran out twice and the app server went On-Demand. The self-rebuild stayed and is the Phase 4 exit test |
| Six parts | Eleven. Release engineering, hardening and the supply chain were not in the first outline at all, and are half the labs |

---

## Raw material

- `docs/learn/` — **the concepts**; one page per phase, already structured as episode content
- `docs/labs/` — the reproducible steps
- `docs/adr/` — the arguments
- `docs/journal/` — what actually happened, including the wrong turns
- `docs/postmortems/` — the game days, one page each
- `docs/cost/` — the plan and the real monthly numbers

The journal is the most valuable of these and the easiest to skip. Wrong turns are the part
viewers cannot get anywhere else.
