# ADR-0007 — Develop on an AWS dev server, not the laptop

- **Status:** Accepted
- **Date:** 2026-09-26
- **Deciders:** Roshan

## Context

The plan put Phases 1–3 entirely on the laptop ("local first", $0), with AWS compute arriving
only in Phase 4. Before starting `KAV-22` (the docker-compose stack), Roshan chose to run the
stack on AWS instead, explicitly accepting the cost.

The laptop could have run it. It has an i5-1335U, 16 GB of RAM, and Docker is allowed 7.6 GB,
against a Phase 1 need of about 2.5 GB. So this is not a capacity decision. What AWS gives that
the laptop can't:

- **Real measurements.** The model shortlist (Gemma 3 1B, Gemma 4 E2B, Qwen3 1.7B,
  Llama 3.2 1B) is chosen on memory and speed. The laptop is x86. Prod will be a 4 GB Graviton
  (arm64) `t4g.medium`, so only the same machine gives the true numbers.
- **arm64 from the first build.** `CLAUDE.md` warns that an amd64 image builds fine locally and
  fails on the cluster. On a Graviton server every build is native arm64, so that class of bug
  can't reach Phase 4.
- **The laptop stays free for other work.** Roshan's Finance project already runs nine Supabase
  containers in the same Docker.

Prices checked 2026-09-26 against the AWS Price List API, ap-south-1:

| | On-demand | Spot |
|---|---|---|
| `t4g.small` (2 GB) | $0.0112/hr | $0.0040/hr |
| `t4g.medium` (4 GB) | $0.0224/hr | $0.0090/hr |
| `t4g.large` (8 GB) | $0.0448/hr | $0.0193/hr |
| gp3 storage | $0.0912 per GB-month | |

## Decision

**One `t4g.medium` on-demand dev server, `kaval-devbox`, which stops itself after 60 idle
minutes.** It's defined in `infra/modules/devbox`, used from `infra/envs/dev`.

- **Same size as prod.** Its measurements are the real ones.
- **On-demand, not spot.** A development machine that AWS can take away mid-session costs more
  in lost work than spot saves: roughly $1/month at this usage.
- **No inbound ports.** The security group has no ingress rules. Access goes through SSM
  Session Manager, which the instance dials out to, with SSH tunnelled inside it. There's no
  public SSH port to scan and no key to leak into a firewall rule.
- **The laptop stays the editor.** Code, Git, Claude Code and `.env` all stay on the laptop. A
  Docker context (`ssh://kaval-devbox`) sends `docker` commands to the server, and ports come
  back through the same tunnel.
- **Idle stop inside the instance.** A systemd timer checks every 5 minutes for any SSH or SSM
  session and runs `shutdown` after 60 minutes of none. Shutdown behaviour is set to **stop**,
  never terminate, so the disk and everything on it survive.
- **Default VPC.** The proper network module is Phase 4 work. Building it now just for a dev
  server would pull Phase 4 forward for no gain.

**The promotion path doesn't change.** ADR-0004's `local → staging → prod` stays exactly as it
is. `local` still means "the developer's own environment". It just runs on this server now
instead of the laptop. So the name `local` stays everywhere: the dashboard,
`deploy/environments/local/`, `CLAUDE.md`. The Terraform folder is `infra/envs/dev` because it
describes *a machine*, not a promotion tier.

## Alternatives rejected

- **Stay on the laptop (the original plan).** Works and costs $0, but gives x86 measurements and
  amd64 builds. This option was superseded by Roshan's choice, not by a flaw.
- **`t4g.large` (8 GB).** Room for bigger models, but the numbers would no longer predict prod,
  and always-on ($32.70/mo) breaks the $25 ceiling.
- **Always on.** No start-up wait, but $16.35/mo for compute alone. Idle stop gets the same
  development experience for about $2.40 of running costs.
- **Bedrock as the main model.** Pay-per-call forever, and it gives up the self-hosting the
  project is built to demonstrate. Bedrock stays the escalation path only.
- **An SSH port open to one IP address.** Simpler, but the home IP changes, and it's an open
  port. SSM needs no inbound rule at all.

## Consequences

- **Phases 1–3 are no longer $0.** Expect about $5/month at 20 hours a week: $1.95 compute,
  $0.44 public IPv4 (both only while running), and $2.74 for the 30 GB disk, which bills even
  while stopped. That adds ~$15 over the three phases and takes the project projection from ~$52
  to ~$67, against a $70 budget-with-buffer. It's paid from the $140 of AWS credit (expires 2027-09-11). See
  `docs/cost/budget-plan.md`.
- **The $24 hard-stop Lambda does not cover this server.** It was built to scale the Phase 4 ASG
  to zero. Protection here is the idle stop plus the $18 and $22 email alerts. Extending the
  Lambda to stop instances tagged `Project=kaval` changes the applied prod guardrail module, so
  it's a separate, explicitly approved change.
- **The Free Plan may refuse `t4g.medium`**: some sources say Free Plan accounts are limited to
  `t4g.small`. If so, the launch fails cleanly, and upgrading the account to the paid plan keeps
  the credits.
- **Phase 3's k3d runs on this server**, not the laptop. The `local` values file in
  `deploy/environments/local/` keeps its name, since it's "the developer's cluster" wherever
  that runs.
- **New commands:** `make devbox-up`, `devbox-down`, `devbox-status`, `devbox-ssh`.
- **Revisit** if monthly dev spend passes $8, or when Phase 4's real node exists. At that point
  the dev server could become the staging node's template instead of a separate machine.
