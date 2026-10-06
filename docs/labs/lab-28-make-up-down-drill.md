# Lab 28 — the `make up` / `make down` drill

**Phase:** 4 · **Time:** most of a day, spread across a multi-hour AWS Spot capacity wait
**Cost:** a few cents of DB-server runtime, a short stretch of On-Demand app-node billing
(~$0.0224/hr instead of ~$0.0126/hr Spot, for under an hour) to get the round trip done

`ROADMAP.md`'s last unchecked Phase 4 line is `make up` / `make down` — the targets exist (built
alongside the rest of Phase 4) but, like the restore script in Lab 26, had only ever been
reviewed, never actually run end to end against prod. This lab is that run, in two parts: the
first found two bugs and then hit an external wall; the second got past the wall and found a
third, far more serious bug, plus a near-miss in the very fix for it. All four fixed; the round
trip now completes for real. It already paid for itself several times over: real bugs, found
the same way
Lab 27's were, by actually executing the thing instead of reading it.

---

## Pre-drill verification caught a live landmine

Before touching `make up`, a plain read-only `terraform plan` against prod (standard practice
before any drill this session) showed:

```
Plan: 2 to add, 1 to change, 2 to destroy.
  # module.database.aws_instance.database must be replaced
  # module.database.aws_volume_attachment.data must be replaced
  # module.node.aws_autoscaling_group.node will be updated in-place
```

The database instance and its volume attachment — real, live, with real data — were about to be
destroyed and recreated by a plain `terraform apply`. Root cause: `associate_public_ip_address =
true` in `infra/modules/database/main.tf`, and AWS only reports that attribute back as `true`
while the instance is *running*. The database was in its normal paused posture (stopped), so the
state refresh read it back as `false`, and because that attribute forces replacement when
changed, Terraform planned to tear down and rebuild the server. `make up`'s plain `terraform
apply` (no `-target`) would have done exactly that, for real, if it had run before the database
was resumed first.

This hadn't shown up before because every prior `terraform plan` against prod (e.g. Lab 26's
"no changes anywhere" check) happened to run while the database was already running.

**Fixed:** added `associate_public_ip_address` to the instance's existing `lifecycle.ignore_changes`
list (`infra/modules/database/main.tf`), right alongside `ami` and `user_data` — same reasoning,
same precedent: a value that's correct at launch and unreliable to read back while stopped must
never drive a replace. Confirmed with a plan: `No changes. Your infrastructure matches the
configuration.` No apply was needed — `ignore_changes` is plan-time behaviour, not a state change.

## Running `make up` surfaced the second bug

```
$ echo y | make up
This starts billing at roughly $0.0126/hr (app node) plus the database server (~$14.40/mo while running).
Starting database instance i-03ab2a6fccaceff7c...
Running -- waiting for SSM...
Database online.
Database health check FAILED (SSM command status: Failed).
--- stderr ---
Error response from daemon: No such container: postgres
```

`systemctl status kaval-postgres` on the instance showed the real failure underneath:

```
FATAL:  could not load server certificate file "/tls/server.crt": No such file or directory
```

And `lsblk` showed the actual 20 GB data volume (`nvme1n1`) with **no mountpoint at all**.
`/etc/fstab` still had the by-id path for the *old* volume that Lab 27's restore drill detached
and (later) deleted:

```
/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol04ded6d8d8e9f8373 /data ext4 defaults,nofail 0 2
```

`restore-snapshot.sh` mounted the new volume by hand for that session (`mount $DEV /data`) but
never touched `/etc/fstab`. `nofail` let this boot continue anyway, `kaval-postgres.service` has
no dependency on the mount succeeding, so Docker auto-created empty `/data/tls` and
`/data/pg_hba.conf` directories for its bind mounts, and Postgres happily `initdb`'d a **brand
new, empty** cluster straight onto the root disk. The container only died because the (also
missing, for the same reason) TLS cert made it fail to start — which accidentally limited the
damage to "an empty cluster got created on the wrong disk," not anything worse.

**The real restored production data was never touched.** `vol-06b98fb00709dd812` sat attached and
untouched the whole time; nothing ever mounted it, so nothing ever wrote to it.

### Recovery (live, approved step by step)

```
systemctl stop kaval-postgres
sed -i 's#...vol04ded6d8d8e9f8373#...vol06b98fb00709dd812#' /etc/fstab
rm -rf /data/pgdata /data/tls /data/pg_hba.conf   # the bogus empty cluster, not real data
mkdir -p /data/pgdata /data/tls
mount /data
systemctl start kaval-postgres
```

The real `/data/tls/server.crt` turned out to still be present once the correct volume was
mounted (dated from the original Lab 22 setup, not regenerated) — confirming the certs were never
lost, only unreachable. `make db-health-check` passed afterward against the real data.

**Fixed in `scripts/ops/restore-snapshot.sh`:** the mount step now rewrites the existing `/data`
line in `/etc/fstab` in place (matching on the mount point, not the old device ID, so it works
regardless of which volume was there before), instead of only mounting for the current boot.

**Also hardened** (`infra/modules/database/user_data.sh.tftpl`, takes effect only on the next
real instance replacement, per that file's existing `ignore_changes` note): added
`RequiresMountsFor=/data` to `kaval-postgres.service`, so a future instance can't repeat this
failure mode even if `/etc/fstab` ever goes stale again for some other reason — the service
simply won't start until the mount is actually there, instead of silently running against an
empty directory.

## Part 1's wall: no Spot capacity, anywhere, for hours

`terraform apply` (the rest of `make up`) then failed for an unrelated, external reason:

```
Error: waiting for Auto Scaling Group (kaval-prod) capacity satisfied:
Failed: There is no Spot capacity available that matches your request.
```

Confirmed not a config problem — the ASG's `vpc_zone_identifier` already spans all three AZs
(`ap-south-1a/b/c`); AWS's own activity log named `ap-south-1a` specifically as short on
`t4g.medium` spot capacity and suggested the other two, which the ASG did also try and also
failed. Polled on and off for over three hours — still failing, now cycling all three AZs and
backing off. Tried bumping to `t4g.large` (double the memory, different spot pool) as a
same-family alternative: also failed, also across multiple AZs. This wasn't one unlucky AZ, it
was the whole `t4g` family short on Spot capacity in the region that day.

## Part 2: On-Demand to get unblocked, then a much bigger bug

Switched the launch template to On-Demand (`instance_market_options` removed via a new
`var.spot` toggle, default `true`, in `infra/modules/node`) at full price, temporarily, just to
exercise the round trip for real. Launched immediately. k3s came up `Ready`, Flux reconciled
(`kaval-prod` kustomization and helmrelease both `True`), and all four services — agent,
collector, executor, gateway (×2) — were `Running 1/1` in the `kaval-prod` namespace within about
two minutes of the instance existing.

Then `make down`'s actual command, read closely before running it for the first time ever:

```
cd infra/envs/prod && terraform destroy -target=module.node
```

A plan (never blindly applied) showed this would destroy not just the node, but also the entire
`module.budget` guardrail (hard-stop Lambda, its log group, the nightly auto-stop scheduler's IAM
policy) **and the live production database — its instance, its security group, and its volume
attachment.**

Root causes, two separate ones stacked:

1. `module.budget`'s `asg_name` input was wired as `module.node.asg_name` — a live Terraform
   output reference — even though the ASG's name is a hardcoded literal (`"kaval-prod"`) that
   never actually varies. That reference alone was enough to make `module.budget` depend on
   `module.node` in Terraform's graph, forcing it to be destroyed alongside the node.
2. The database's security group correctly (this part isn't wrong) allows port 5432 only from
   the app node's security group, by ID — proper SG-to-SG scoping, not a CIDR. But that makes
   `module.database` genuinely depend on `module.node` too, and `-target` destroy can't leave a
   security group alive if something else is about to be destroyed out from under a resource
   that references it. Terraform's only consistent option was to destroy the database's security
   group, and then the instance that uses it, right along with the node.

**`make down` had never been safe to run for real, from the moment the database module shipped
(Lab 22) or the budget module was wired to the node (`KAV-50`) — because nobody had ever actually
run it and looked at the full plan.** The live, de facto way this project has been "paused"
between sessions was clearly something else — manually setting the ASG's capacity to 0 by hand —
which is, by complete coincidence, exactly the right fix.

### The fix: scale to zero, don't destroy

An idle `aws_autoscaling_group`, launch template, and security group cost nothing — only a
*running* EC2 instance (plus its public IPv4) bills. So `make down` doesn't need to destroy
anything at all to save money; it only needs zero running instances.

- `infra/modules/node`: new `var.desired_capacity` (default `1`), wired straight into both
  `desired_capacity` and `min_size` on the ASG (`max_size` stays fixed at `1` — this node never
  autoscales beyond one instance, paused or not).
- `infra/envs/prod`: new `var.app_node_desired_capacity` (default `1`), passed through to the
  module.
- `module.budget`'s `asg_name` changed to the literal `"kaval-prod"`, breaking the first,
  unnecessary dependency.
- `Makefile`'s `down` target: `terraform apply` with the capacity variable set to `0`, instead of
  `terraform destroy -target=module.node`. `up` goes back to a plain `terraform apply` (the
  variable's default, `1`, already does the right thing).

Confirmed with a plan before touching anything live: `Plan: 0 to add, 1 to change, 0 to destroy`
— only the ASG's `desired_capacity`/`min_size`. Applied for real: the node terminated cleanly,
the database took its pre-stop snapshot and stopped, exactly as `make down` is supposed to work.

### A second near-miss, from the fix's very first use

Reverting the temporary On-Demand/`t4g.large` overrides immediately afterward, `terraform apply`
was run **without** re-passing the capacity override — and Terraform, quite correctly, used the
variable's *default* (`1`), and tried to scale the node back up. It got lucky: Spot capacity for
`t4g.medium` had freed up in `ap-south-1c` by then, so a real instance actually launched, rather
than failing loudly. Caught immediately, scaled back to `0` right after.

This is exactly the failure mode a one-off CLI `-var` flag invites: it only takes effect on the
apply that passes it, and nothing stops some *later*, unrelated `terraform apply` — run by anyone,
for any reason, forgetting this one flag — from silently undoing the pause. Fixed by persisting
the override in a file instead: `make down` now writes `infra/envs/prod/node.auto.tfvars`
(`app_node_desired_capacity = 0`), which Terraform auto-loads on every plan or apply in that
directory with no flag needed; `make up` deletes it. Not committed to Git — `*.tfvars` is already
gitignored — since it's live operational state, not configuration.

## Outcome

Both halves of the round trip are now proven live: `make up` brings the database and the app node
back, Flux reconciles, pods go healthy; `make down` scales the node to zero and pauses the
database, with nothing destroyed and nothing left running. `ROADMAP.md`'s `make up` / `make down`
line is ticked. Ended the session with the node at `desired_capacity=0` and the database stopped
— Phase 4's standing paused posture — and the temporary On-Demand/`t4g.large` overrides fully
reverted back to Spot `t4g.medium`.

## What this doesn't do

- Doesn't prove the round trip is cheap to repeat under genuine Spot scarcity — this run needed a
  short On-Demand detour to get unblocked at all. A `mixed_instances_policy` (several instance
  types the ASG can fall back across) would be the durable fix for that; not built here.
- Doesn't add a confirmation prompt to `make down` the way `make up` and `make nuke` have one —
  scaling to zero is cheap and reversible, so this wasn't treated as needing one, but it's worth
  a second look if that judgment ever changes.

## Related

- [Lab 26](lab-26-startup-health-check.md), [Lab 27](lab-27-restore-drill.md) — the scripts this
  lab exercised, and the same "a drill finds what a review can't" pattern, now a third and fourth
  time over
- [ADR-0008](../adr/0008-production-database-on-its-own-server.md) — amended with all four
  findings across both parts of this lab
- `ROADMAP.md` Phase 4 — `make up` / `make down` line, now ticked
