# ADR-0101: The AWS access review, and what it changes

**Status:** Proposed on 2026-11-16, waiting for the owner. The review itself (read-only) is done and is not in question; what is proposed is which findings get a change, and which are accepted. Nothing in AWS was changed.
**Deciders:** Roshan
**Related:** [ADR-0063](0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md) (the remover and its allow-list), [ADR-0100](0100-secrets-are-changed-yearly-and-the-laptop-key-every-90-days.md) (the laptop key), [access-review.md](../architecture/access-review.md) (the evidence), [Lab 101](../labs/lab-101-the-aws-half-of-the-access-review.md)
**Story:** `KAV-154`; the changes are `KAV-160` and `KAV-161`

## Context

Phase 6 promised a permission boundary on the role that removes waste. The role was built with an allow-list and no boundary. Phase 8 asked for an IAM least-privilege review, with IAM Access Analyzer kept as evidence. One read-only look at the live account answered both. It found five things (A-1 to A-5, [access-review.md](../architecture/access-review.md)):

- **No Access Analyzer exists** (A-1).
- **The budget brake may scale down any auto-scaling group in the account** (A-2).
- **No role has a permission boundary** (A-3). Thirteen roles; only two change anything.
- **The laptop key is an administrator's, with no second factor on the user** (A-4).
- **The remover's snapshot delete does not check the environment label** (A-5).

## What real companies do

1. **Review once a year and write it down.** Cheap, and the findings go stale in between.
2. **A permission boundary on every role a team creates.** The textbook answer to "stop a role being made wider than it should be". It is built for companies where many people can create roles. Here one person's Terraform creates them all.
3. **Boundaries (or service control policies) only where a mistake would hurt,** plus a scan that is easy to run again. What a small team with one account does in practice.

## Decision (proposed)

1. **Switch on IAM Access Analyzer** (external access, the whole account), in the `state` folder next to the bucket that holds Terraform's record, not inside an environment that gets destroyed. Free. Then read its findings and record them. `KAV-160`.
2. **Limit the budget brake to Kaval's own auto-scaling groups** (by their `Project` label). The brake's own proof (it fires, it stops staging's group) is repeated on staging. `KAV-161`.
3. **Attach a permission boundary to the two roles that change things: the waste remover and the budget brake.** The other eleven read, or write one named parameter, and cannot do harm that a boundary would stop. The boundary is a ceiling that names only what each role may ever do (for the remover: disks and snapshots, Kaval's own parameters, its own logs) and nothing else. `KAV-161`.
4. **Make the remover's snapshot delete require the `Env` label to match** (A-5). `KAV-161`.
5. **Accept A-4** for v1: the laptop key stays an administrator's, covered by the 90-day change. The stronger pattern is [FS-15](../future-scope.md#fs-15--a-laptop-login-that-expires-and-needs-a-second-factor), with its trigger written.

## Planning standard

- **The whole path.** Local: the boundary is tested with IAM's policy simulator, which takes a role's policy *and* a boundary and says allowed or denied, with no AWS change. Staging: applied at a staging session; the brake is fired for real and the removal ladder's five refusals are run again. Production: at its next start-up, through the normal plan on the owner's go. The analyzer is account-wide, so it has no staging step; its plan is one resource added.
- **Security.** Gain: a mistaken edit to either role cannot make it delete a server or change IAM. The risk of the change itself is the opposite one: a boundary written too tight would stop a legitimate removal or, worse, the brake. That is why the brake is fired live on staging first, and why the simulator's negative *and* positive cases are kept as tests.
- **Failure.** A removal that the boundary wrongly refuses shows as an unanswered order (the existing alarm fires at six hours). A brake that wrongly fails is the serious case: the nightly 02:00 stop runs through the same function, so both would fail together, and the budget alarm would still e-mail but could not stop anything. So the brake's proof is not optional.
- **Observability.** `make iam-review` lists every role's boundary. The analyzer's findings are listed by the same command.
- **Cost.** $0. The analyzer's external-access type is free; a boundary is a policy document.
- **Testing.** Unit: the scan's wide-statement rules on made-up policies (done, `scripts/ops/test_iam_review.py`, 10 cases). Simulator tests, positive and negative, for both boundaries. A live run on staging.
- **People.** Roshan owns the account and the laptop key. The review is repeated after any role change and at the yearly rotation.

## Rejected

- **A boundary on all thirteen roles.** Eleven of them cannot change anything that matters; the extra policies would be more to keep right for no gain.
- **An ADR that says "the allow-list is enough".** It is nearly true for the remover. But the roadmap promised the ceiling, the cost is $0, and the brake (A-2) shows the allow-list is not always as tight as it looks.
- **Service control policies.** They need AWS Organizations; this is one account.
- **Narrowing the laptop key now.** Terraform needs wide rights to build everything; a narrower key would be a project of its own. See FS-15.

## Accepted, and said plainly

- **A-4 stays open on purpose.** If the laptop is taken and its key is not yet changed, whoever has it can do anything in the account, the other project's too. The cover is the 90-day change and the key's one location.
- **The review is of the live account as of 2026-11-16, production's roles only** (staging was down). Staging's roles come from the same templates and are scanned again at its next session.

## Consequences

- Two stories carry the changes (`KAV-160`, `KAV-161`) and one carries the cluster half's fix (`KAV-162`); each goes through staging before production.
- `KAV-154` can close when the owner accepts or changes this decision, because the review is complete and every finding has a story or a written acceptance.
- The Phase 6 line "a permission boundary on the roles that act" is ticked when `KAV-161` is proven.
