# ADR-0060 — The cost reader: the server asks AWS for the bill, the cluster never holds an AWS login

- **Status:** Accepted. Built and tested on the laptop; the live proof on staging and production is recorded in
  [Lab 57](../labs/lab-57-the-cost-reader.md) as it happens
- **Date:** 2026-10-18
- **Deciders:** Roshan
- **Story:** `KAV-92` (Phase 6) · builds on [ADR-0059](0059-always-on-cost-model-and-measuring-usage-before-credits.md)
  (decision 2: built in-house, capped, read-only, separate from the executor) ·
  [ADR-0053](0053-the-still-alive-alarm-lives-in-aws.md) (the same "the server does it, no pod holds a login" pattern)

## Context

Phase 6 needs the AWS bill as signals Kaval can reason over. ADR-0059 fixed the rules (in-house, read-only, a
call cap, a failed pull must be loud). It left one question open: **how does the reader get permission to see the
bill?** Today no program in the cluster can talk to AWS at all, on purpose: the server's role cannot be reached
from a pod (IMDSv2 only, one network hop, [ADR-0021](0021-the-executor-scoped-rbac-and-the-approval-write-path.md)). Whatever
we choose becomes the first AWS access a Kaval program ever has, so it sets the pattern for the cost actions of
`KAV-95`.

Three facts from the recorded bill (pulled 2026-10-18, 14 days, Lab 57) shaped the rest:

1. **The newest day is always partial.** The 17th read $0.12 against about $0.70 for a normal day, and every day
   of the current month carries `Estimated: true`, so that flag cannot tell a partial day from a settled one.
2. **Most services are always zero.** 18 services, 7 with any spend in the window.
3. **A ratio alone is the wrong spike test on small money.** A service at 8 cents that costs 30 once is "3.75
   times the usual", and nobody should be woken for 22 cents.

## What real companies do

| Pattern | What it is | Verdict here |
|---|---|---|
| Pod identity (IRSA on EKS, Pod Identity, IAM Roles Anywhere elsewhere) | Each program gets its own short-lived AWS role from the cluster | The right end state. On k3s it needs an OIDC issuer published somewhere AWS can reach, or a certificate authority: real moving parts for one daily call. Revisit with `KAV-95`, when the executor needs a write role |
| The server's role, shared by every pod | Raise the metadata hop limit so pods can use the instance role | Simple, and every program in the cluster (a misled one included) could then use everything the server's role can: images, secrets, backups. Rejected |
| A long-lived access key in a Kubernetes Secret | A key made for the job | Never expires, readable by anything that can read the Secret, and the pattern this project has avoided since Phase 4. Rejected |
| A scheduled function outside the cluster | A Lambda with its own role reads the bill | It cannot reach the database without a paid network add-on (about $7 a month) or a public database. Rejected |
| **The server's own job, with a separate read-only role, handing the answer to the cluster** | A timer on the server borrows a role that can only read costs, asks once, leaves the answer in one ConfigMap | **Chosen.** The same pattern as the still-alive alarm: no program in the cluster holds a login, and the one that has a login has one verb |

## Decisions

1. **The server asks, the cluster reads.** A timer on the server (`kaval-cost-pull`, in the node's start-up script)
   borrows `kaval-<env>-cost-reader`, a role whose only permission is `ce:GetCostAndUsage`, asks Cost Explorer once,
   and writes the answer into one ConfigMap, `kaval-<env>-cost-pull`. A new Deployment, `collector-cost`, may `get`
   that one ConfigMap by name and turns it into signals. It holds no AWS login of any kind; a test renders the chart
   and fails if the words appear. Only that environment's server can borrow the role (the trust names the server's
   role exactly), and the role is separate from the executor's future cost actions: looking and acting never share
   a login.
2. **Only settled days are recorded.** A day is written once it is at least two days old, once per service, never
   twice (de-duplicated against what is already stored). Zero and rounding dust write nothing. Signals are
   append-only, so a partial day written early would stay as a false drop forever. The cost of this: a spike is
   noticed about two days after it started, which suits a bill that lags a day anyway.
3. **One request a day, capped at 60 a month, counted before it is made.** One request asks for the last 14 days
   at once (a first run gets its history, a repeat costs nothing extra). A good answer less than 20 hours old means
   no request at all, so the timer can try every three hours and a failed try is retried soon without a second
   request being billed. The count is kept on the server, **so a rebuilt server counts again from zero**: that
   costs cents (the cap is $0.60 a month, each request is a cent) and the budget alarm is the backstop. The cap was
   chosen as the figure ADR-0059 wrote; it is a test, not a hope.
4. **A failed pull is loud, in three ways.** The pull failing for four hours (one failed try that the next one
   fixes is not worth a message), no good pull for 36 hours (catches a pull that never runs), and no reader running
   at all. All three are Prometheus rules, tested with promtool, and a person is told straight from the signals.
   The server tells the cluster only a status and one of three fixed words (`assume_role_failed`, `ce_call_failed`,
   `capped`): AWS's own error text carries account details and the health page is readable by
   every pod.
5. **A cost spike needs a rise in dollars as well as a ratio.** The existing rule (three times the median of the
   week before) now also needs the day to be at least 25 cents above that median (`MIN_SPIKE_RISE`). Run against
   the recorded real bill it opens nothing; without it, a 20-cent move on the compute line was a "spike". A real
   one (the usual compute day of about 20 cents becoming $2.40) is still found, and a test says so.
6. **The reader sees the whole account for now.** Cost Explorer's figures are account-wide, including the other
   project that shares it (`stock-trader`). The signals say what AWS says. What Kaval may *do* about any of it is a
   separate fence ([ADR-0059](0059-always-on-cost-model-and-measuring-usage-before-credits.md) decision 5: only
   resources carrying Kaval's tag) and filtering the bill by tag arrives with `KAV-93`.
7. **Rolled out in order, switched on by one line.** The chart part is off everywhere (`costReader.enabled`); staging
   turns it on once a build containing it is pinned there; production at its next bring-up, with the plan shown first.

## Consequences

- **Cost:** about 31 requests a month, **$0.31**, at most $0.60. No new server, no new managed service. The reader's
  own requests appear in the bill as "AWS Cost Explorer" (10 cents on 2026-10-16 from the tests that built this),
  which is how a runaway would show.
- **Memory:** one small pod, limited to 128 MB, measured on staging (Lab 57).
- **Size limit:** the node's start-up script grew by about 3 KB. Packed, it is now about 15.3 KB of the 16 KB the
  server allows; a test fails before the next addition does.
- **A rebuilt server counts requests again from zero** (decision 3). Accepted.
- **The first AWS login of any Kaval program is one verb, borrowed by the server's job.** `KAV-95` has to choose
  again for write actions; pod identity is the likely answer and this decision does not block it.
- **Reversal:** set `costReader.enabled` to false and remove the `cost_reader` module; the timer is part of the
  server's start-up script and stops with the next rebuild (or `systemctl disable --now kaval-cost-pull.timer`).

## Not decided here

- Switching on AWS Cost Anomaly Detection as the second opinion (ADR-0059): its price still has to be confirmed in
  the console, then it is a console action recorded in Lab 57.
- Tag-filtered figures and a show-back report (`KAV-93`), waste scans (`KAV-94`), proposals (`KAV-95`).
