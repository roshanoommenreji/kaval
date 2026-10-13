# The app server has disappeared (spot reclaim, hardware fault, or ended on purpose)

The machine Kaval runs on is gone: no pods, no gateway, no messages. This is **for a person**, and
it is the one failure Kaval cannot raise an alarm about, because it ran on that machine. The
database lives on a separate server and is unaffected. Design and test:
[ADR-0046](../adr/0046-losing-the-app-server-and-how-we-test-it.md).

## Signals

- **Silence.** No Slack messages, `make mttr` and the incident list stop moving, the gateway does not
  answer. Nothing inside Kaval can report this
- AWS: the instance state is `shutting-down` / `terminated` (EC2 console, or
  `aws ec2 describe-instances --filters Name=tag:Name,Values=kaval-prod`), or an interruption notice
  for a spot instance
- Session Manager shows the instance missing or offline
- An e-mail from the outside "still alive" check (`KAV-83`, [ADR-0052](../adr/0052-the-outside-still-alive-check.md),
  [runbook](still-alive-alarm.md)): no sign of life for about 25 minutes. This ends this section's first
  bullet: silence no longer depends on someone noticing it

## Likely causes

Ordered by how often they happen.

1. **Spot reclaim** (staging; prod's app node is On-Demand since ADR-0028, so rare there)
2. **The instance failed its hardware checks** and was replaced
3. **Someone ended it** (a game day on staging, or a mistake: check who and what first)
4. **The auto-scaling group is at size zero** because the environment was paused on purpose
   (`make down` / `staging-down`), which looks identical from outside

## Diagnosis

```bash
aws ec2 describe-instances --filters Name=tag:Name,Values=kaval-<env> \
  --query "Reservations[].Instances[].[InstanceId,State.Name,LaunchTime]" --output text
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names kaval-<env> \
  --query "AutoScalingGroups[0].[DesiredCapacity,Instances[].[InstanceId,LifecycleState]]"
aws autoscaling describe-scaling-activities --auto-scaling-group-name kaval-<env> --max-items 5
```

If desired capacity is 0, it was paused on purpose: nothing is broken. If it is 1 and an instance is
`Pending` or `InService`, the replacement is on its way: **wait**, then watch for it (below).

## Remediation

- **Usually nothing.** The auto-scaling group replaces the machine by itself; the new machine installs
  k3s, Flux brings every service back, and the database server still holds all the data. Measured on
  staging (game day, [post-mortem](../postmortems/README.md)): see the table there for the time to
  each step. Watch it come back: `make staging-status` (staging) / the same by Session Manager (prod)
- If no replacement appears within ~10 minutes: look at the scaling activities above (no capacity
  for the instance type in the zone has happened to this project before, ADR-0028), and follow
  [`kaval-stopped-reporting.md`](kaval-stopped-reporting.md) once the node is back but a service is not
- Afterwards: check **the data**, not just the pods: the newest incident before the loss is still
  there (`/v1/incidents`), and any approval that was waiting for a person is still waiting
- Do **not** assume a queued action ran or did not run: look at the `execution` row for it

## Do not

- **Do not rebuild by hand while the group is replacing it**: two machines racing for the same
  address and the same database role is worse than waiting
- Do not end the database server, or run `make down`, to "reset" things: that destroys the thing that
  survived
- Do not treat silence from Slack as "all quiet" for longer than a normal gap: silence is exactly
  what a lost server looks like
