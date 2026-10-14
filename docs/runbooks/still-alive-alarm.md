# The "still alive" alarm has rung

An e-mail from AWS says the alarm **kaval-prod-still-alive** (or **kaval-staging-still-alive**) is in
**ALARM**: the app-server group wants a server and Kaval's two-minute "still alive" datapoints have stopped for
25 minutes (10 on staging). Design: [ADR-0053](../adr/0053-the-still-alive-alarm-lives-in-aws.md). This alarm is
not inside the server on purpose, so when it rings, nothing inside Kaval may be able to tell you the same thing.

Proven in production on 2026-10-14 (`KAV-83` Part 3): a pretend ring (`set-alarm-state`) e-mailed the owner, real
datapoints began about 4 minutes after the server booted and arrived every two minutes, the alarm stayed `OK` for
about 45 minutes, and a parked production was quiet. Procedure: [prod-rollout.md](prod-rollout.md).

## Signals

- An e-mail "ALARM: kaval-prod-still-alive" from AWS Notifications (and later "OK: ..." when it clears)
- No Slack messages from Kaval, `make mttr` and the incident list not moving
- The alarm state: `aws cloudwatch describe-alarms --alarm-names kaval-prod-still-alive --profile kaval --query 'MetricAlarms[0].[StateValue,StateReason]'`

## First question: should production be running?

```bash
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names kaval-prod --profile kaval \
  --query 'AutoScalingGroups[0].[DesiredCapacity,length(Instances)]'
```

- **`[1, 1]` (running):** the server is up but the datapoints stopped: "The poller is not finishing passes" below
- **`[1, 0]` (wants one, has none):** the server is missing: [node-lost](node-lost.md)
- **`[0, 0]` (parked):** the alarm should not ring. It can only mean the group's size reading is missing (the
  rule counts a missing reading as "wants a server" on purpose): go to "A false ring while parked" below

## The poller is not finishing passes (server up, datapoints stopped)

Likely causes, most frequent first. On the server (`aws ssm start-session --target <instance-id> --profile kaval`):

1. **The server was lost or is still coming up.** A replacement takes about 2 min 10 s on staging and up to 8
   minutes cold. [node-lost](node-lost.md)
2. **The poller is not finishing passes.** Prometheus is down, the database is unreachable, or the poller is
   crash-looping. [kaval-stopped-reporting](kaval-stopped-reporting.md); and
   `kubectl logs -n kaval-prod deploy/kaval-prod-collector-prometheus --tail=40`
3. **The timer itself failed.** Its journal says why a beat was missed (nothing is printed when it works):
   `systemctl status kaval-heartbeat.timer` and `sudo journalctl -u kaval-heartbeat.service --since "1 hour ago"`.
   Messages: "the poller's health page could not be read", "reports no finish time", "last finished pass was N
   seconds ago"
4. **The server's role cannot put the datapoint** (a changed policy): the same journal shows `AccessDenied` for
   `cloudwatch:PutMetricData`; the permission is in `infra/modules/iam` (`still-alive-heartbeat`)
5. **The server cannot reach the internet or AWS** (route table, security group)

## A false ring while parked

The rule leans toward ringing when it cannot read the group's size. Check the metric exists:
`aws cloudwatch get-metric-statistics --namespace AWS/AutoScaling --metric-name GroupDesiredCapacity --dimensions Name=AutoScalingGroupName,Value=kaval-prod --start-time <2 hours ago> --end-time <now> --period 300 --statistics Maximum --profile kaval`.
No datapoints means the group's metrics collection is off (`enabled_metrics` in `infra/modules/node`): re-apply
Terraform. A lone ring right after a bring-up is not this: see the 25-minute rule in the ADR.

## Remediation

- **Server gone or stuck:** follow node-lost. Restarting the poller is reversible:
  `kubectl rollout restart deploy/kaval-prod-collector-prometheus -n kaval-prod`
- **Run the beat by hand** to see the reason: `sudo /usr/local/sbin/kaval-heartbeat; echo exit=$?`
- **Test the e-mail path** without waiting (a pretend ring, then back):
  `aws cloudwatch set-alarm-state --alarm-name kaval-prod-still-alive --state-value ALARM --state-reason test --profile kaval`
  then `--state-value OK`. The real state returns on the next evaluation
- **The first e-mail never came:** the SNS subscription is probably unconfirmed. Look for "AWS Notification -
  Subscription Confirmation" and click the link; check with
  `aws sns list-subscriptions-by-topic --topic-arn <arn> --profile kaval` (`PendingConfirmation` means not yet)

## Do not

- Do not widen the node's heartbeat permission (one verb, one namespace) or give the poller AWS credentials
- Do not "fix" a ring by changing the rule's fills, the 25 minutes, or the heartbeat's freshness limit in the
  node script (`scripts/dev/test_still_alive_alarm.py` and `test_node_heartbeat.py` exist to stop that)
- Do not delete or silence the alarm "because it is noisy": the noise is a real stop or a defect to fix
- Do not make the node script send a datapoint when it is unsure; a doubt must never look like health
