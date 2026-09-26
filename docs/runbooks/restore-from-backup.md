# Database lost or corrupted

## Signals

- `postgres` pod in `CrashLoopBackOff` with `PANIC: could not locate a valid checkpoint record`
- `FATAL: database files are incompatible with server`
- PVC missing, or `Multi-Attach error for volume`
- Queries returning empty where rows are expected
- `relation "signal" does not exist`
- Volume shows healthy but the incident history is truncated or absent

## Likely causes

Ordered by how often they actually happen, not by how interesting they are.

1. **Someone ran something destructive** — a `DROP`, a migration in the wrong direction, a `--clean` restore aimed at the wrong host
2. **A migration failed part-way**, leaving the schema between two states
3. **EBS volume detached or attached to the wrong node** — usually an availability-zone mismatch after the ASG replaced the instance
4. **Filesystem corruption** after an unclean shutdown
5. **Volume genuinely lost** — rare

## Diagnosis

Work out whether the *data* is gone or only the *access to it*. These are different incidents and
only one needs a restore.

```bash
kubectl get pvc -n kaval                       # bound? and to what?
kubectl describe pvc postgres-data -n kaval    # events tell you about attach failures
aws ec2 describe-volumes --volume-ids <id> --profile kaval \
  --query 'Volumes[0].[State,AvailabilityZone,Attachments[0].InstanceId]'
kubectl logs -n kaval postgres-0 --previous | tail -50
```

Then check whether the data is there:

```bash
kubectl exec -n kaval postgres-0 -- psql -U kaval -d kaval \
  -c "SELECT count(*) FROM signal;
      SELECT max(ts) FROM signal;
      SELECT count(*) FROM outcome;"
```

**If the volume is in the wrong availability zone**, the data is fine. Constrain the ASG to the
volume's AZ and let it replace the instance. Do not restore.

**If `max(ts)` is recent and counts look sane**, the data is fine and this is an access problem.
Do not restore.

**Only restore when the data is genuinely gone or inconsistent.**

## Remediation

### Assess what you will lose first

RPO is **24 hours** — a deliberate decision, see
[ADR-0005](../adr/0005-data-durability-and-staging-seeding.md). Everything since the last nightly
dump is gone.

```bash
aws s3 cp "s3://${BACKUP_BUCKET}/postgres/LATEST" - --profile kaval
```

The timestamp in that key is your recovery point. Note the gap between it and now — that figure
goes in the incident record.

The `outcome` table is the painful loss. It is the evidence base for promoting an action class
from `ask` to `auto`, it accumulates over months, and it cannot be regenerated.

### Restore

**Reversible:** no. **Blast radius:** the entire database.

```bash
kubectl scale deploy -n kaval --replicas=0 \
  kaval-agent kaval-collector kaval-executor kaval-gateway

./scripts/ops/restore.sh prod        # prompts for the database name

kubectl scale deploy -n kaval --replicas=1 \
  kaval-agent kaval-collector kaval-executor kaval-gateway
```

Writers are stopped first so nothing inserts into a half-restored database.

### Verify

```bash
kubectl exec -n kaval postgres-0 -- psql -U kaval -d kaval -c "
  SELECT count(*) FROM signal;
  SELECT count(*) FROM outcome;
  SELECT max(ts)  FROM signal;"

kubectl get pods -n kaval
kubectl logs -n kaval deploy/kaval-agent --tail=20
```

Then record: the recovery point, the measured restore duration from
`.build/last-restore.json`, and the row counts before and after.

## Do not

- **Do not restore because the pod is crashlooping.** Most crashloops are not data loss. Check `max(ts)` first — restoring unnecessarily *causes* the data loss you were trying to avoid.
- **Do not run `restore.sh prod` to test the restore path.** Staging exercises it on every release, which is the whole reason it seeds from the production snapshot.
- **Do not restore with the writers running.** They will insert into a partially-restored schema.
- **Do not skip the sanitisation when restoring into staging.** `restore.sh` handles it, but if you are doing this by hand, run `anonymise.sql` before anything reads the database.
- **Do not delete the volume to "start clean"** until you have confirmed the snapshot in S3 actually restores. A bad backup plus a deleted volume is total loss.

## Related

- [ADR-0005](../adr/0005-data-durability-and-staging-seeding.md) — why RPO is 24 hours and what was rejected
- [Release engineering](../learn/release-engineering.md) — restore as continuous verification
- `scripts/ops/backup.sh` · `scripts/ops/restore.sh` · `scripts/ops/anonymise.sql`
