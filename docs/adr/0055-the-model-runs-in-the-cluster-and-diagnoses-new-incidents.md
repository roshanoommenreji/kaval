# ADR-0055 — The model runs in the cluster, and a separate worker diagnoses new incidents by itself

- **Status:** Accepted; proven on staging 2026-10-15 and switched on in production 2026-10-16 after the memory was
  measured again on the real node (see "Measured" and "Measured on production")
- **Date:** 2026-10-14
- **Deciders:** Roshan
- **Story:** `KAV-87` (Phase 5) · follows [ADR-0016](0016-json-schema-enforced-proposal-output.md) (the reply is
  validated), [ADR-0054](0054-telling-the-model-which-fixes-exist.md) (the model is shown one fix) and
  [ADR-0042](0042-watching-the-watcher.md) (Kaval watches itself)

## Context

Finding the problem is automatic: the collector writes signals and the agent groups them into incidents. Finding the
*fix* is not. Nothing in a cluster calls the language model today. A person runs `make diagnose` from the laptop
(with the tunnel up), or on staging a script files the proposal by hand (`chaos_propose.py`). So ADR-0054's better
prompt reaches only people who run the command, and the claim "Kaval proposes a fix for what it finds" is true in
the lab and false on the nodes.

The constraints that shape the answer:

- The app node is a 4 GB `t4g.medium`. Staging measured it at 1.99 GiB used with no model (Lab 45). The model
  (`gemma3:1b-it-qat`) needs **1,141 MiB** while loaded (`docs/architecture/model-shortlist.md`).
- The ceiling is $50 a month and the credit has about $14 of slack ([ADR-0028](0028-prod-app-node-on-demand-and-ceiling-50.md)).
- The hosted route (Bedrock) is blocked on the AWS Marketplace payment issue (`KAV-44`).
- A diagnosis takes 10 to 60 seconds, and the model may be down. Neither may stop incidents from being found, nor
  stop an approved fix from running.

## What real companies do

| Pattern | One line | Fits here? |
|---|---|---|
| A model service in the cluster, called over HTTP by a separate worker | The common shape; the worker can fail without taking the model, or anything else, with it | **Chosen** |
| A hosted model API (Bedrock and similar) | No memory on our node, pay per call | Blocked (`KAV-44`); stays the planned escalation path |
| A separate machine that wakes for an incident (a queue plus scale-to-zero) | Cheapest when idle, no neighbours to disturb | Many parts for a few incidents a month; kept as the fallback if the model does not fit |
| The model inside the agent process | One thing to run | Rejected: a 128 MiB container cannot hold it, and a slow model would stall the correlator |

## Decision 1 — the model runs on the app node, as its own pod, unloaded when idle

An Ollama Deployment (`deploy/charts/kaval/templates/ollama.yaml`, off unless `ollama.enabled`). It costs **no new AWS
resource**: it uses the node and root disk Kaval already pays for.

What keeps it from hurting its neighbours:

- A memory **limit of 1.5 GiB**. If it ever grows, the kernel stops the model, not the database client or the
  executor. The limit is below what is spare on the node; a request of 512 MiB is the scheduler's honest floor.
- It is **unloaded after 5 idle minutes** (`OLLAMA_KEEP_ALIVE`), so the 1.1 GB is a cost while diagnosing, not all
  day. The chat model and the small embedding model (all-minilm, about 60 MB) stay loaded together while in use,
  so a diagnosis does not swap them in and out.
- `Recreate`, not a rolling update: two copies would be two models.
- A CPU limit of 1.5 of the 2 vCPUs, so a diagnosis cannot take the whole machine from the services.

What keeps it from being a way in:

- No ServiceAccount token, no Secret, no database credentials, a read-only filesystem, no capabilities, not root,
  a `ClusterIP` Service only. It can generate text and nothing else.
- **The model is pinned like an image.** The files live in an `emptyDir`; a new pod downloads them (about a
  minute), and an init step compares each model's id with the pinned one and **refuses to start** on a mismatch
  (tested on the dev server: right id passes, wrong id exits 1 with `REFUSING`). Docker Hub's image is pinned by
  digest too. Tags move; a model whose weights changed upstream would otherwise be trusted by name.
- The cluster's network cannot stop another pod from calling the model (Flannel enforces no NetworkPolicy, the same
  fact `metrics.py` documents). The worst that does is spend the model pod's processor, which has a limit.

## Decision 2 — a separate worker, not a step inside the correlator

`kaval_agent/diagnoser.py` runs as its own Deployment (the agent image, a different command). The correlator stays
exactly as it was: it never calls the model, never waits for it, and cannot be slowed by it. Each pass of the worker,
in one transaction under an advisory lock (a second copy does nothing):

1. pick the **oldest** incident that is open, not older than 4 hours, not for a person, and has no proposal;
2. build its context, ask the model, write the proposal (`write_proposal`, unchanged).

The rules, each of which has a test:

- **One diagnosis per incident.** A proposal row, even one with no actions, is the record; the picker skips any
  incident that has one. A failed attempt writes nothing and is tried again.
- **Nothing for a person's incidents**: Kaval's own health, a full disk, a slow service (`kaval_shared.health`).
  `write_proposal` refuses them again.
- **Not the old ones**: an approval expires after 4 hours ([ADR-0048](0048-approvals-nobody-answers-expiry-and-escalation.md));
  a diagnosis of how things were this morning is worth less than a person looking.
- **A speed limit**: 6 proposals an hour (`diagnoser.maxPerHour`), so one flapping workload cannot keep the
  processor busy or fill Slack. The waiting incidents are visible and alerted.
- **A pause after a failure**: 30 s, doubling to 10 minutes; the model is not hammered while it recovers.

It writes only proposals. **The security picture is unchanged from before**: the worker has the agent's database role
(reads incidents and signals, writes proposals, actions and the runbook index) and no cluster or AWS credentials. A
proposal still needs policy and, almost always, a person's approval, and the executor re-checks both. A misled
model can change the words of a diagnosis, never what it may do: the fix and its target are chosen by code
(ADR-0054).

## Decision 3 — the runbooks ship in the image

`docs/runbooks/` is the corpus the model retrieves from, but it never existed inside a cluster, so the model there
would have worked from less than it did in every test. It is now copied into the agent image
(`/app/runbooks`), and the worker indexes it when it starts (idempotent: only changed sections are re-embedded).
The runbooks are versioned with the image, so staging and prod read the runbooks of the release they run, and the
build-once-promote rule covers them.

## Decision 4 — failure is loud, and does not spread

| What happens | What the system does |
|---|---|
| The model is down, slow, or answers badly twice | The incident stays open and undiagnosed; nothing is written; the worker pauses and tries again |
| An incident waits over 15 minutes | `KavalDiagnosisBacklog` fires; the poller writes a signal; a person is told in Slack |
| The worker pod is gone | `KavalDiagnoserMissing` fires (a dead pod's numbers vanish with it, so this rule asks for the missing series) |
| The worker is alive but stuck | `KavalLoopStalled` fires for `diagnoser`; its declared interval is its pass plus two model timeouts, so a slow honest diagnosis is not a stall |
| The correlator, the executor, approvals | Unaffected. Nothing else depends on the worker |

Two details: the success time moves after a pass that *tried*, so "the loop is stuck" and "the model is failing" are
two separate alerts and do not page twice for one fault; and the second rules file is only loaded where the worker
is switched on, because its `absent()` rule would otherwise page in every environment without one. Runbook:
[diagnosis-not-happening](../runbooks/diagnosis-not-happening.md).

**Off switch:** `diagnoser.enabled: false` and `ollama.enabled: false` in the environment's values file, merged by
pull request; Flux applies it. The chart refuses to render the worker without the model.

## Decision 5 — what the metrics may say

`kaval_diagnosis_total{outcome}`, `kaval_diagnosis_waiting`, `kaval_diagnosis_oldest_wait_seconds`,
`kaval_diagnosis_last_seconds`. The page is readable by every pod, so no incident id, target or error text goes on
it. `outcome` is allowed as a label only because its values are a fixed handful of words, and a test pins that.

## Decision 6 — the order of the rollout

Staging first, on in its values file from this change. Prod gets the same two lines in its own values file when the
staging numbers say the model fits, as its own pull request, then a live check on the real production node (memory
and cost measured again). Until then prod renders none of it, and a test proves that.

## Cost

- **AWS, recurring: $0.** No new machine, volume, address or service. The model's files (about 1.1 GB) sit in the
  node's existing 20 GB root volume.
- **Processor:** a diagnosis uses up to 1.5 vCPUs for 10 to 60 seconds. The node runs in the default "unlimited"
  credit mode, so CPU above its baseline is billed at $0.04 per vCPU-hour ([budget plan](../cost/budget-plan.md)).
  At the speed limit the worst case, one workload flapping every hour of the month at a full minute each, is about
  109 vCPU-hours, **at most about $4.40 a month**, and only if the node's earned credits ran out (they cover its
  baseline use first). Real use is far lower: a few diagnoses a month are cents. The figure is measured on staging
  below, and the credit line is read on the next bill.
- **Downloads:** inbound traffic is free; the model comes from Ollama's registry on every new pod.

## Consequences

- The better prompt of ADR-0054 now reaches incidents nobody ran a command for. "Proposals appear without a person
  running a command" is true on the nodes.
- A new moving part on the node with a real memory cost while loaded. If it does not fit with the measured numbers,
  the answer is a decision about the node or the separate-machine pattern, not a bigger limit.
- Registry dependence: a new model pod downloads its files, so if Ollama's registry is down while the pod restarts,
  diagnosis waits and the backlog alert says so. Baking the model into an image in our own registry removes this,
  and is a candidate if it ever happens.
- Game days change: the staging proposal is now filed by the worker. `make chaos-propose` finds it and prints its
  action (a hand-filed one is `CHAOS_BY_HAND=1`, or for the slow-service and full-disk experiments, whose point is
  that no fix should exist).
- The confidence the model states rose after ADR-0054 (`KAV-88`); now that proposals appear unattended, that matters
  more, not less. *Resolved 2026-10-16 by [ADR-0056](0056-the-stored-confidence-follows-the-evidence.md): the stored number
  is limited by the evidence.*
- A proposal waits for a person like any other. An approval nobody answers still expires (ADR-0048), and a fresh
  proposal for the same incident is not made automatically: a person runs `make diagnose`. Revisit if it happens
  often.

## Measured

On staging, 2026-10-15, build `sha-b36c4be` plus the fix of PR #170 (Lab 52, [game-day record](../postmortems/2026-10-15-oom-diagnosed-by-itself.md)).
The node is a `t4g.medium` (3,829 MB), the same size as production's.

| Question | Measured |
|---|---|
| Node memory, model not loaded | **1,640 to 1,725 MB used**, about 2,000 MB available (`free`); `kubectl top node` 1,955 MiB (51 %). The model pod itself holds 138 MiB while it has nothing loaded |
| Node memory, model loaded | **about 2,840 MB used**, available memory bottomed out at **about 780 MB** (20 % of the node). The model adds about **1.13 GB**, inside its 1.5 GiB limit |
| How long it stays loaded | 5 minutes after the last use (`OLLAMA_KEEP_ALIVE`); the node returned to about 1,540 MB the moment it unloaded. No pod restarted, nothing was killed for memory |
| One diagnosis | **36 s** for the model call (1,625 tokens read at 68 per second = 23.7 s, 102 tokens written at 15 per second = 6.8 s), against the 90 s limit. From the failure starting to the proposal existing: **about 2 min 35 s** on the first run, which includes the 60 s the correlator waits to group signals |
| Failure to recovery, nobody typing a command to diagnose | out-of-memory game day, second run: proposal filed by the worker, approved 6 s later, limit raised **200 s** after the failure began, the program stayed up |
| Processor credits | The node earned credits the whole time (balance 0 to 29.5 over 2 h 15 min) and was charged **$0** for surplus (`CPUSurplusCreditsCharged` 0 throughout). At the speed limit (6 an hour at about 1.2 vCPU-minutes each) the worst case is under 10 credits an hour against 24 earned, so the node's credits cannot run out from this alone. The $4.40 worst case above cannot happen at these settings |
| New incident, one proposal | Each new out-of-memory incident got exactly one proposal; the passes after it printed `nothing waiting`; the full-disk incident (for a person) got none |
| Model off | The model scaled to zero at 17:50:29; the incident opened at 17:51:33 and stayed open and undiagnosed; the worker counted 6 `model_error` passes and paused between tries (30 s doubling towards 10 min). `KavalDiagnosisBacklog` began at 18:07:25 (900 s over) and fired after its 2-minute hold; the poller opened `kaval_diagnosis_backlog` at 18:09:51 (staging has no Slack, so the message is the dry run). The correlator and the executor kept running throughout |
| Model back | Ready **35 s** after scaling up (both files downloaded and checked against their pinned ids). The worker's next try, 5 minutes later (it was in its pause), filed the one proposal; the alarm cleared and `kaval_diagnosis_waiting` returned to 0 |

**Does it fit on production's node?** Production measured 1,576 MB used and 2,062 MB available before this change
(Lab 50). Staging, which carries the demo namespace, measured 1,640 to 1,725 MB used idle. Adding the 1.13 GB the
model takes puts production at about **2,700 MB used**, and about **850 MB available** while the model is loaded
(staging's available memory fell by 1,220 MB, the model plus the file cache it brings, from 2,000 to 780), then back
to about 1,580 MB used five minutes later. That is enough headroom for the other services (45 to 81 MiB each) to
spike, and it is the number to re-measure on the real node before and after the switch-on.

## Measured on production

2026-10-16, build `sha-c8e3dee` (promoted through PR #174; the two switch-on lines through PR #175). Production had
been parked; it was brought up from the merged `main` (`terraform plan` shown first: 2 in place, 0 created, 0
destroyed). The model pod was ready 2 min 44 s after the node booted, and Helm's 5-minute install window held. The
number was read from `free` on the node while a one-off test job asked the model a question the size of a real
diagnosis (1,781 tokens in); no real incident was used.

| Question | Measured on production's own node |
|---|---|
| Node memory, model not loaded | **1,631 MB used, 2,007 MB available** (before this change: 1,576 / 2,062) |
| Node memory, model loaded | **2,765 MB used, 872 MB available at the worst moment** (the estimate above was 2,700 / 850). The model takes about 1.1 GB |
| Unloaded again | 6 minutes after the call: **1,593 MB used, 2,045 MB available**; `kubectl top node` 1,838 MiB |
| One model call | **33.9 s**, 1,781 tokens read in 26.3 s (the first call also loads the model, 5.3 s), 34 written in 2.0 s; the limit is 90 s |
| Pods | All nine running, none restarted; the worker idles on `nothing waiting`; `KavalDiagnosisBacklog` and `KavalDiagnoserMissing` are loaded and quiet; the only firing alert is the always-on `KavalWatchdog` |
| Processor credits | First 50 minutes after boot: credit balance 0, surplus balance 0.5 to 2.1 credits, **surplus charged $0**. A freshly booted node starts at zero and earns from there; staging did the same before its balance rose |
| Recurring cost | **$0**. Production costs about $0.044 an hour while it is up, the same as before |

The estimate held, and the switch-on stays. Nothing was changed from staging's settings. If the memory had come out
worse than about 600 MB free the two lines would have gone back to `false` and the numbers would have gone to
Roshan (a bigger node is about $24 a month more, which breaks the $50 ceiling).

The same session found that the staging smoke test read a running image's tag name instead of its bytes (a node names
an image by the first tag it pulled, so two byte-identical builds look like the older one). Fixed in PR #173 and
noted in ADR-0031.

**Found by running it, fixed in PR #170:**
- The worker's database role could not read `outcome`, which the context builder reads once a similar past incident
  exists. The first diagnosis worked (no history yet); the next incident with history crashed the worker (it
  restarted, found nothing to do, and idled). Nothing else was affected and the restart count was the signal. A
  test now lists every table the worker reads and that the role cannot write outcomes. The laptop never showed it
  because the laptop uses an administrator's database account.
- `CHAOS_BY_HAND=1` never reached the pod that files the proposal. A test now pins it.
- Staging gives an approval 15 minutes (`approvals.expireMinutes`); a proposal approved after 17 minutes was refused
  as expired, as designed, and the worker did not make a second one, as the consequence above says.
