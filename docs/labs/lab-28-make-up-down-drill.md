# Lab 28 — the `make up` / `make down` drill (part 1)

**Phase:** 4 · **Time:** ~50 min, almost all of it recovering from two more latent bugs
**Cost:** a few cents of DB-server runtime; the app node never actually launched (see below),
so no app-node spend happened at all

`ROADMAP.md`'s last unchecked Phase 4 line is `make up` / `make down` — the targets exist (built
alongside the rest of Phase 4) but, like the restore script in Lab 26, had only ever been
reviewed, never actually run end to end against prod. This lab is that run. It didn't finish —
see "What's still open" — but it already paid for itself: two more real bugs, found the same way
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

## What's still open

`terraform apply` (the rest of `make up`) then failed for an unrelated, external reason:

```
Error: waiting for Auto Scaling Group (kaval-prod) capacity satisfied:
Failed: There is no Spot capacity available that matches your request.
```

Confirmed not a config problem — the ASG's `vpc_zone_identifier` already spans all three AZs
(`ap-south-1a/b/c`); AWS's own activity log named `ap-south-1a` specifically as short on
`t4g.medium` spot capacity and suggested the other two, which the ASG did also try and also
failed. Polled scaling activity for ~5 minutes (6 retries, all failed) — genuine, if unlucky,
regional Spot scarcity for this instance type at this moment, not something to fix in code.

Decision: leave `desired_capacity=1`. The ASG keeps retrying on AWS's side in the background at
no cost until an instance actually launches — nothing to babysit. The database was left running
and healthy rather than paused again, since the drill is paused, not finished.

**Not yet done, carried to part 2:** the app node coming up, Flux reconciling, pods healthy, then
the matching `make down` half of the round trip and its own verification. `ROADMAP.md`'s
`make up` / `make down` line stays unchecked until that actually happens — two real bugs fixed is
real progress, but it isn't the round trip the checklist line asks for.

## Related

- [Lab 26](lab-26-startup-health-check.md), [Lab 27](lab-27-restore-drill.md) — the scripts this
  lab exercised, and the same "a drill finds what a review can't" pattern repeating a third time
- [ADR-0008](../adr/0008-production-database-on-its-own-server.md) — amended with both findings
- `ROADMAP.md` Phase 4 — `make up` / `make down` line, still open
