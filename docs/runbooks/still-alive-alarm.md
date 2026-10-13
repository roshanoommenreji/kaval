# The outside "still alive" check has gone quiet

An e-mail from healthchecks.io says the check **kaval-prod** (or **kaval-staging**) is **down**: no sign of
life for about 25 minutes. Design: [ADR-0052](../adr/0052-the-outside-still-alive-check.md). This check lives
outside the system on purpose, so when it fires, nothing inside Kaval may be able to tell you the same thing.

## Signals

- An e-mail from healthchecks.io: "kaval-prod is DOWN" (and later "is UP" when pings return)
- No Slack messages from Kaval, `make mttr` and the incident list not moving (if production should be running)
- A red `still-alive` run on GitHub (only means "could not decide or could not ping"; not an alarm by itself)

## First question: should production be running?

Production is parked most of the time. Look at the server group:

```bash
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names kaval-prod --profile kaval \
  --query 'AutoScalingGroups[0].[DesiredCapacity,length(Instances)]'
```

- **`[0, 0]` (parked):** nothing should be pinging from inside, and the GitHub job should be doing it. The
  alarm is then about the **cover**, not production: go to "The parked cover is not working" below
- **`[1, 1]` (running):** production should be pinging; go to "Production is running but silent"
- **`[1, 0]` (wants one, has none):** the server is missing; this is [node-lost](node-lost.md)

## Production is running but silent

Likely causes, most frequent first:

1. **The server was lost or is still coming up.** A replacement takes about 2 min 10 s on staging and up to
   8 minutes with a cold start. [node-lost](node-lost.md)
2. **The poller is not finishing passes.** Prometheus is down, the database is unreachable, or the poller is
   crash-looping. [kaval-stopped-reporting](kaval-stopped-reporting.md); on the server:
   `kubectl logs -n kaval-prod deploy/kaval-prod-collector-prometheus --tail=40`
3. **The poller has no address** (the Secret is empty). The log says nothing at all about pings in that case.
   Check: `kubectl get secret kaval-heartbeat -n kaval-prod -o jsonpath='{.data.HEARTBEAT_URL}' | wc -c`
   (0 means empty; never print the value)
4. **Pings are failing.** The poller's log shows `heartbeat: pings are failing (...)` once. Reasons are fixed
   words: `HTTP 404` (the address is wrong or the check was deleted), `timed out`, `could not connect`
5. **The server cannot reach the internet** (route table, security group)

## The parked cover is not working

1. **The workflow is switched off or never ran.** GitHub disables scheduled workflows after 60 days without
   repository activity: open Actions -> still-alive, and re-enable it (or push any commit)
2. **The variable or secret is missing:** `AWS_STILL_ALIVE_ROLE_ARN` (variable) and `PROD_HEARTBEAT_URL`
   (secret). Without the variable the job is skipped, which shows as grey
3. **The run is red:** open it. "could not read the group" means the role or the AWS call is wrong;
   "ping failed" names the reason; "expected exactly one group" means the group was renamed
4. **GitHub delayed the run** by more than the 25-minute margin (rare). Run it by hand: Actions -> still-alive
   -> Run workflow

## Remediation

- **Re-run the cover by hand** (parked case): Actions -> still-alive -> Run workflow. Reversible; sends one ping
- **Change the address** (rotated, or the check was recreated). Reversible:
  1. Run `AWS_PROFILE=kaval scripts/ops/set-heartbeat.sh prod` yourself (hidden prompt)
  2. On the server, if running: refresh the Secret (`sudo systemctl start kaval-gitops-bootstrap.service`, which
     the 6-hour timer also runs) and restart the poller:
     `kubectl rollout restart deploy/kaval-prod-collector-prometheus -n kaval-prod`
  3. Confirm the check turns green on healthchecks.io
- **Pause the check** on the service while you work on it, so it does not keep e-mailing. Resume it after

## Do not

- Do not widen the `kaval-still-alive` role; describing groups is all it needs
- Do not paste the ping address into a chat, a ticket, a commit or a log; if it leaked, make a new check and
  change the address (above), because anyone with it can keep a dead system looking alive
- Do not make the cover ping when it is unsure (a change to `still_alive.py` that pings on "not running"
  instead of on "clearly parked" hides exactly the failure this exists to find)
- Do not delete or pause the check "because it is noisy": the noise is either a real stop or a defect to fix
