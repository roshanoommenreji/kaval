# Lab 31 — the Phase 4 exit gate: kill the node, time the rebuild

**Phase:** 4 · **Time:** about 30 minutes of wall clock · **Story:** `KAV-60`
**Cost:** about $0.03: roughly 25 minutes of the database server and two short stretches of the
On-Demand app node (`t4g.medium`, ~$0.0224/hr each, one of them only 6 minutes)

`ROADMAP.md` ends Phase 4 with one sentence: **terminate the node by hand; it rebuilds itself from
Git in under 5 minutes.** It was shown once before, on a Spot node (Lab 20, `KAV-51`), but never
timed, and since then the node went On-Demand (ADR-0028), Secrets were made to survive replacement
(`KAV-56`) and the bootstrap and database-roles scripts were fixed. This lab is the timed run of the
real thing against prod, on the current code.

**Result: 3 minutes 36 seconds from the kill to every pod `Running 1/1`. The gate passes with 84
seconds to spare, and nobody typed a command after the kill.**

---

## 1. Start from the parked posture

Prod sits parked between sessions: the node's Auto Scaling Group at 0, the database stopped.

```bash
aws ec2 describe-instances --filters "Name=tag:Project,Values=kaval" \
  --query "Reservations[].Instances[].[Tags[?Key=='Name']|[0].Value,State.Name]" --output text
# kaval-prod-database  stopped        (and no kaval-prod node)
aws autoscaling describe-auto-scaling-groups \
  --query "AutoScalingGroups[].[AutoScalingGroupName,DesiredCapacity]" --output text
# kaval-prod  0
```

## 2. Bring prod up (`make up`, the plan shown first)

The plan, read-only, before anything started:

```bash
terraform -chdir=infra/envs/prod plan -var app_node_desired_capacity=1
# Plan: 0 to add, 1 to change, 0 to destroy.
```

One change, the node's Auto Scaling Group from 0 to 1, and nothing is replaced. (Lab 28's trap, a
plan against a stopped database wanting to replace it, is gone: the `associate_public_ip_address`
fix holds.) Then what `make up` does, step by step (its `read` prompt needs a terminal, so the steps
were run directly):

```bash
bash scripts/ops/resume-database.sh                      # start the database, health check passes
rm -f infra/envs/prod/node.auto.tfvars                   # lift the pause file
terraform -chdir=infra/envs/prod apply -auto-approve     # Apply complete! 0 added, 1 changed
```

Pods polled over Session Manager (the same `kubectl` read as `make staging-status`):

| Seconds from start | State |
|---|---|
| 108 | node booting; `kubectl` not installed yet |
| 182 | agent, collector, executor `Init`, gateway `Running 0/1` |
| 206 | all five pods `Running 1/1` |

That 206 s includes starting the database and the Terraform apply, so it is a loose upper bound on a
cold start, and it answers the watch item from Lab 28: **the first On-Demand launch of the prod node,
and the first run of the fixed bootstrap and `db-roles` on prod, both worked.**

```bash
curl -s http://<gateway pod ip>:8000/healthz
# {"status":"degraded","version":"0.1.0","postgres":{"ok":true,"detail":"migrated to 8f3b1c6a2d94"},
#  "ollama":{"ok":false,"detail":"UnsupportedProtocol"}}
```

`degraded` is the expected steady state on AWS: Postgres is ok and migrated, Ollama is not served
there (the local model runs on the dev server, ADR-0007), so that half of the check cannot pass.

## 3. The gate: terminate the node by hand

```bash
OLD=i-0db9c6a5db284b583
date +%s > kill-time
aws ec2 terminate-instances --instance-ids $OLD
```

Then nothing. The Auto Scaling Group sees the group below its desired size of 1, launches a new
instance from the launch template, cloud-init installs k3s and Flux, Flux pulls `deploy/gitops/prod`
from `main`, and the Helm release brings the services up. Timed from the terminate call:

| Seconds from the kill | What happened |
|---|---|
| 0 | `terminate-instances` returns `shutting-down` |
| 96 | the replacement instance (`i-083dded5f60967dd4`) is `running` |
| ~150 | cloud-init has installed k3s; the cluster has no workloads yet |
| ~190 | Flux has reconciled and created the five pods (`Init`) |
| **216** | **all five pods `Running 1/1`, which is the gate** |

Healthz on the new node again says `postgres ok, migrated to 8f3b1c6a2d94`, from a different
subnet and a fresh instance. This is what `KAV-56` (Secrets survive replacement) and the database
security group (allowing the node's security group, not an address) were for: the pods
authenticated to the database with no hand-created Secret and no edit to any rule.

## 4. Park prod again

`make down`, run as its steps:

```bash
echo 'app_node_desired_capacity = 0' > infra/envs/prod/node.auto.tfvars
terraform -chdir=infra/envs/prod apply -auto-approve     # 0 added, 1 changed
bash scripts/ops/pause-database.sh                       # pre-stop snapshot, then stop
```

Final check: the node `shutting-down` then gone, the database `stopped`, the Auto Scaling Group at 0,
a fresh snapshot taken first (`snap-0181c66ce31680851`). `git status` clean: the pause file is
git-ignored.

---

## What the number does and does not prove

The 3m36s is honest for what it measured, and it is worth being exact about the edges:

- **The database stayed up.** Only the app node was replaced, which is the failure the gate is
  about (a lost or reclaimed node). Losing the database server is a different story, with its own
  drill (Lab 27, restore RTO ~3m51s).
- **Capacity was available.** On-Demand `t4g.medium` came straight back. A capacity shortage in the
  AZ would stretch the 96 s launch step; that is the exact thing that pushed prod off Spot
  (ADR-0028), and the Auto Scaling Group spans three subnets so it can try another zone.
- **The images were already in ECR and the repo reachable.** The rebuild pulls from GitHub and the
  container registry, so a GitHub or ECR outage would stop it. Both are accepted dependencies.
- **96 of the 216 seconds is AWS launching an instance.** Nothing in Kaval can shorten that. The
  remaining ~2 minutes are k3s, Flux and pod startup, and that part is ours.

## Reproduce

1. `make up` on prod (or the three steps in section 2), wait for five pods `Running 1/1`.
2. Note the node's instance ID, then `aws ec2 terminate-instances --instance-ids <id>`.
3. Poll the pods over Session Manager (`scripts/ops/staging.sh` has the `on_node` helper; point it at
   the `kaval-prod` namespace) and stop the clock at all-ready.
4. `make down`.
