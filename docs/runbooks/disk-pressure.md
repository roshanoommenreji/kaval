# The app server's disk is nearly full

Kaval has noticed that the root disk of the machine it runs on is above its line (85 percent on prod,
38 percent on staging; see [ADR-0047](../adr/0047-incidents-that-are-for-people-disk-full-and-slowness.md)).
This is **for a person**: Kaval posts to Slack and proposes no fix, because no restart frees a disk and
the right action depends on what is filling it. If a fix appears in the approvals list for this
incident, something is wrong; do not approve it (the system will refuse anyway).

## Signals

- Slack: ":warning: Needs a person: the app server's disk is nearly full", with this runbook; a
  reminder every 30 minutes while it stays above the line, and one louder `@here` message after 2 hours
- An incident `disk_pressure:k8s:<namespace>/node-disk`, severity high
- On the machine: `df -h /` above the line
- Pods being evicted with "DiskPressure" or images failing to pull ("no space left on device") means it
  is already past the point Kaval warns about

## Likely causes

Ordered by how often they happen on a single small k3s node.

1. **Container images piled up** (every release leaves the old image on the node)
2. **Logs** under `/var/log/pods` and the systemd journal
3. **A workload writing to its own disk** (a program that logs to a file, a cache, a leak), including
   what Kaval's own pods write to their temporary volumes
4. **Prometheus's temporary storage** (bounded: two hours of data, but it grows with what is scraped)
5. **A game day still running** on staging: `make chaos-status` (the disk fill is bounded at 3 GiB and
   removes itself)

## Diagnosis

Over Session Manager (`aws ssm start-session --target <instance>`), as root:

```bash
df -h /
du -xh --max-depth=2 /var/lib/rancher /var/log 2>/dev/null | sort -h | tail -15
k3s crictl images | head -30            # how many images, how big
k3s kubectl get pods -A -o wide         # what is running and where
k3s kubectl get --raw "/api/v1/nodes/$(hostname)/proxy/stats/summary" | grep -B2 -A6 ephemeral
```

The last command lists each pod's own disk use; the biggest one is the first suspect.

## Remediation

Do the safe, reversible things first, and look at the number after each:

1. **Stop the writer** if one is obvious (scale it down or delete it with `kubectl`), after checking it
   is not something Kaval or the database depends on
2. **Remove unused images:** `k3s crictl rmi --prune`
3. **Trim the journal:** `journalctl --vacuum-size=200M`
4. If it is simply full of legitimate data: **grow the volume**. That is a Terraform change to the
   node's root volume size (`infra/modules/node`), applied with a plan shown first, **to prod only
   when you decide to**; the filesystem is then grown on the machine (`xfs_growfs /` or
   `resize2fs`, depending on the type). Staging is rebuilt from scratch, so `make staging-down` and
   up again is the quick answer there

## Do not

- **Do not delete under `/var/lib/rancher/k3s/server` or `/var/lib/rancher/k3s/agent/containerd`
  by hand.** That is the cluster's own state; use `crictl` to remove images
- Do not delete a PersistentVolumeClaim to "free space": the policy refuses it, and so should you
- Do not restart pods hoping it helps; it frees nothing and hides the cause. Kaval will not propose
  it either
- Do not ignore the reminder: a disk at 100 percent stops the database writes and the whole node
  with it

## If it is the DATABASE server's disk

The message says "the database server's disk is nearly full" and the incident is
`disk_pressure:k8s:<namespace>/database-disk` ([ADR-0050](../adr/0050-monitoring-and-patching-the-database-server.md)).
That server has two disks: a small root disk (8 GB: the system, Docker's logs) and the data volume
(`/data`, where Postgres keeps the database). Find out which, over Session Manager on the database
server (`aws ssm start-session --target <database-instance-id>`):

```bash
df -h / /data
sudo du -xh --max-depth=2 /var/lib/docker /var/log 2>/dev/null | sort -h | tail -10   # root disk
sudo du -xh --max-depth=2 /data 2>/dev/null | sort -h | tail -10                       # data volume
sudo docker exec postgres psql -U kaval -d kaval -c "SELECT pg_size_pretty(pg_database_size('kaval'))"
```

- **Root disk full of container logs:** Docker keeps a container's log without limit unless told. The monitoring
  script sets a default cap that applies from the next Docker start; `sudo journalctl --vacuum-size=200M`
  and truncating the biggest `/var/lib/docker/containers/*/*-json.log` frees space now
- **Data volume full because the database really grew:** that is legitimate growth, and the fix is more
  disk. Grow the EBS volume (`data_disk_gb` in `infra/envs/<env>`, plan shown first, **prod only when you
  decide**), then `sudo resize2fs` on the device. A snapshot first (`make db-restore-snapshot` documents the
  names); do not shrink or delete under `/data/pgdata`
- **Do not** `rm` anything under `/data/pgdata` or delete WAL files by hand: that destroys the database

## After

- Check the incident closes by itself (about 15 minutes after the last signal)
- If the cause was a workload, fix it at the source (log rotation, a cache limit) and write what you
  learned in the post-mortem
