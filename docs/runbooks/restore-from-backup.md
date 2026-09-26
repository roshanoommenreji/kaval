# Database lost or corrupted

Production Postgres runs on **its own server**, a `t4g.small` tagged `Role=database`, with its data
on a separate EBS volume ([ADR-0008](../adr/0008-production-database-on-its-own-server.md)). It is
not a pod in the cluster. Admin access is through SSM Session Manager only; the server has no SSH
port. *Written ahead of Phase 4 (`KAV-32`); the commands are verified when the server is built.*

## Signals

- Services logging `connection refused` or `timeout` on port 5432, or `SSL connection has been closed unexpectedly`
- The start-up health check failing after `make up` (`pg_isready` or the sanity query)
- In the Postgres container log: `PANIC: could not locate a valid checkpoint record`, or `FATAL: database files are incompatible with server`
- Queries returning empty where rows are expected, or `relation "signal" does not exist`
- Server healthy but the incident history truncated or absent

## Likely causes

Ordered by how often they actually happen, not by how interesting they are.

1. **Someone ran something destructive**: a `DROP`, a migration in the wrong direction, a `--clean` restore aimed at the wrong host
2. **A migration failed part-way**, leaving the schema between two states
3. **The database server is stopped**, by `make down`, the nightly paused-posture stop, or the $38 hard stop. That's not data loss
4. **The data volume isn't attached or mounted**, or the security group no longer lets the app node reach 5432
5. **Filesystem or WAL damage** after an unclean stop
6. **Volume genuinely lost**: rare

## Diagnosis

Work out whether the *data* is gone or only the *access to it*. These are different incidents, and
only one needs a restore.

```bash
DB=$(aws ec2 describe-instances --profile kaval \
  --filters Name=tag:Project,Values=kaval Name=tag:Role,Values=database \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text)
echo "$DB"                                             # running? stopped?

aws ec2 describe-volumes --profile kaval \
  --filters Name=tag:Project,Values=kaval Name=tag:Role,Values=database-data \
  --query 'Volumes[0].[VolumeId,State,AvailabilityZone,Attachments[0].InstanceId]'
```

**If the server is stopped**, check why (`Reason` tag, hard-stop Lambda log, CloudTrail), then
`make up`. Do not restore.

Then, on the server (`aws ssm start-session --target <instance-id> --profile kaval`):

```bash
sudo docker ps --filter name=postgres            # running?
sudo docker logs postgres --tail 50
findmnt /var/lib/postgres-data                   # data volume mounted?
sudo docker exec postgres psql -U kaval -d kaval -c "
  SELECT count(*) FROM signal;
  SELECT max(ts)  FROM signal;
  SELECT count(*) FROM outcome;"
```

**If the volume isn't mounted, or the security group changed**, the data is fine. Fix the mount
or the rule. Do not restore.

**If `max(ts)` is recent and counts look sane**, the data is fine and this is an access problem.
Do not restore.

**Only restore when the data is genuinely gone or inconsistent.**

## Remediation

### Assess what you will lose first

Two recovery sources exist, and their recovery points differ:

```bash
# EBS snapshots of the data volume: daily (DLM, keep 7) and one before every stop
aws ec2 describe-snapshots --owner-ids self --profile kaval \
  --filters Name=tag:Project,Values=kaval Name=tag:Role,Values=database-data \
  --query 'reverse(sort_by(Snapshots,&StartTime))[:5].[SnapshotId,StartTime,Tags[?Key==`Reason`]|[0].Value]' \
  --output table

# The nightly logical dump in S3
aws s3 cp "s3://${BACKUP_BUCKET}/postgres/LATEST" - --profile kaval
```

The newer of the two is your recovery point. RPO is **24 hours** at worst, a deliberate decision
([ADR-0005](../adr/0005-data-durability-and-staging-seeding.md)). A pre-stop snapshot is often much
newer. Note the gap between the recovery point and now; that figure goes in the incident record.

The `outcome` table is the painful loss. It is the evidence base for promoting an action class
from `ask` to `auto`, it accumulates over months, and it cannot be regenerated.

### Stop the writers

**Reversible:** yes. **Blast radius:** the application, briefly.

```bash
kubectl scale deploy -n kaval --replicas=0 \
  kaval-agent kaval-collector kaval-executor kaval-gateway
```

Writers stop first so nothing inserts into a half-restored database.

### Restore option A: from an EBS snapshot (whole-volume damage)

**Reversible:** yes, the damaged volume is kept. **Blast radius:** the entire database.
Use this when the volume or its filesystem is damaged, or the start-up health check failed after a stop.

```bash
make db-restore-snapshot SNAPSHOT=<snap-id>
```

This stops the Postgres container, creates a new volume from the snapshot in the same AZ,
detaches the damaged volume (tagged `Reason=damaged`, **not deleted**), attaches and mounts the
new one, and starts Postgres. WAL crash recovery runs on first start; that's expected.

### Restore option B: from the nightly dump (logical damage)

**Reversible:** no. **Blast radius:** the entire database.
Use this for a bad `DROP` or a broken migration, where the volume is healthy but the data isn't.

```bash
./scripts/ops/restore.sh prod        # prompts for the database name
```

### Start the writers

```bash
kubectl scale deploy -n kaval --replicas=1 \
  kaval-agent kaval-collector kaval-executor kaval-gateway
```

### Verify

On the server:

```bash
sudo docker exec postgres psql -U kaval -d kaval -c "
  SELECT count(*) FROM signal;
  SELECT count(*) FROM outcome;
  SELECT max(ts)  FROM signal;"
```

From the cluster:

```bash
kubectl get pods -n kaval
kubectl logs -n kaval deploy/kaval-agent --tail=20
```

Then record the recovery point, which source you used (snapshot ID or dump key), the measured
restore duration (`.build/last-restore.json` for a dump), and the row counts before and after.

## Do not

- **Do not restore because a service can't connect.** Most connection failures are a stopped server, a missing mount or a security-group change, not data loss. Check `max(ts)` first; restoring unnecessarily *causes* the data loss you were trying to avoid.
- **Do not restore on every start.** The start-up health check restores only on failure. A stop keeps the EBS volume, so a normal start resumes from the same disk.
- **Do not run `restore.sh prod` to test the restore path.** Staging exercises it on every release, which is the whole reason it seeds from the production snapshot.
- **Do not restore with the writers running.** They will insert into a partially-restored schema.
- **Do not skip the sanitisation when restoring into staging.** `restore.sh` handles it, but if you are doing this by hand, run `anonymise.sql` before anything reads the database.
- **Do not delete the damaged volume** until the restored database has been verified. It carries `prevent_destroy` for exactly this reason. A bad backup plus a deleted volume is total loss.
- **Do not terminate the database server to "start clean".** Termination protection is on deliberately.

## Related

- [ADR-0008](../adr/0008-production-database-on-its-own-server.md): why the database has its own server, and its stop and snapshot safeguards
- [ADR-0005](../adr/0005-data-durability-and-staging-seeding.md): why RPO is 24 hours and what was rejected
- [Release engineering](../learn/release-engineering.md): restore as continuous verification
- `scripts/ops/backup.sh` · `scripts/ops/restore.sh` · `scripts/ops/anonymise.sql`
