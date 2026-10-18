# The waste scan is not running (KavalWasteScanFailing / KavalWasteScanStale)

Kaval looks for wasted money once a day: a small function reads the account, leaves its findings in one parameter,
the server copies that into the cluster, and the cost reader's pod turns it into incidents
([ADR-0062](../adr/0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md)). One of two alarms means
that chain is broken, so **waste is not being looked for**. Nothing else is affected: the bill reading
([cost-reader-not-pulling.md](cost-reader-not-pulling.md)) is a separate alarm on the same pod.

## Signals

- **`KavalWasteScanFailing`**: the newest scan failed or its hand-over is missing, and four hours later it still is
- **`KavalWasteScanStale`**: no good scan for 36 hours (a new bring-up gets 36 hours of grace from when the reader starts)
- The pod's log says which: `no good waste scan: missing | describe_failed | failed | unreadable | stale`

## Likely causes

| Log says | Meaning | Where to look |
|---|---|---|
| `missing` | The server has not copied the parameter into the cluster: the timer, the node's right to read the parameter, or the cluster not up yet | `systemctl status kaval-cost-pull.timer` on the server; `journalctl -u kaval-cost-pull`; the node role's `read-app-secrets` policy must list the parameter |
| `describe_failed` | The function could not read the account (a permission removed, an AWS outage) | `aws logs tail /aws/lambda/kaval-<env>-waste-scan --since 2d`; the role's `Look` statement |
| `failed` | The document says failed with a reason Kaval does not know | the parameter's value (below) |
| `unreadable` | The parameter holds something that is not a scan | the parameter's value (below) |
| `stale` | The function stopped running (schedule deleted or disabled), or the server stopped copying | `aws scheduler get-schedule --name kaval-<env>-waste-scan`; the function's last invocation in its log |
| nothing, but the alarm still fires | The reader is up but reads the wrong ConfigMap | the pod's `--waste-handoff` argument must be `<release>-waste-scan` |

## Diagnosis

```bash
AWS_PROFILE=kaval aws ssm get-parameter --name /kaval/kaval-<env>/waste-scan --query Parameter.Value --output text
AWS_PROFILE=kaval aws lambda invoke --function-name kaval-<env>-waste-scan /dev/null   # run it now
kubectl get configmap -n kaval-<env> kaval-<env>-waste-scan -o jsonpath='{.data.scan\.json}'
```

The parameter's `status` is `ok`, `failed` (with a fixed `reason`), or `pending` (Terraform made it and no scan has
run yet; the reader treats that as waiting, not failing).

## Remediation

- Fix the cause in the table above, then run the function by hand (`aws lambda invoke …`); the server copies the new
  result within 3 hours (`systemctl start kaval-cost-pull` copies it at once).
- A function permission error: `terraform plan` in the environment shows the drift; apply with the owner's go.
- Do not edit the parameter by hand to make the alarm stop. The reader refuses anything that is not a scan, and a
  hand-edited "ok" would hide a broken scan.

## Do not

- Do not switch the alarm off. A scan that silently stops is the failure it exists to catch.

## Escalation and ownership

- Owner: Roshan. The alarm is critical and reaches Slack like the other Kaval health alarms; it repeats and escalates
  after 2 hours.

## When it is fixed

`kaval_waste_scan_failing` goes to 0 and the log line reads `nothing new` or lists the day's findings. Record the
cause in the journal.
