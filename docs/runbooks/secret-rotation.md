# Changing a secret: the approver token or a database password

Use this for the yearly change ([schedule](../architecture/secret-rotation.md), ADR-0100) or at once when a leak is suspected. It covers the approver token, a service's database password and the database's own admin password. The steps were rehearsed on staging on 2026-11-19 ([Lab 107](../labs/lab-107-rehearsing-a-secret-rotation-on-staging.md), `KAV-159`); production has not had one yet. The Slack tokens, the Atlassian token and the laptop's AWS key are changed by other steps, listed on the schedule page.

Nothing here prints, pastes or types a secret. Terraform makes each new value and writes it to Parameter Store; the servers read it from there.

## Signals

- The calendar says it is November (or ninety days since the laptop key, which is a different change).
- A leak is suspected: a secret pasted somewhere it should not be, a lost laptop, an alert with no innocent cause, gitleaks finding one.
- `KavalApprovalRefused` fires with no typo behind it ([approval-refused](approval-refused.md), cause 3): change the token, but only after finding what is calling.

## Before you start

- The environment must be **up**. On production that means a session, on the owner's go (a parked production has no servers to copy the new value to).
- `scripts/ops/tf-init.sh <staging|prod>` has connected Terraform to that environment's record.
- Production: after the change `make prod-smoke` must pass. The previous values stay in Parameter Store's history for recovery.
- On staging, `make rotation-check PHASE=before` first: it keeps the old values on the server, in memory only, so the check afterwards can prove they are refused.

## The order that works

Replace the secrets in one plan (show it first; production only when asked):

```bash
terraform -chdir=infra/envs/<staging|prod> plan -out=rotate.tfplan \
  -replace='module.approver_token.random_password.token' \
  -replace='module.database.random_password.db_password["gateway"]' \
  -replace='module.database.random_password.db_password["admin"]'
```

Expect, for those three: 3 to add, 3 to destroy, 3 to change (each secret's stored copy). The values show as `(sensitive value)`. Apply that file. Then, in this order:

1. **The admin password, inside the database.** Skip this and the next step cannot log in. Postgres only reads its admin password when the data disk is first made, so a new value in Parameter Store does not reach the database by itself. On the **database server** (Session Manager), without printing:

   ```bash
   P=$(aws ssm get-parameter --region ap-south-1 --with-decryption --name /kaval/<env>/db/admin-password --query Parameter.Value --output text | tr -d '[:space:]')
   printf "ALTER ROLE kaval PASSWORD '%s';\n" "$P" | docker exec -i postgres psql -U kaval -d kaval -v ON_ERROR_STOP=1
   unset P
   ```

   It must answer `ALTER ROLE` and nothing else.
2. **Re-run the app server's start-up script** (no reboot): `systemctl start kaval-gitops-bootstrap.service` on the **app server**. It copies the new token and passwords into the cluster's Secrets and sets each service login's password in the database. It then starts a second permissions pass as `kaval-db-grants.service`; wait until `systemctl is-active kaval-db-grants.service` says `inactive`.
3. **Restart what reads the changed Secret.** The pods read their Secrets only when they start. For the token and the gateway's password that is `kubectl rollout restart deployment/<release>-gateway -n <namespace>`. A changed agent, executor or collector password needs that service's deployment restarted too.
4. **Check.** Staging: `make rotation-check PHASE=after` (every line must be PASS), then `make staging-smoke NO_RECORD=1`, then `make rotation-check PHASE=finish` to delete the kept old values. Production: `make prod-smoke`, and confirm by hand that the old approver token is answered 401.

## What you will see in between (not a fault)

After the apply and before the restart, the **old** token still works and the **new** one is refused (the running gateway holds the old one in memory), and the database still takes the old passwords. `make rotation-check PHASE=applied` shows exactly this. The window is a few minutes.

## Diagnosis if a step goes wrong

- **The start-up script waits about ten minutes, then logs `schema still missing after ~10 min -- db-roles.sql skipped`.** It logs in to the database as the admin, so this is what a skipped step 1 looks like (read from the script's code; the rehearsal ran step 1 first and did not see it). Run step 1, then step 2 again.
- **A service cannot connect after the restart.** Compare its cluster Secret to Parameter Store (`rotation-check` prints `yes` or `no`). `no` means step 2 did not finish; read `journalctl -u kaval-gitops-bootstrap.service`.
- **Approvals through the API give 401 with the new token.** The gateway was not restarted (step 3) or you loaded the old token into your shell ([approval-refused](approval-refused.md)).
- **Back out:** the previous values are Parameter Store's earlier versions. Put the earlier version back with `put-parameter --overwrite` (owner only), repeat steps 1 to 3. On staging, simply rebuild it.

## Not covered by this page, and not yet rehearsed

- The database server's **monitor** password (`/kaval/<env>/db/monitor-password`) is set by a script on the database server; its change was not rehearsed.
- The **agent, executor and collector** passwords use the same mechanism as the gateway's; only the gateway's and the admin's were carried out.
- `make rotation-check` is staging-only by design. On production, the check is `make prod-smoke` plus the by-hand token test.

## Do not

- Do not print, paste or type a secret value, in a shell, a chat, a ticket or a pull request.
- Do not skip the `ALTER ROLE` step and re-run the start-up script anyway: the database still refuses the new admin password (shown live), so the script cannot update the service logins.
- Do not rotate production without the plan shown and the owner's go.
- Do not leave the kept old values on a server: run `PHASE=finish`.
