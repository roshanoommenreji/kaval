# ADR-0100: Secrets are changed yearly, and the laptop's AWS key every 90 days

**Status:** Accepted on 2026-11-15 (Roshan chose "Yearly, laptop key 90 days" from three options). A documentation change: no secret was changed and nothing was deployed.
**Deciders:** Roshan
**Related:** [ADR-0027](0027-automatic-secret-and-role-recreation.md) (how secrets reach the cluster), [ADR-0073](0073-the-approval-route-gets-a-login-and-the-namespace-gets-network-rules.md) (the approver token), [ADR-0077](0077-the-database-roles-script-keeps-passwords-out-of-psqls-output.md) (the decision not to rotate in November 2026), [ADR-0071](0071-the-public-repo-gets-a-security-policy-owners-and-a-weekly-scorecard.md) (public-repo hygiene, whose last open item this is)
**Story:** `KAV-155`

## Context

The threat model's row T-SLACK-3 has said "No rotation schedule yet" since 2026-11-01, and the roadmap's public-repo hygiene line stayed half done for the same reason. Kaval holds five kinds of secret: database passwords, the approver token, two Slack tokens, an Atlassian token, and the AWS access key on the owner's laptop. None has ever been changed on a schedule, and nothing said when one should be.

Two facts shape the answer:

- **Production is parked most of the time.** Changing a database password needs the servers restarted, so a rotation of those costs a production session.
- **The laptop's key is different in kind.** It can do everything Terraform can, it is long-lived, and it can be changed in two minutes with nothing running.

## What real companies do

1. **Everything every 90 days.** The long-standing compliance default (PCI-style rules). Predictable, and a lot of ceremony.
2. **Short-lived credentials wherever possible, and a slow schedule for the rest.** The current direction: remove stored secrets (OIDC, instance roles), and rotate what is left on a longer period or on an event.
3. **Only when a leak is suspected.** Least work, and a quietly stolen secret stays valid for ever.

Kaval already follows pattern 2 for most of its access: GitHub Actions, the servers and the image checker hold no long-lived secret at all.

## Decision

1. **Every stored secret is changed once a year,** together, in one production session each November, starting November 2027.
2. **The AWS access key on the laptop is changed every 90 days.**
3. **Any secret is changed at once when a leak is suspected.**
4. The list of secrets, where each lives, how each is changed and the record of changes is [docs/architecture/secret-rotation.md](../architecture/secret-rotation.md). Each change is written into its table.
5. **A rotation is rehearsed on staging before it is relied on** (`KAV-159`): a database password and the approver token, in a staging session that happens anyway.

## Planning standard

- **The whole path:** nothing is deployed by this decision. The rehearsal goes staging first; the first real rotation is production's, in a session, on the owner's go.
- **Security:** the owner reads and writes secret values; no assistant, log or pull request ever holds one. The page lists names and homes only.
- **Failure:** a rotation that goes wrong leaves a service unable to log in. That is why it is rehearsed on staging, and why on production it is done in a session with `make prod-smoke` afterwards and the previous value still recoverable from Parameter Store's history.
- **Observability:** the page's record table shows the last change; the laptop key's age is visible in IAM (not in Access Analyzer: its unused-access view is the paid kind, and `KAV-160` switched on only the free external-access kind, Lab 105); misuse signals are listed per secret.
- **Cost:** $0 for the laptop key; a few cents for a session, once a year.
- **Testing:** `KAV-159` is the test. Until it passes, the database row of the page is marked "not rehearsed".
- **People:** the owner; the dates are on the [ownership page](../ownership.md)'s list of recurring duties.

## Accepted, and said plainly

- **No rotation has been carried out end to end.** This ADR sets a schedule; it does not prove a procedure.
- **A year is a long time for a Slack token.** It is accepted because the token can only post to and listen in one workspace's one app, production is mostly off, and a leak has its own rule.
- **The laptop key remains a long-lived key.** A login that expires by itself (IAM Identity Center) is the stronger pattern and is not planned.
- **The age of the current laptop key is not recorded,** so its first change is due now. *(Carried out by the owner on 2026-11-16; the next is due by 2027-02-14. See the record on the [schedule page](../architecture/secret-rotation.md).)*

## Rejected

- **Everything every 90 days:** four production sessions a year for a system that is parked, to rotate secrets that have not left Parameter Store.
- **Only on suspicion:** it is not a schedule, and it is weaker than what most reviewers accept.
- **Automatic rotation (AWS Secrets Manager):** about $0.40 a secret a month, and a new moving part, for six secrets on a parked system.

## Consequences

- The threat model's T-SLACK-3 and the roadmap's hygiene line close.
- Two dates now exist that somebody has to keep: every 90 days and each November.
- `KAV-159` is new v1 work, small, and rides on a staging session.
