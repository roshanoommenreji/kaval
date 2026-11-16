# Resume bullets, written from what shipped

For `KAV-157`. Each bullet is a claim the repository can back up, with the page that records it
beside it, so the bullet can be defended in an interview line by line. Numbers are copied from
those pages. Pick four to six for a resume; the rest are for the interview.

The target reader is a hiring manager for an AI platform, DevOps or release-engineering role. The
order puts the most unusual thing first.

## The bullets

1. **Designed and built Kaval, an operations agent that may think but not act.** The reasoning
   service has read-only credentials and can only write a proposal row; a separate executor, with
   narrow permissions, acts only on proposals that passed a Rego policy and a person's approval in
   Slack. A self-hosted Gemma 3 1B model runs on CPU. No action class is automatic yet, and the
   list that would allow one is empty by design.
   *Record:* [overview](../architecture/overview.md), [policy](../../policy/README.md).

2. **Built a release pipeline that promotes one artifact, never a rebuild, and refuses what it
   cannot verify.** Images are scanned, signed and given a parts list on build; promotion to
   production refuses an image that did not pass staging, is not a tagged release, or whose
   signature does not verify; and the cluster itself rejects an unsigned pod on staging and on
   production. A rollback workflow only goes back to a version that has run.
   *Record:* [ADR-0004](../adr/0004-environment-strategy-and-promotion.md),
   [ADR-0092](../adr/0092-promotion-refuses-an-image-whose-signature-does-not-verify.md),
   [ADR-0095](../adr/0095-production-refuses-unsigned-images-from-its-next-start-up.md).

3. **Proved the system by breaking it on purpose.** Ran five game days on staging (a crashing
   service, a bad release, a lost server, a slow service, a full disk) with a blameless write-up
   each. For one fault that Kaval diagnosed itself, detection to a held fix took 103 seconds of
   machine time and 194 seconds including the person's approval, against service-level targets
   written before the first run.
   *Record:* [post-mortems](../postmortems/README.md), [service levels](../architecture/service-levels.md),
   [mttr.svg](../architecture/mttr.svg).

4. **Made the model's safety measurable and a release gate.** A 20-case golden incident set and a
   10-case prompt-injection set score the real diagnosis pipeline; a proposed `never`-class action
   or a crashed diagnosis fails the run. Wrote a STRIDE threat model that includes the agent
   reading attacker-influenced text, then fixed what it found (a login on the approval route,
   network rules, escaped model text in Slack messages).
   *Record:* [evals](../../evals/README.md), [threat model](../architecture/threat-model.md),
   [ADR-0073](../adr/0073-the-approval-route-gets-a-login-and-the-namespace-gets-network-rules.md),
   [ADR-0082](../adr/0082-text-the-model-wrote-is-escaped-before-it-goes-into-slack.md).

5. **Held a public repository to a security standard.** Secret scanning over full history on every
   pull request, CodeQL, a published security policy, named code owners, a weekly OpenSSF
   Scorecard, and a written rotation schedule with its first rotation carried out. A cluster
   access review found no wildcard permissions, nothing able to read a Secret, and no token for
   the reasoning service, and one real finding (a token two workloads carry and do not need).
   *Record:* [access review](../architecture/access-review.md), [Lab 100](../labs/lab-100-the-cluster-half-of-the-access-review.md),
   [secret rotation](../architecture/secret-rotation.md).

6. **Ran a $50-a-month cost ceiling as a design constraint.** Budget alarms and an automatic
   shut-off were built before any compute. Production is brought up for sessions and parked. The
   first two months used $1.39 and $15.31 of usage, all covered by credits ($0.00 billed). A
   daily read-only waste scan reports idle resources, and removal is done by a separate worker
   outside the cluster, only with approval.
   *Record:* [budget plan](../cost/budget-plan.md), [actuals](../cost/actuals/),
   [ADR-0063](../adr/0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md).

7. **Treated documentation as the product.** 101 reproducible labs, 100 decision records with the
   alternatives rejected, 27 runbooks that the agent also searches, a daily journal, and about
   2,450 automated tests. Backups are drilled, not assumed: the latest restore drill passed with
   an estimated recovery of 5 minutes 6 seconds against a 30-minute target.
   *Record:* [labs](../labs/), [ADRs](../adr/), [restore drill](../drills/restore-2026-11-12.md).

## Shorter forms

One line, for a profile headline: *Built and documented an AI operations agent that proposes
fixes but cannot apply them; a person approves in Slack. Signed releases, chaos-tested, $0 billed.*

Two sentences, for a cover letter: *I built Kaval alone over a few months to learn how an AI agent
can be given a job without being given power. It diagnoses faults and wasted spend on a small
self-hosted model, and nothing changes until a person approves; every choice is written up so you
can redo it.*

## Say these limits yourself

An interviewer will find them anyway, so say them first.

- The model is small and a person approves everything. Nothing is automatic.
- It is a one-person project on one AWS account. Production is switched off between sessions, so
  long-running behaviour has been tested in sessions, not months.
- There is no phone app: Slack on a phone does the job
  ([ADR-0097](../adr/0097-phase-9-is-retired-slack-is-the-approval-surface.md)).
- Staging has no Slack; the game days approve by typing, and the real card was seen on production.

## Waiting on a record

These are true but not yet written down in the repository, so they are not in the bullets above.
Add them when their write-up lands:

- The first real approval and denial pressed in Slack on production (2026-11-16). The production
  session's write-up (`KAV-147`) records it.
- The demo video, once recorded ([the script](demo-script.md)).
