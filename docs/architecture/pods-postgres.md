# Inside the cluster — the 5 workers, and the filing cabinet they share

> This page explains exactly what's shown by running `kubectl get pods -n kaval-prod` on the live system, and what's inside the Postgres database every one of those pods talks to.

## The flow, in one picture

Tap any box for exactly what it reads, what it writes, and which tables it can touch.

<!-- diagram -->

## Kubernetes, in one picture

Think of the app node (from [AWS, plain and simple](#aws-tour)) as a small factory floor. On that floor, Kubernetes is the floor manager. It doesn't do any of the actual work — its only job is making sure the right workers are standing in the right spots, and replacing one instantly if it ever stops responding.

Each worker lives in its own sealed little box, called a **pod**. A pod has exactly one program inside it, nothing shared with its neighbours. If a worker's box ever goes quiet, the floor manager doesn't try to fix the worker — it just throws the box away and builds a brand new one from the same recipe. That's why the real screenshot shows **0 restarts** next to every pod: none of these five have ever needed replacing since they started, roughly half an hour before the screenshot was taken.

## The 5 workers, one at a time

### `kaval-prod-collector` — the scout

Its only job is watching for trouble and writing down exactly what it saw, with zero opinions. Right now it watches Kubernetes' own built-in events (a pod crashing, a container failing to start) and copies each one, untouched, into one row of the `signal` table below. It never decides anything and never acts on anything — it only observes.

### `kaval-prod-agent` — the detective

The thinker. It takes the scout's raw observations, groups the ones that look like the same problem into one `incident`, and then asks a small AI model that runs right there on the same computer (Gemma 3 1B — small enough to run without a GPU) a very specific question: "given this incident, and given our written runbooks for problems like it, what's wrong and what should we do?" Its answer is forced into a strict, checkable shape before it's ever trusted — not free-form rambling — and gets written down as a `proposal` plus one or more concrete `action`s.

### `kaval-prod-executor` — the only hands

The only one of the five ever allowed to actually touch and change anything — restart a pod, scale something up, and so on. Critically, it never decides *whether* to act; it only carries out actions that have already been approved, either by a real human tapping a button, or by a pre-agreed rule for very safe, reversible cases. It writes down exactly what it did and the before/after state, in `execution` and `outcome`.

### `kaval-prod-gateway` — the front desk (×2)

The only one of the five that talks to actual people. It serves a small web API, and it's the one that posted the Approve/Deny Slack buttons for a real incident and reacted to the click. You'll notice **two** gateway pods in the screenshot, not one — that's deliberate: it's the single piece a human is actually waiting on, so it runs twice for reliability. If one copy is busy or briefly unhealthy, the other is already there to answer.

### Two more workers you won't see running

Two extra pods exist but finish and vanish on their own, which is why they're never in a "running pods" screenshot: one sets up the filing cabinet's folders for a brand-new database (runs the database migrations), and the other hands out the right keycards (creates the four database roles below) — both run once per release, do their one job, and exit cleanly.

## Postgres — the one filing cabinet everyone shares

All five workers above talk to the exact same database: one Postgres server, running on its own dedicated computer (see [AWS, plain and simple](#aws-tour)), reached over a locked, encrypted connection (TLS) so nobody on the network between them can read what's being said.

A database is really just a filing cabinet with labelled drawers, called **tables**. Here are all nine drawers Kaval uses, and what lives in each:

| Table (drawer) | What's actually inside it |
|---|---|
| `signal` | One raw thing the scout observed, exactly as seen — never interpreted or changed afterwards. |
| `incident_signal` | The list linking "these 3 signals all belong to the same problem" — only ever added to, never edited. |
| `incident` | One grouped problem the detective has decided is worth looking at as a single thing. |
| `proposal` | What the AI suggested doing about an incident, plus what it cost to ask it. |
| `action` | One specific, concrete step the executor *could* take — not yet approved. |
| `decision` | The actual yes or no on one action — from a human's tap, or an auto-approved safe rule. |
| `execution` | What the executor actually did, and the system's state before and after. |
| `outcome` | Checked five minutes later: did it actually fix the problem? |
| `runbook_chunk` | Pieces of our own written "what to do when X breaks" documents, stored so the detective can look them up before guessing. |

## Keycards, not master keys

None of the four always-on workers shares one login to the filing cabinet. Each has its own named keycard (a Postgres role) that only opens the drawers it actually needs — so a bug in one worker's program can't accidentally read or change a drawer that isn't its business. There's also a fifth, master keycard (the admin role), but it's only ever used by the two one-time setup workers above, never by the five that run all the time.

| Keycard (role) | Drawers it can open, and how |
|---|---|
| `kaval_collector` | Write new rows into `signal` — and read it back. Nothing else. |
| `kaval_agent` | Read `signal` + `incident`; add to and update `incident`, `proposal`, `action`; add (never edit) to `incident_signal`; fully manage its own `runbook_chunk` drawer. |
| `kaval_executor` | Read `action` + `decision`; add to and update `execution` + `outcome`. Cannot approve anything itself. |
| `kaval_gateway` | Read broadly across `incident`, `proposal`, `action`, `execution`, `outcome` to show people what's happening; add to and update `decision` — the only drawer it's allowed to actually fill in. |

Put together, no single worker can both *decide* something is approved and *also* carry it out — the detective can only suggest, the gateway can only record a yes/no that a human or a pre-approved rule actually gave, and the executor can only act once that yes/no already exists. The same split that exists in the real-world approval process is enforced a second time, independently, inside the database itself.
