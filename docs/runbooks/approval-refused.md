# Approving through the API is refused (401 or 503)

`POST /v1/actions/{id}/decisions` (what `make approve` and `make chaos-approve` call) needs the
approver token ([ADR-0073](../adr/0073-the-approval-route-gets-a-login-and-the-namespace-gets-network-rules.md)).
Slack's Approve and Deny buttons do **not** use this route, so a refusal here is never the reason a
Slack approval fails. Nothing was approved by a refused call.

## Signals

- `make approve` prints `401 Unauthorized: an approver token is required` or `503 ... approvals through the API are switched off`
- `make approve` stops before calling with `set KAVAL_APPROVER_TOKEN ...` (the variable is empty)
- A gateway log line `approval call refused (switched_off|missing|wrong) from <address>`
- The counter `kaval_approval_refused_total{outcome=...}` on the gateway's `:9100/metrics`

## Likely causes

1. **`401`, outcome `missing` or `wrong`:** your shell has no token, an old one, or one from the other environment (staging and production have different tokens, and staging gets a new one every time it is rebuilt)
2. **`503`, outcome `switched_off`:** the gateway has no usable token. The Secret `kaval-approver` is missing from the namespace (the node could not read the Parameter Store value at boot), or the value is shorter than 24 characters
3. **A burst of `wrong` or `missing` from an address that is not yours:** something inside the cluster is trying the route. Treat it as an incident, not a typo

## Diagnosis

- Is the Secret there? On the node (`make staging-status` shows how to reach it): `kubectl get secret kaval-approver -n <namespace>`
- Did the node's bootstrap say so? Its log has `approver token unreadable: approval route stays shut`
- Does the parameter exist? `aws ssm describe-parameters --parameter-filters Key=Name,Values=/kaval/<env>/approver/token` (this shows that it exists, not its value)
- Which address was refused? The gateway log line names it. A pod address that is not yours is cause 3

## Remediation

- **Load your token (never print it):**
  `export KAVAL_APPROVER_TOKEN=$(aws ssm get-parameter --name /kaval/<env>/approver/token --with-decryption --query Parameter.Value --output text)` where `<env>` is `kaval-staging` or `kaval-prod`. Reversible; touches nothing
- **The Secret is missing:** start the bootstrap again on the node (it also runs every 6 hours), then restart the gateway so it picks the Secret up (`kubectl rollout restart deployment/<release>-gateway -n <namespace>`). Reversible; blast radius one deployment
- **Rotate the token** (after a suspected leak, or on a schedule): `terraform apply -replace=module.approver_token.random_password.token` in the environment's folder (show the plan first; production only when asked), then as above. The old token stops working the moment the gateway restarts. Blast radius: approvals through the API only
- **Cause 3:** do not rotate yet. Find the pod (`kubectl get pods -A -o wide`, match the address), look at what it is, and only then rotate

## Do not

- Do not paste the token into a chat, a ticket or a command line; load it into the environment as above
- Do not set the gateway's `APPROVER_TOKEN` to something short to "make it work": under 24 characters is treated as no token at all
- Do not edit the database to write a decision by hand. Approve through Slack instead, which does not need this route
