# The cost reader is not getting the AWS bill

Kaval reads the account's AWS bill once a day (see [ADR-0060](../adr/0060-the-cost-reader-the-server-asks-aws-and-the-cluster-never-holds-the-login.md)).
One of three alarms says that is not happening. This is **for a person**: Kaval posts to Slack and proposes no fix,
because the cause is a login, a cap or a timer on the server, none of which Kaval may change. Until it is fixed
Kaval cannot see a cost spike (the bill is the only thing it reads for that), so the rest of Kaval carries on and
this is a gap in watching money, not an outage.

## Signals

- Slack: ":rotating_light: Kaval's own health: `collector-cost` ...", one of
  - **cannot read the AWS bill: the daily cost pull keeps failing** (`KavalCostPullFailing`): the newest pull failed
    and so did the retry four hours later
  - **has had no good AWS bill for over 36 hours** (`KavalCostPullStale`): nothing good has arrived for a day and a
    half, including the case where the pull never ran
  - **is not running, so the AWS bill is not being read** (`KavalCostReaderMissing`): the reader pod is gone
- An incident `kaval_cost_pull_failing:k8s:...` (or `_stale`, `_reader_missing`), severity high
- A parked production shows none of these (no server, no reader), by design

## Likely causes

Ordered by how often they should happen.

1. **The monthly cap was reached** (60 requests): the handoff says `capped`. Something asked far more than once a day
2. **The server could not borrow the read-only login** (`assume_role_failed`): the role or the permission to borrow
   it was removed or changed, or the server was rebuilt with a different name
3. **AWS refused or did not answer the request** (`ce_call_failed`): Cost Explorer not enabled, a short outage, a
   changed permission
4. **The timer is not running** on the server (stale, with no failure word): the start-up script did not install it
5. **The cluster could not take the answer**: the reader pod is crash-looping, or the namespace is not ready

## Diagnosis

Over Session Manager (`aws ssm start-session --target <instance>`), as root. Replace `<ns>` with `kaval-prod` or
`kaval-staging`.

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# What the server last told the cluster: status, reason, when, requests this month. Not the bill itself.
kubectl get configmap <ns>-cost-pull -n <ns> -o jsonpath='{.data.status}{"  "}{.data.reason}{"  "}{.data.pulled_at}{"  "}{.data.calls_this_month}{"\n"}'
systemctl status kaval-cost-pull.timer --no-pager      # is the timer there, and when does it next fire
journalctl -u kaval-cost-pull --since "2 days ago" --no-pager | tail -30
ls -l /var/lib/kaval/cost-pull/                        # calls-<month> is the count, last-ok the last good pull
kubectl get pods -n <ns> | grep collector-cost         # the reader
kubectl logs -n <ns> deploy/<ns>-collector-cost --tail=20
```

Reading the answer:

| Handoff says | Meaning | Next step |
|---|---|---|
| `capped capped` | 60 requests used this month | See "The cap" below. Do **not** delete the counter file to get round it without finding out why |
| `failed assume_role_failed` | cannot borrow the login | Check the role exists and the server may borrow it (below) |
| `failed ce_call_failed` | AWS refused the request | Run the request by hand as the role (below); if Cost Explorer itself is down, wait |
| `ok` but the alarm says stale | the answer is old | The timer is not firing: `systemctl status kaval-cost-pull.timer`, and `journalctl` above |
| no ConfigMap | the pull never wrote one | The namespace was not ready, or the timer is missing |

## The cap

`calls_this_month` is the number the server has counted. About 31 is normal. If it is near 60, find out what asked
so often: `journalctl -u kaval-cost-pull` shows each try. A pull that fails after the request was sent still counts
(AWS may bill it), so a long-failing pull uses the cap up too; fix the failure first. The counter is a file per
month, so it resets itself on the 1st; a rebuilt server also starts from zero (an accepted gap, ADR-0060).

## The login

```bash
# From the laptop (project login), read-only checks:
aws iam get-role --role-name <ns>-cost-reader --query Role.AssumeRolePolicyDocument
aws iam list-role-policies --role-name <ns>-cost-reader          # expect: read-costs
aws iam get-role-policy --role-name <ns>-cost-reader --policy-name read-costs
aws iam get-role-policy --role-name <ns>-node --policy-name borrow-cost-reader
```

The trust must name the server's role, the one permission must be `ce:GetCostAndUsage`, and the server's role must
be allowed `sts:AssumeRole` on this role. If any is missing, `terraform plan` in the environment shows it; the
fix is `terraform apply` (never by hand), with the plan shown first.

## Remediation

Nothing here changes what Kaval may do to the account; it only restores the reading. All of it is reversible and
touches the bill reader alone.

- **Capped:** wait for the 1st (the count restarts), after finding what asked so often. Blast radius: none.
- **The login or the right to borrow it is missing:** `terraform plan` in the environment, read it, then
  `terraform apply` (production only when asked, with the plan shown first). Reversible: yes, it is Terraform.
- **The timer is not running:** `systemctl restart kaval-cost-pull.timer`. If the unit is missing altogether, the
  server was started from an old start-up script: the fix is a server replacement through the normal rollout, not a
  hand-made unit. Blast radius: the server's own timers.
- **The reader pod is down:** delete the pod and let the Deployment make a new one
  (`kubectl delete pod -n <ns> -l app.kubernetes.io/component=collector-cost`). Blast radius: this one pod.
- **Cost Explorer itself is failing:** nothing to fix on our side; the next good pull catches up, it asks for 14 days.

## Do not

- Do not delete the counter file, or edit the ConfigMap by hand, to make the alarm go away. The alarm would clear
  while the bill is still unread, which is the failure it exists to prevent.
- Do not raise the cap or ask more often "to catch up". Every request costs a cent, and one request already covers 14
  days.
- Do not widen the login (more permissions, a different role) or put an AWS key in the cluster to get round a refusal.
  If the read-only role cannot do something, that is the design working.
- Do not approve any proposal that appears for this incident: none should exist (it is for a person), and the
  system refuses it anyway.

## Escalation and ownership

- Owner: Roshan. A message repeats every 30 minutes while the fault stays, and one louder message arrives after 2
  hours ([health alerts](../adr/0042-watching-the-watcher.md)).
- Nothing here is urgent in money terms: a missed day is picked up by the next good pull, which asks for 14 days.
  A gap of more than 14 days leaves those days out of Kaval's table for good (Cost Explorer still has them, and
  `make cost-report` still shows the month's total).

## When it is fixed

The next good pull clears the first two alarms by itself (the reader sets the failing number back to 0 and records
the new time). Confirm with the ConfigMap command above: `ok`, a recent time.
