# ADR-0038: The nightly database dump is taken by the app node itself, not by a pod

**Status:** Accepted. Built and proven locally (Lab 40); **not yet run on AWS** (see "Not proven").
**Date:** 2026-10-10
**Related:** [ADR-0005](0005-data-durability-and-staging-seeding.md) (RPO 24 h, the nightly dump),
[ADR-0008](0008-production-database-on-its-own-server.md) (the database server; the CronJob design this
replaces), [ADR-0037](0037-staging-seeding-from-the-production-dump.md) (staging is seeded from this dump),
`KAV-74`, Lab 40

## Context

ADR-0005 and ADR-0008 decided that production is dumped to S3 every night and that staging is seeded from
that dump. The dump was written as a Helm `CronJob` (`scripts/ops/backup.sh`). It has never run: the S3 bucket
is empty. Reading what it would need to run found four separate problems:

1. **A pod cannot use the node's AWS identity.** Pods on this k3s node cannot reach the instance metadata
   service (the default hop limit stops one hop short of a pod), so the CronJob would need an `aws-creds`
   Secret holding a live key, refreshed from the node. That means a working AWS credential sitting inside the
   cluster. The environment's safety classifier refused to draft it, and the decision belongs to Roshan.
2. **The schedule would never find anything running.** The CronJob runs at 00:00 UTC. The nightly auto-stop
   (ADR-0008, Lab 25) runs at 02:00 IST, which is 20:30 UTC, and scales the app node to zero and stops the
   database server. At 00:00 UTC neither exists.
3. **The node's database tools are too old.** The node installs `postgresql15`. The database server runs
   Postgres 16, and `pg_dump` refuses to dump a server newer than itself ("server version mismatch"). A dump
   from the node would have failed on its first run.
4. **`backup.sh` would upload an unreadable file.** It checked that `pg_dump` exited cleanly but not that the
   file could be read back, and it moves the `LATEST` pointer that `restore.sh` trusts.

## Decision

**1. The node takes the dump, from a systemd timer, using its own instance role.** The node already has
permission to write to the backup bucket (`infra/modules/iam`), already reads the database admin password from
SSM, and already finds the database server by tag, all for the `db-roles.sql` step in
`kaval-gitops-bootstrap`. The new `kaval-db-backup` command does the same three things and then runs
`scripts/ops/backup.sh`. No key is created or stored anywhere new. It lives in the node's cloud-init
(`infra/modules/node/user_data.sh.tftpl`), and is installed only when the module is given a
`backup_bucket_name` (prod and staging both pass their own bucket; the default is none).

**2. 19:30 UTC (01:00 IST).** One hour before the 20:30 UTC auto-stop, which is plenty: a dump of this database
takes seconds.

**3. A Postgres 16 client** (`dnf install postgresql16`, falling back to 15). The fallback exists only so a
missing package cannot stop the whole node from booting (that would break `make up` and `make staging-up`).
With the fallback, a node on 15 boots, and the dump then fails loudly at `pg_dump`, which is the better way to
find out.

**4. `backup.sh` lists the archive with `pg_restore --list` before uploading.** A file that cannot be listed
is not uploaded and the `LATEST` pointer is not moved. It also now passes `POSTGRES_SSLMODE` through as
`PGSSLMODE` (it was set by the Secret but never used).

**5. The chart's `CronJob` stays, disabled.** It is not used. Deleting it would change the Helm chart and cut a
release for no behaviour change, so it goes the next time the chart changes for another reason.

## Alternatives rejected

- **`aws-creds` Secret, refreshed hourly from the node.** The original plan. It works, but it puts a live
  (short-lived) AWS credential inside the cluster, readable by anything that can read Secrets in that
  namespace. The timer needs no such thing for the same result.
- **Raise the metadata hop limit to 2 so pods can use the node role.** One line, but every pod on the node
  then holds everything the node holds, including read access to the database and Slack secrets in SSM. This
  would undo the "agent never gets write access" separation in spirit.
- **Dump from the database server itself.** It has no S3 access and no reason to have it; giving it some
  widens the one machine that holds the data.

## Consequences

**Easier.** The first real dump needs no credential decision. One copy of the dump logic (`backup.sh`) serves
the timer, the chart job if it is ever enabled, and by-hand use before something risky.

**Harder / accepted.**
- `backup.sh` is fetched from `main` at run time and run as root with the admin password, the same trust the
  node already extends to `db-roles.sql`. `main` is protected (pull request, 11 required checks), so a change
  to it is reviewed. If that is ever judged too loose, pin the fetch to a commit.
- **"Nightly" means nightly while production is running.** Production is parked most of the time (`make down`),
  and a parked node takes no dump. The EBS snapshots (daily, plus one before every stop) are the recovery
  point on those days. A dump can be taken by hand over Session Manager (`systemctl start kaval-db-backup`)
  right before parking, and doing so is what gives staging something to be seeded from.
- A failed dump is visible only in the journal (`journalctl -u kaval-db-backup`) and as a stale `LATEST`. There
  is no alert yet.

**Cost.** No new AWS resource. S3 storage for 14 days of dumps is a few pennies a month (the dump of the
current schema is about 20 KB; ADR-0005 estimated $0.05/month). No change to the $50 ceiling.

**Takes effect** on the next node that boots (`make up`, `make staging-up`). The existing prod plan shows
`0 to add, 2 to change, 0 to destroy` (the launch template and the Auto Scaling group's version of it).

## Proven so far (Lab 40)

The real boot script, rendered by Terraform with and without a bucket (valid shell both ways; the timer is
only present with one). The node's dump command and the real `backup.sh` run end to end against a real
Postgres 16 with the real migrations: the dump is uploaded and readable and contains the row, `LATEST`
moves, a 40-day-old dump expires and a 3-day-old one stays. Four failures upload nothing and exit non-zero (an
unreadable dump, a version-mismatch error, no database server found, a refused login). With the readable-file
check removed, a junk file **is** uploaded and `LATEST` is moved to it, so the check earns its place.

## Found live, 2026-10-10: the boot script was over EC2's size limit

The first `make up` after this change failed before any server started: `InvalidUserData.Malformed: User data is
limited to 16384 bytes`. The rendered script is 17,643 bytes (14,653 before this ADR). `terraform plan` does not check
the limit, and none of the local proof exercised it, so the plan's "0 add, 2 change" was true and still not enough.
Fix: `base64gzip` instead of `base64encode` in `infra/modules/node/main.tf`; cloud-init unzips gzip user data by
itself, and the zipped script is about 6,550 bytes, so the limit is no longer close. Staging shares the module, so it
would have failed identically. The rule for the next addition: measure the **rendered** size (Lab 40, step 1b).

## Not proven (needs a live run, and is part of `KAV-73`)

- That `dnf install postgresql16` exists on this Amazon Linux 2023 image. Search results conflicted. If it
  does not, the node falls back to 15 and the dump fails with the version-mismatch message.
- The real TLS connection to the database server, and the real S3 writes under the node's IAM policy.
- That the systemd timer fires at 19:30 UTC.
