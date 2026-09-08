# Phase 6 — Chaos and proof

> **Written from:** theory
> **Lab:** to be written
> **Cost:** no change — posture still **paused between sessions**

## Where this sits

Phases 1–5 built a system that *should* detect, diagnose, propose and remediate. This phase finds
out whether it does.

It unlocks: every claim the project makes becomes a measurement. Without this the resume line is
"built an autonomous remediation agent." With it, it is "sub-five-minute MTTR across five injected
failure classes," which is a different kind of sentence.

## What we're doing

- Chaos CronJobs injecting five failure classes into a namespace labelled `kaval.io/chaos=enabled`
- Blast-radius containment, tested before the experiments are trusted
- MTTR measured from signal to verified resolution
- A **runbook per failure class** — which is also the agent's retrieval corpus

## Why this way

**Because a recovery path you have not exercised does not work.** This is the same principle as
Phase 0's untested budget alarm, applied to the whole system. Every backup that has never been
restored, every failover that has never been triggered, every runbook nobody has followed — these
are hopes wearing the costume of controls.

The rejected alternative is waiting for real failures. Real failures arrive at inconvenient times,
in unrepeatable forms, and teach you one thing each. Injected failures arrive when you are
watching, in the same form every time, and can be run again after a fix.

---

## Key concepts

### Chaos engineering is an experiment, not vandalism

The discipline came out of Netflix and has a precise definition. It is not "break things and see."
It is:

1. Define **steady state** — a measurable property of normal operation
2. **Hypothesise** that steady state persists through a specific disturbance
3. Introduce the disturbance in the smallest scope that tests the hypothesis
4. **Measure** whether steady state held
5. If it did not, you have found a real weakness before it found you

The hypothesis is what makes it an experiment. "I think the agent detects an OOM kill within 30
seconds and proposes a memory-limit patch" is falsifiable. "Let's kill a pod" is not.

A failed hypothesis is a **success** — it is the entire point. An experiment that always passes is
either testing something trivial or not testing what you think.

### Steady state is a property, not a status

Steady state must be measurable and about *behaviour*, not internals. "All pods running" is a weak
steady state — it describes the system's internals. "Incidents are detected within 30 seconds and
resolved within 5 minutes" describes what the system *does*.

For Kaval:

| Property | Steady state |
|---|---|
| Detection | Signal recorded within 30s of the fault |
| Diagnosis | Valid proposal produced within 90s |
| Remediation | Approved action executed within 30s |
| Verification | Outcome recorded as resolved within 5 min total |
| Safety | Zero `never`-class actions proposed |

The last row is the one that must never regress, and it is not about speed.

### Blast radius containment, tested first

The chaos tooling can break things. Therefore the chaos tooling is itself dangerous, and its
containment must be verified before it is trusted.

Layered:

- Experiments target only namespaces labelled `kaval.io/chaos=enabled`
- The chaos ServiceAccount is bound with a **RoleBinding** in that namespace, not a ClusterRoleBinding
- Production-critical workloads are not in that namespace
- Every experiment has an explicit abort condition and a manual kill switch

**Test the containment before the experiment.** Confirm the chaos ServiceAccount cannot delete a
pod in `kube-system` — the `kubectl auth can-i` idiom from Phase 3 — and confirm it before you
run anything that kills pods.

### The five failure classes, and what each proves

| Experiment | Fault injected | What it actually tests |
|---|---|---|
| `oom-kill` | Container exceeds its memory limit | Event ingestion, resource-limit reasoning, patch action |
| `crashloop` | Bad command in a container | Distinguishing a config error from a resource problem; rollback action |
| `disk-fill` | Volume filled to 95% | Metric-threshold signals rather than events; cleanup action |
| `latency` | Injected network delay | SLO-breach detection; and correctly proposing *no* automated action |
| `node-drain` | Simulated spot reclamation | The Phase 4 rebuild path, under a controlled clock |

The `latency` case is the interesting one, because **the correct behaviour is to investigate and
not act**. A system that always proposes a remediation is a system that will eventually propose a
bad one. Knowing when the answer is "escalate to a human, no automated action available" is a
capability, and it needs testing like any other.

### MTTR, and what the average hides

**MTTR** — mean time to *repair*, *recovery*, *resolve*, or *respond*, depending on who is talking,
which is the first problem with the metric. Define which you mean. Here it is signal to verified
resolution.

The second problem is the mean. Incident durations are heavily skewed: many fast recoveries and a
few catastrophic ones. An average of 4 minutes might be forty incidents at 90 seconds and one at
two hours — and the two-hour one is the one that mattered.

Report the **distribution**: median, p90, worst. And break it into stages, because the stage
totals tell you where to spend effort:

```
fault ──▶ detect ──▶ diagnose ──▶ notify ──▶ approve ──▶ execute ──▶ verify
        30s        60s          5s        [human]     10s        60s
```

Note that the human stage is unbounded and usually dominates. That is not a defect — it is the
architecture working as designed — but it means "MTTR" for this system is really two numbers:
machine time, and wall-clock time including the human. Reporting only the flattering one would be
dishonest.

### MTBF, and why it is the weaker metric

**MTBF** — mean time between failures — measures how often things break. MTTR measures how quickly
you recover.

For most modern systems MTTR is the more actionable metric. You have limited influence over
whether a dependency fails; you have considerable influence over how fast you notice and recover.
This is why the industry shifted from "prevent all failure" to "recover fast" — and it is
precisely the shift that makes chaos engineering sensible rather than reckless.

### SLI, SLO and error budgets

Google's SRE vocabulary, and worth using precisely:

- **SLI** — Service Level *Indicator*. A measurement. "Proportion of incidents detected within 30 seconds."
- **SLO** — Service Level *Objective*. Your internal target for that SLI. "99% of incidents detected within 30 seconds."
- **SLA** — Service Level *Agreement*. A contractual promise with financial consequences. This project has none.

The **error budget** is the inverse of the SLO. A 99% objective permits 1% failure — and that
budget is a resource to *spend*, not a shame to avoid. Chaos experiments consume error budget
deliberately, buying information with it.

The organisational insight, which is worth carrying into a release-management role: an error
budget converts an argument about risk appetite into an arithmetic question. "Can we ship this
risky change?" becomes "do we have budget left this month?"

### Runbooks that do work

Each experiment's runbook serves two readers: a human at 2am, and the agent's retrieval step.

That dual purpose disciplines the writing. Retrieval works on semantic similarity, so a runbook
must contain the *symptom language that will actually appear* — the event reason, the metric name,
the log substring — not just a tidy prose description.

Structure:

```markdown
# <Symptom as an operator would see it>

## Signals
Event reasons, metric names, log patterns. The literal strings.

## Likely causes
Ordered by frequency, not by interest.

## Diagnosis
Commands that distinguish between the causes above.

## Remediation
The fix. Each action marked reversible or not, with its blast radius.

## Do not
Actions that look right and make it worse.
```

The **Do not** section is the most valuable and the most often omitted. It is where hard-won
knowledge lives, and it is exactly what stops a plausible-sounding proposal from being a bad one.

---

## Common mistakes

| Mistake | What it costs |
|---|---|
| Running chaos without a hypothesis | You learn nothing repeatable; it is just an outage you caused |
| Trusting containment before testing it | The chaos tooling becomes the incident |
| Reporting mean MTTR alone | The skew hides the incidents that actually hurt |
| Quoting machine-only MTTR when a human is in the loop | Flattering and dishonest |
| Treating a failed experiment as a failure | It is the point; you found a weakness cheaply |
| Building a system that always proposes an action | It will eventually propose a bad one |
| Runbooks written in tidy prose without literal symptom strings | Retrieval misses them; the agent is left guessing |
| Omitting the "Do not" section | The most valuable knowledge stays in your head |
| Treating the error budget as something to protect at all costs | You stop shipping and stop learning |

## Glossary

| Term | Meaning |
|---|---|
| **Chaos engineering** | Experimenting on a system to build confidence in its behaviour under turbulence |
| **Steady state** | A measurable property of normal operation, expressed as behaviour |
| **Hypothesis** | A falsifiable prediction that steady state survives a disturbance |
| **Blast radius** | The scope an experiment or action can affect |
| **Abort condition** | The predefined trigger for stopping an experiment |
| **Game day** | A scheduled session of running experiments, often with a team |
| **MTTR** | Mean time to repair/recover/resolve — define which you mean |
| **MTBF** | Mean time between failures |
| **p90 / p99** | The value below which 90% / 99% of observations fall |
| **SLI** | A measured indicator of service behaviour |
| **SLO** | An internal target for an SLI |
| **SLA** | A contractual commitment with consequences |
| **Error budget** | The failure allowance implied by an SLO; a resource to spend |
| **Toil** | Manual, repetitive operational work that scales with load |
| **Postmortem** | Structured analysis after an incident |
| **Blameless** | A postmortem culture examining systems rather than assigning fault |

## Check yourself

1. What separates a chaos experiment from an outage you caused?
2. Why is "all pods running" a weak steady state? Write a stronger one.
3. Your MTTR averages 4 minutes. Why might that be a reassuring number hiding a serious problem?
4. Why must the chaos tooling's blast-radius containment be tested before the experiments themselves?
5. For the latency experiment, the correct outcome is *no automated action*. Why is that a capability worth testing rather than a gap?
6. Explain error budgets to a release manager in two sentences, and say what they change about a go/no-go conversation.
7. Why does a runbook need the literal event reason string in it, when a human reader would understand a paraphrase?

## In an interview

**"You said sub-five-minute MTTR. How did you measure it, and what does that number leave out?"**

> "Signal timestamp to verified-resolution timestamp, across five injected failure classes — OOM
> kill, crashloop, disk pressure, latency injection and a simulated spot reclamation. I report
> median, p90 and worst rather than the mean, because incident duration is skewed and an average
> hides the one that actually hurt. What it leaves out is the honest part: there's a human
> approval in the middle, so there are really two numbers — machine time, which is about two
> minutes, and wall-clock including me, which depends on whether I was asleep. Quoting only the
> first would be dishonest. The experiment I'm proudest of is the latency one, where the correct
> behaviour is to detect the SLO breach and propose *no* automated action. A system that always
> has a remediation will eventually have a bad one."

Volunteering what the number excludes is what makes the number believable.

## Further reading

- *Principles of Chaos Engineering* (principlesofchaos.org) — short, and the source of the definition
- Casey Rosenthal & Nora Jones, *Chaos Engineering* (O'Reilly)
- Google, *Site Reliability Engineering* — chapters on SLOs and error budgets, freely available
- Google, *The Site Reliability Workbook* — the practical SLO-setting chapter
- John Allspaw, *Blameless PostMortems and a Just Culture*
