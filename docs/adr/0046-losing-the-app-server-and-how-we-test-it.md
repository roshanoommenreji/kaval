# ADR-0046 — Losing the app server, and how we test it

- **Status:** Accepted — proven live on staging (see the [post-mortem](../postmortems/README.md))
- **Date:** 2026-10-11
- **Deciders:** Roshan
- **Story:** `KAV-80` (Phase 5, slice 4) · roadmap line "node drain" / "lost server"

## Context

Kaval runs on one machine (the app node, a single k3s server on a Graviton instance in an
auto-scaling group of size one). Its database lives on a **separate** server. "Lose a server" can
mean either, and the answers are very different. Kaval also **runs on the machine it watches**, so
the experiment where that machine disappears cannot be an in-cluster Job like the others: the Job
would die with the cluster.

## The options

| Option | What it tests | Verdict |
|---|---|---|
| **A. Lose the database server** (stop the instance) | Kaval can neither read nor write. The right response is an alert to a person and **no automatic action**: you never auto-restart a database. The recovery is a person starting it again | Mostly the concern of `KAV-82` (database monitoring, the scheduled restore drill). Not run here |
| **B. Lose the app node** (end the instance; the group replaces it) | The self-heal: a new machine, k3s, Flux, every pod, and the data survives because it is on the other server. Real cause in the field: a spot reclaim, a hardware fault | **Chosen.** This is Kaval's real single point of failure and the one failure that can happen to it for free |

What real companies do for this: a **managed fault-injection service** (AWS Fault Injection
Service) or a scripted "terminate an instance" drill in a game day (the Chaos Monkey lineage).
Fault Injection Service is billed per action-minute and adds IAM roles for one command we can run
ourselves with the AWS CLI; revisit if more host-level faults are wanted (network blackhole, disk
full on a host).

## Decision

1. **A laptop-run script, not a Job** (`scripts/ops/chaos_node.py`, `make chaos-node`). The cluster
   cannot test its own death from inside.
2. **The guard lives in the script**, because there is no Kubernetes switch to lean on:
   - it picks only the instance tagged `Name=kaval-staging` that is in the auto-scaling group
     `kaval-staging` (the group name is the identity; the group passes only `Name` and `Project` to its machines, so an `Env` tag cannot be relied on, which the first live attempt found), and refuses on anything else (prod's are `kaval-prod`);
   - it refuses when more than one staging machine runs, rather than guess;
   - it refuses to start if Kaval on staging is **not healthy to begin with** (the run would measure
     nothing);
   - you must **type the machine's id** back; nothing is ended otherwise.
3. **The machine is ended through the auto-scaling group**, `--no-should-decrement-desired-capacity`,
   so the group sees a lost machine and launches a replacement by itself. Ending the instance
   directly also works but leaves the group's health check to notice; the group call is the
   deterministic version. The replacement is the thing being tested.
4. **What it measures** (seconds from ending the machine): old machine gone, new machine running,
   Session Manager reachable, cluster node Ready, every pod ready (at least as many as before), the
   gateway healthy (which includes reaching the database server), and the **incident seen before the
   loss still there** (data intact). It gives up after 30 minutes.
5. **It cannot be aborted** once the machine is ended. The control is the guard before it.
6. **Cost:** a new staging machine for the minutes the replacement takes: cents. No new resource.

## Consequences

- **Kaval cannot raise an alarm about its own death.** While the machine is gone nothing watches and
  nothing can send a message. The run's most useful number is how long nobody would know. The answer
  to that is an **outside** "still alive" check (a small thing running somewhere else that shouts when
  Kaval goes quiet); it is already planned in `KAV-83` and this run gives it a real figure to design
  against. The runbook says so plainly: [`node-lost.md`](../runbooks/node-lost.md).
- Approvals waiting for a person survive (they are rows in the database server), and so does the
  executor's own protection against acting twice (one decision per action, one execution per
  action), but this is only as good as the live check on the day.
- A spot reclaim is the same event from the node's point of view; this does not simulate AWS's
  two-minute warning, only the loss.
- Prod: nothing runs on prod. The script's guard makes that a refusal, tested.
