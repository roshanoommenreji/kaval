# Game day: <experiment> — <date>

Copy this file to `docs/postmortems/<YYYY-MM-DD>-<experiment>.md` **before** starting, fill in the first
half, run, then fill in the second half the same day. Blameless: it describes what the system did, not
who was at fault. The person running it is also the person on call, so the page says so.

## Before (written first, not after)

| | |
|---|---|
| **Experiment** | e.g. `oom`: a program that needs ~96 MiB started with a 64 MiB limit |
| **Environment** | staging only. Never prod |
| **Who is watching** | name; and who to tell if the run goes wrong |
| **Hypothesis** | One sentence that can be false. e.g. "Kaval opens an incident within 5 minutes, a proposal to raise the memory limit reaches me, and after I approve, the program stays up for 5 minutes" |
| **Steady state** | What "normal" looks like right now: pods Running, no open incidents, `make staging-status` clean, node memory % |
| **What the run may touch** | Only the demo namespace. Name the objects it will create |
| **Abort if** | Any of: an incident appears for a Kaval service itself; the node's memory passes 85 %; a pod outside the demo namespace restarts; the run passes 25 minutes. Abort = `make chaos-stop` |
| **Kill switch** | `make chaos-stop` (switch off, Jobs and the victim deleted). Second route: delete the victim Deployment by hand over Session Manager |
| **Cost** | staging up for the session: about $0.045/hr |
| **Model** | none on staging: the diagnosis is a proposal filed by hand, and the page says so. Or: dev server, real model |

## During (times as they happen)

| Time (UTC) | What was seen | Where |
|---|---|---|
| | victim created | runner log |
| | first OOM kill | runner log / event |
| | incident opened | `make mttr` / incidents |
| | proposal filed | |
| | approved | |
| | executed | |
| | outcome recorded | |

## After

| Question | Answer |
|---|---|
| Was the hypothesis true? | yes / no / partly |
| Detect, diagnose, human, queue, execute (seconds) | from `make mttr` |
| Did the fix hold five minutes? | the `outcome` row |
| Did anything happen that the hypothesis did not predict? | |
| Did the alerts that should be silent stay silent? | |
| Node memory with the victim running | measured, not guessed |
| Was everything cleaned up? | `make chaos-status` shows no Jobs and no victim; switch `false` |

### What we learned, and what changes

One list. Each item says what will change (code, a threshold, a runbook, a roadmap line) and where it
is tracked (Jira key) — or says "nothing, because …". A lesson that only lives in this page is lost.

### Service levels

Do the numbers meet [service levels](../docs/architecture/service-levels.md)? If not, is the target
wrong or is the system?
