# Secret rotation: what Kaval holds, where, and when each is changed

Rotation means replacing a secret with a new one on a schedule, so that a copy stolen quietly
stops working without anyone having noticed the theft. This page lists every secret Kaval has,
where it lives, how often it is changed and how.

Decided by the owner on 2026-11-15 ([ADR-0100](../adr/0100-secrets-are-changed-yearly-and-the-laptop-key-every-90-days.md), `KAV-155`).

## The rule

1. **Every secret is changed once a year,** together, in one production session each November.
2. **The AWS access key on the laptop is changed every 90 days.** It is the most powerful
   credential in the project and changing it needs no server.
3. **Any secret is changed at once when a leak is suspected,** whatever the calendar says. A leak
   is suspected when a secret was pasted somewhere it should not be, a laptop is lost, an alert
   below fires with no innocent cause, or gitleaks finds one in a commit.

No secret value is ever in git, in a pull request, in a chat with an assistant or in a log. The
owner reads and writes them; nobody else does.

## The secrets

| Secret | What it opens | Where it lives | Changed | How it is changed | Rehearsed? |
|---|---|---|---|---|---|
| **AWS access key on the laptop** (profile `kaval`) | Everything Terraform manages in the account | The laptop's AWS credentials file | Every 90 days | IAM console: create a second key, put it in the credentials file, run `make cost-report` to prove it works, delete the old key | Yes, carried out 2026-11-16 |
| **Database passwords**: `admin`, `gateway`, `agent`, `executor`, `collector`, `monitor` (per environment) | One database login each, with only that role's rights | Parameter Store, encrypted, under `/kaval/<env>/db/` | Yearly | `terraform apply -replace` on that role's `random_password` in the environment's folder (plan shown first), then a restart of the servers: the start-up script sets each role's password from Parameter Store and recreates the cluster's Secrets ([Lab 23](../labs/lab-23-automatic-secret-recreation.md)) | **No.** Never done; rehearse on staging first (`KAV-159`) |
| **Approver token** (per environment) | Calling the gateway's approval route | Parameter Store, encrypted; copied into the `kaval-approver` Secret at start-up and every six hours | Yearly | `terraform apply -replace=module.approver_token.random_password.token`, then restart the gateway ([runbook](../runbooks/approval-refused.md)) | Steps written and the token created live; a replacement has not been done |
| **Slack bot token and app token** (production) | Posting cards and receiving button presses as Kaval's Slack app | Parameter Store, encrypted, under `/kaval/kaval-prod/slack/` | Yearly | In Slack's app settings create new tokens, `aws ssm put-parameter --overwrite` for each, restart the gateway, then revoke the old ones in Slack | No |
| **Atlassian API token** | Reading and writing Jira and Confluence as the owner | The laptop's `.env`, and GitHub's repository secret `JIRA_API_TOKEN` | Yearly (Atlassian also lets a token carry its own expiry date) | Create a new token at Atlassian, replace it in both places, run `make docs-sync`, revoke the old one | No |
| **Alert email and the budget's contact** | Nothing; listed so nobody looks for it | `.env` | Not a secret | | |

### What is deliberately not on the list

These are not rotated because there is nothing stored to steal:

- **GitHub Actions' AWS access.** The workflows hold no key. GitHub proves which repository and
  branch is asking, and AWS hands out a login that lasts one job (OIDC). The two repository
  secrets with `ROLE_ARN` in their names are addresses, not credentials.
- **The servers' AWS access.** Each server borrows its role's login from AWS, renewed by AWS
  every few hours.
- **The image checker's registry login** (`ecr-cred`). Rewritten by a timer on the server every
  six hours; it expires by itself after twelve.
- **Flux's access to the repository.** The repository is public; Flux reads it with no login.
- **Signing keys.** Images are signed without a stored key (cosign keyless): the proof is the
  build's identity, recorded in a public log.

## How a missed rotation is noticed

- The yearly change has a date: **each November, starting November 2027.** It is on the
  [ownership page](../ownership.md)'s calendar with the other recurring duties.
- The laptop key: AWS shows each key's age in the IAM console, and IAM Access Analyzer's
  unused-access view (part of the access review, `KAV-154`) lists keys older than 90 days.
- Each change is written in the table below, so the last date is always visible here.

## What tells us a secret is being misused

| Secret | Signal |
|---|---|
| Approver token | The alert `KavalApprovalRefused`: more than two wrong or missing tokens in ten minutes ([runbook](../runbooks/approval-refused.md)) |
| Database passwords | The database's failed-login lines in its log; the database server has no address reachable from outside the private network |
| Slack tokens | A message from Kaval's app that the gateway's log does not account for |
| AWS access key | The budget alarms at $42 and $46 and the hard stop at $48; CloudTrail's record of calls |
| Any | gitleaks before every commit and over the whole history on every pull request |

## Record of changes

| Date | Secret | Environment | Why | By |
|---|---|---|---|---|
| 2026-11-06 | Database passwords | production | **Not changed, on purpose:** four passwords had been printed into the server's own log; the log was on a disk that was destroyed, nothing left the server ([ADR-0077](../adr/0077-the-database-roles-script-keeps-passwords-out-of-psqls-output.md)) | Roshan |
| 2026-11-16 | AWS access key on the laptop | the account | The first scheduled change. The old key's age had never been recorded, so it was treated as due. A new key was made in the IAM console and put in the credentials file by the owner; afterwards a login with it worked and IAM listed exactly one key, active, created that day (both read back, values never shown). **Next change due by 2027-02-14** | Roshan |

## Said plainly

- **None of these changes has ever been carried out end to end.** The steps for the
  approver token are written and its creation was proven live; a database password has never been
  replaced on a running environment. Until `KAV-159` rehearses one on staging, the database row
  is a plan.
- **Changing a database password needs the servers restarted,** so on production it is done in a
  session that starts production anyway.
- **The laptop key is a long-lived key.** The stronger pattern is a login that expires by itself
  (AWS IAM Identity Center). That is a bigger change than this page and is not planned.
