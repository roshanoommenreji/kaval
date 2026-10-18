# Lab 57 — The cost reader: the server asks AWS, the cluster reads the answer

**Phase:** 6 · **Story:** `KAV-92` · **Time:** about 2 hours (Parts 1 to 3 on the laptop, Part 4 on staging) ·
**Cost:** a few cents on the laptop (each Cost Explorer request is $0.01); staging about $0.05 an hour while it is up.

The AWS bill becomes signals Kaval can reason over, without any program in the cluster holding an AWS login. The
decision is [ADR-0060](../adr/0060-the-cost-reader-the-server-asks-aws-and-the-cluster-never-holds-the-login.md);
if it breaks, [the runbook](../runbooks/cost-reader-not-pulling.md).

How it works in one paragraph: a timer on the server (`kaval-cost-pull`) borrows a role that can only read costs,
asks Cost Explorer for the last 14 days, and leaves the answer in one ConfigMap. A pod, `collector-cost`, may read
that one ConfigMap and writes one `daily_cost` signal per service per settled day. Three alarms tell a person when
that stops.

## Prerequisites

- `AWS_PROFILE=kaval`; on Windows with Git Bash also `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` and `MSYS_NO_PATHCONV=1`
- Docker Desktop running (for the database and the alert-rule tests); `helm`, `terraform`, `bash`
- The project's Python environment: `export PATH="$PWD/.venv/Scripts:$PATH"` (Git Bash on Windows)

## Part 1 — Look at the real bill, once

```bash
START=$(date -u -d "14 days ago" +%Y-%m-%d); END=$(date -u +%Y-%m-%d)
aws ce get-cost-and-usage --time-period "Start=$START,End=$END" --granularity DAILY --metrics UnblendedCost \
  --filter '{"Not":{"Dimensions":{"Key":"RECORD_TYPE","Values":["Credit","Refund"]}}}' \
  --group-by Type=DIMENSION,Key=SERVICE --output json > bill.json
```

This is one billed request ($0.01) and exactly what the server's job asks. Look at it:

```bash
python - <<'PY'
import json
d = json.load(open("bill.json"))
for day in d["ResultsByTime"]:
    total = sum(float(g["Metrics"]["UnblendedCost"]["Amount"]) for g in day["Groups"])
    print(day["TimePeriod"]["Start"], f"{total:.3f}", "Estimated" if day["Estimated"] else "final")
PY
```

Seen 2026-10-18 (14 days, 4 to 17 October): complete days between $0.26 and $0.77, **every day flagged `Estimated`**, and the
last day, the 17th, at **$0.12** against about $0.70 for a normal day: **the newest day is partial**. Of 18 services only 7
ever cost anything (EC2-Other, compute, network, the other project's container service, S3, ECR, and "AWS Cost
Explorer" itself at 10 cents on the 16th, from the tests that built this lab). This is why the reader records a day only
once it is two days old, writes nothing for zero, and de-duplicates.

The recorded copy used by the tests is `services/collector/tests/fixtures/bill-2026-10-04-to-17.json` (service names
and dollars, nothing that identifies the account).

## Part 2 — The tests, in the order the data flows

```bash
# the database the tests use (throwaway; CI uses the same image)
docker run -d --rm --name kaval-test-pg -e POSTGRES_DB=kaval -e POSTGRES_USER=kaval \
  -e POSTGRES_PASSWORD=ci-only-not-a-secret -p 55432:5432 pgvector/pgvector:pg16
export POSTGRES_HOST=localhost POSTGRES_PORT=55432 POSTGRES_DB=kaval POSTGRES_USER=kaval \
  POSTGRES_PASSWORD=ci-only-not-a-secret KAVAL_REQUIRE_DB=1
alembic upgrade head

pytest scripts/dev/test_cost_pull.py          # the server's script, against stand-ins for aws and kubectl
pytest services/collector/tests/test_cost_explorer.py   # the recorded bill in, the right signals out
pytest services/agent/tests/test_correlate.py -k spike  # the spike rule, with its new dollar floor
pytest services/gateway/tests/test_health_alerts.py -k cost_reader
pytest scripts/dev/test_chart_cost.py         # the chart (needs helm)
bash scripts/dev/rules_test.sh                # the three alarms (needs Docker)
```

What each one proves, including the ones that must **refuse**:

| Test | Proves |
|---|---|
| `test_cost_pull.py` (21) | one request, made with the borrowed login and never the server's own; none when a good answer is under 20 hours old, when the cluster is not ready, or at the cap; the 60th request is allowed and the 61st is not; a new month counts again; a damaged counter counts as zero; a failed request is counted and never marks the pull good; no AWS error text, account number or ARN ever reaches the cluster; the role has one permission; the start-up script still fits EC2's 16 KB |
| `test_cost_explorer.py` (50) | the real bill parses into decimals; a wrong shape, `NaN`, a non-dollar unit and a missing service are errors, never an empty bill; only settled days are written; a second pass writes nothing; a later pull adds only the day that settled; every doubt about the handoff (missing, failed, capped, stale, from the future, half-written) is named and none looks like health |
| `test_the_real_bill_raises_no_cost_incident` | this account's ordinary two weeks, through the real spike rule, open nothing |
| `test_a_real_spike_in_the_real_bill_is_found` | the usual compute day of about 20 cents becoming $2.40 is found |
| `test_chart_cost.py` (11) | nothing rendered unless switched on; the role may `get` one ConfigMap by name and nothing else; no AWS word anywhere in it; the collector's database role and nothing stronger; refused at render time without Prometheus |
| promtool, `kaval-cost.test.yaml` (5) | quiet for two days when healthy; **one failed try that the next fixes never alerts**; still failing after four hours alerts; no good pull for 36 hours alerts, not at 35; no pod at all alerts |

Seen 2026-10-18: the whole suite, 1,091 passed.

## Part 3 — The spike rule on the real bill

The ratio-only rule (three times the median of the week before) flagged small moves on this account's real figures
(a service at 8 cents costing 30). It now also needs a rise of 25 cents. To see both sides:

```bash
pytest services/collector/tests/test_cost_explorer.py -k "real_bill or real_spike" -v
```

## Part 4 — Live on staging

All done on 2026-10-18 (the server's clock read 13:48 to 14:10 UTC). In order:

1. **The login.** `bash scripts/ops/tf-init.sh staging`, `terraform plan` in `infra/envs/staging`, shown first, applied on a go.
   Seen: **3 to add** (the role, its one permission, the server's right to borrow it), **2 to change in place** (the app-server
   group and its launch template, which carries the new job and also moves to a newer Amazon Linux image), **0 to destroy**;
   applied, then a second plan said *No changes*. Staging was parked, so no running server was touched.
2. **Prove the login can read costs and nothing else.** `aws iam simulate-principal-policy` against the role. Seen: of 14
   actions, `ce:GetCostAndUsage` **allowed** and the other 13 `implicitDeny`: `s3:PutObject`, `s3:GetObject`,
   `ec2:TerminateInstances`, `ec2:RunInstances`, `ec2:DeleteVolume`, `ssm:GetParameter`, `ssm:PutParameter`,
   `iam:CreateUser`, `iam:PassRole`, `sts:AssumeRole`, `ce:CreateAnomalyMonitor`, `budgets:ModifyBudget`,
   `lambda:InvokeFunction`. Read back: the trust names only `kaval-staging-node`, the role has one inline policy
   (`read-costs`) and none attached, and the server's role may `sts:AssumeRole` on this role only.
3. **The code.** Pull request #195, CI green, merged; the release workflow built the images and opened a staging pin
   (#196); merged.
4. **Switch it on.** `costReader.enabled: true` in staging's two settings files (#197), merged; `make staging-up`.
   (Run it without `MSYS_NO_PATHCONV=1`: that setting, needed for `/kaval/...` AWS parameter names, breaks the script's
   Terraform path under Git Bash. The first try failed on exactly that and was harmless.)
5. **Watch one pull.** Over Session Manager, read-only (a few lines of `aws ssm send-command` that print the output).
   Seen:
   - the timer fired by itself 8 minutes after boot; the job took **4 seconds** and made **1 request**
     (`calls-2026-10` = 1, `last-ok` written)
   - the ConfigMap said `ok`, with the time and `calls=1`
   - the reader wrote **65 `daily_cost` rows**, 4 to 16 October, the 17th correctly left out as partial; the figures match
     the recorded bill (EC2-Other 2026-10-12 $0.2538, AWS Cost Explorer 2026-10-16 $0.1000)
   - its health page: `kaval_cost_pull_failing 0`, `kaval_cost_pull_last_ok_timestamp_seconds` set to the pull's time,
     `kaval_cost_calls_this_month 1`, `kaval_cost_rows_written 65`
   - Prometheus on staging: target `kaval-collector-cost` up, all three rules loaded and `ok` with holds of 4 h, 5 min and
     2 min
   - **no cost incident was opened** from the real fortnight (the agent's log has no cost lines)
6. **Make it fail on purpose** (staging only, deliberately against the runbook's "do not"): put 60 in the month's counter
   file, remove `last-ok`, start the service. Seen:
   - the job refused: `cost pull: 60 requests already this month (cap 60)`, exit 1, **no request to AWS** (nothing in the
     journal), the ConfigMap `capped` with reason `capped` and **no bill in it**
   - within three tries (under two minutes, one reader pass) `kaval_cost_pull_failing` went to **1** and the reader printed
     `no good cost pull: capped`; the rows written did not change
   - restored (counter back to 1, `last-ok` removed, service started): `ok`, `calls=2`; on the next pass
     `kaval_cost_pull_failing` was back to **0** by itself and **`kaval_cost_rows_written` 0**: the second identical bill
     added no duplicates

   The four-hour hold before the message reaches Slack is the promtool test's job; waiting for it live was not worth a
   staging night.
7. **Measure.** The reader pod: **86 MiB** in use of its 128 MiB limit (the health page's own RSS reads 101 MB), 1m CPU, 0
   restarts. The server: **1,916 MB available of 3,829 MB** with all services up and the model unloaded (2,062 MB at the last
   full measurement on 2026-10-14; the reader's 86 MiB and the other services' growth account for the difference).
   The limit leaves about 40 MiB; if it is ever hit the pod restarts and the "reader missing" or stale alarms ring.

## Part 5 — Production (pending)

At production's bring-up, with its own plan shown first and the `KAV-90` checks done together
([prod-rollout runbook](../runbooks/prod-rollout.md)).

## Done when

- [x] A real bill read once and recorded as a test fixture
- [x] The reader's code, the server's script, the chart part, three alarms and the Terraform login all written, with the
      refusal cases tested
- [x] The spike rule proven quiet on this account's real fortnight and loud on a real spike
- [x] The login applied on staging and shown unable to do anything but read costs (simulation, 2026-10-18)
- [x] One real pull on staging, signals in the table, the pod's memory measured (65 rows, 86 MiB)
- [x] A capped pull makes the alarm number go to 1 on staging, and a good pull puts it back to 0 with no duplicate rows
- [ ] Production, at its bring-up
