# Lab 107 — Rehearsing a secret rotation on staging

**Phase:** 8 (hardening) · **Story:** `KAV-159` · **Decision:** [ADR-0100](../adr/0100-secrets-are-changed-yearly-and-the-laptop-key-every-90-days.md) (amended 2026-11-19) · **Status:** carried out and proven live 2026-11-19

## What you will build

A proof that Kaval can change three of its secrets on a running environment, and a checker that shows it without ever printing a secret. The three are the approver token, the gateway's database password and the database's own admin password. The rehearsal runs on staging, so production's first real change is not its first attempt.

You need the repository, the owner's AWS login, Terraform, and a staging that is up (`make staging-up`, about five minutes, about 5 cents an hour).

## Why

ADR-0100 set a schedule (yearly) but proved no procedure. A password that has never been replaced on a running system is a plan, not a skill: the first attempt should fail somewhere cheap. Real companies do this in a staging copy of the system (a "game day" for credentials) before they do it on the live one.

Two limits shaped it:

- **Nobody may see a secret.** Terraform generates each value and writes it to Parameter Store, hiding it in every plan. The checks run on the servers and print only `yes`, `no`, `ok`, `refused`, a status code or a version number.
- **"Refused" needs the old value.** To prove the old token no longer works you have to try it. The checker keeps the old values in `/run/kav159` on the node (memory only, root only, gone at a reboot) and deletes them at the end.

## Part 1 — the checker

`scripts/ops/rotation_check.py` (`make rotation-check PHASE=...`), tested without AWS by `scripts/ops/test_rotation_check.py` (15 tests). It asks three places the same question, from the node and the database server over Session Manager:

| Question | How |
|---|---|
| Is a new value stored? | Parameter Store's version number went up (the number, never the value) |
| Does the cluster hold the stored value? | The Secret's value compared with the stored one, on the node, answer `yes` or `no` |
| Does the thing that checks it believe it? | The token: a call to the approval route with an empty body is answered `401` if refused, `422` if believed (the login is checked before the body, so it can never change anything). A password: a login attempt, `ok` or `refused` |

It has four phases: `before` (also keeps the old values), `applied` (new values stored, nothing caught up yet), `after` (all caught up, old refused) and `finish` (deletes the kept values). The safety tests assert that no `echo` in the node script can print a secret and that staging is the only prefix.

## Part 2 — the rehearsal, as it ran

All on staging, 2026-11-19, on the owner's typed gos.

1. `make staging-up`, then `PHASE=before`: 7 of 7 (the cluster's copy equals the stored one; the token is believed; a made-up one is refused; both logins work).
2. The plan (`terraform plan -replace=...` for the three secrets): 3 to add, 3 to destroy and 3 or 4 to change. The fourth, the database server's own read policy, was shown as "known after apply" and turned out to be rewritten identically, so the apply reported 3 changed.
3. Apply, then `PHASE=applied`: 10 of 10. The new values are stored (version 1 to 2) and **nothing has caught up**: the cluster still holds the old copy, the running gateway still believes the OLD token and refuses the new one, and the database still takes the old passwords **and refuses the new ones, admin included**.
4. **The trap, found by reading and then proven by the check.** Postgres reads its admin password only when its data disk is first made. A new admin value in Parameter Store does not reach the database. The app server's start-up script logs in as the admin to set every service login, so run as it stands it cannot. The fix is one `ALTER ROLE` inside the database, from the stored value, before the script re-runs (runbook step 1).
5. The app server's start-up script, re-run with `systemctl start kaval-gitops-bootstrap.service` (no reboot). It finished in under a minute; its second permissions pass (`kaval-db-grants.service`) finished a little later.
6. The gateway restarted (`kubectl rollout restart`), because a pod reads its Secret only at start.
7. `PHASE=after`: **13 of 13.** New token accepted, old token `401`, made-up token `401`; both new passwords accepted by the database, both old ones refused; the cluster's copies equal the stored ones. The database's log showed refused logins only from the checker's own deliberate tests.
8. `make staging-smoke NO_RECORD=1`: pods ready, no crash loop, digests match ECR, the gateway reaches a migrated database, both API reads answer `200`. (Its last line, "build is a tagged release", fails because staging runs an untagged build; that is the promotion gate and has nothing to do with secrets.)
9. `PHASE=finish` deleted the kept old values; `make staging-down` on the owner's go at 18:28: 93 resources destroyed, and its own sweep found nothing tagged staging left (0 servers, disks, networks or snapshots).

## Part 3 — what it found

| Finding | What changed |
|---|---|
| The admin password is not read again after the database is first made, so the plan's "just replace it" does not work | Runbook step 1 (`ALTER ROLE`, no value printed); a check line that proves the database still refuses the new admin value before the fix |
| No server restart is needed | The old text said the servers must be restarted. Re-running the start-up script and restarting the gateway is enough; production still has to be **up** |
| Between the apply and the restart the old token works and the new one does not | Written into the runbook as expected, and as the `applied` phase |
| The runbook the Terraform notes pointed to did not exist | [secret-rotation.md](../runbooks/secret-rotation.md) now does; the notes point to it |
| The checker's first version glued a status code to the next marker (curl prints no newline) | Fixed and given its own test |

## Part 4 — said plainly

- Rehearsed: the approver token, the **gateway** password and the **admin** password.
- Not rehearsed: the agent, executor and collector passwords (same mechanism, not carried out), the database's monitor password (set by a script on the database server), the Slack tokens, the Atlassian token.
- Production has not been rotated. Its first change is November 2027, or sooner on a suspected leak.
- The checker is staging-only. On production the check is `make prod-smoke` plus the by-hand token test.

## Cost

About 14 cents: staging was up about three hours (15:28 to 18:32 IST, from the first start-up to the teardown) at about $0.045 an hour (an estimate from running time, not yet on the bill). Most of that was waiting between steps, not work: a rehearsal done in one sitting would cost nearer 5 cents. No recurring cost.

## Try it yourself

`make staging-up`, then follow the [runbook](../runbooks/secret-rotation.md), running `make rotation-check` at each phase. End with `make staging-down` (a staging left up parks itself after four idle hours and is stopped at 02:00 IST).
