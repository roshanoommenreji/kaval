# The waste scan found something (unattached disk or address, old backup copy, server left running, unlabelled resource)

An incident whose name starts `idle_volume`, `idle_address`, `snapshot_past_retention`, `server_overstay` or
`resource_unlabelled` means the daily waste scan
([ADR-0062](../adr/0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md)) found something that
costs money or has no clear owner. This is **for a person**: Kaval proposes no fix and the policy refuses to attach one.
Nothing is broken. The cost is the point: a disk costs about $0.09 per GB a month while it sits there, an address
$3.65 a month, and a running server bills by the hour (staging's two servers and their addresses come to about
$0.045 an hour together; [budget-plan.md](../cost/budget-plan.md) has the full table).

## Signals

- An incident with one of the names above, severity low (medium for `server_overstay`); the target is the resource
  (`ebs/vol-…`, `eip/eipalloc-…`, `snapshot/snap-…`, `ec2/i-…`)
- Its signal carries only ids, sizes, ages and fixed words, for example
  `{"snapshot_id": "snap-…", "class": "stop_copy", "age_days": 41, "limit_days": 30, "size_gib": 20}`
- The scan restates a finding every day while it is true; the incident closes by itself 48 hours after the waste is gone

## Likely causes, and what to do first

| Incident | Usually | First look |
|---|---|---|
| `idle_volume`, `idle_address` | A server was replaced and its disk or address was left, a restore drill was not cleaned up, or a person made it by hand | `aws ec2 describe-volumes --volume-ids <id>` (State `available`, Tags, CreateTime); who made it: CloudTrail `CreateVolume` / `AllocateAddress` |
| `snapshot_past_retention` | An old pre-stop copy that is no longer the newest of its disk | `aws ec2 describe-snapshots --snapshot-ids <id>`: `VolumeId`, `StartTime`, tags. **Check it is not the only copy of a disk that still exists** (the scan never flags the newest of each disk, but a disk may have been replaced) |
| `server_overstay` | Staging was left up past its normal use, or its idle stop did not run | `aws ec2 describe-instances --instance-ids <id>` (LaunchTime); staging's idle-stop log: `aws logs tail /aws/lambda/kaval-staging-idle-stop --since 1d` |
| `resource_unlabelled` | Something a service launched by itself, or made by hand | [untagged-resources.md](untagged-resources.md) |

## Remediation

Removing anything is a deliberate act with your say-so; there is no automatic removal yet (`KAV-95`).

- **Disk or address:** if you recognise it and it is not needed, snapshot first if it ever held data
  (`aws ec2 create-snapshot --volume-id <id> --tag-specifications …` with the three required labels), then delete.
  A fixed address is released with `aws ec2 release-address --allocation-id <id>`
- **Backup copy:** confirm a newer copy of the same disk exists, then delete the old one. Never delete the only copy
- **Server:** `make staging-down` ends staging deliberately; to just park it, scale its group to zero or stop it from
  the console. Production is never flagged
- **Unlabelled:** label it; do not delete it for being unlabelled

## Do not

- Do not delete something because it is flagged. A flag says "look", not "remove": a disk the scan sees twice may
  still be a restore someone is checking
- Do not stop a production server because of a `server_overstay`; production is not scanned for it, so a finding
  naming a production server is a bug to report
- Do not delete the newest copy of any disk, even if it is old

## Escalation and ownership

- Owner: Roshan. These are low severity and do not page; they appear as incidents. A server left running is medium.
- If a finding repeats for a week, either remove the waste or write down why it stays in the journal, so the next
  reader does not have to work it out again.

## When it is fixed

The waste is removed or accounted for. The next daily scan (03:15 UTC) no longer reports it, and the incident closes
48 hours later. To look sooner without waiting: `aws lambda invoke --function-name kaval-<env>-waste-scan /dev/null`
runs the scan now (it needs the owner's login; two runs in a row are what a disk needs to be reported).
