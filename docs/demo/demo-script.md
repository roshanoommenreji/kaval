# Demo script: five minutes, "here is the problem, here is the thing working"

For the Phase 8 demo video (`KAV-157`). This page is the script, the shot list and the checklist for
recording it. **The video is not recorded, on purpose:** on 2026-11-16 the recording moved to
[future scope, FS-2](../future-scope.md#fs-2--demo-craft) and out of v1's exit gate. The script
stays ready for the day it is wanted.

Write-up rule: say only what the repository can back up. Each claim below names the page that
records it, so a viewer who pauses on it can check it.

## The one sentence

> Most AI-ops demos let a model run commands on your cluster and hope. Kaval is built the other way
> round: the part that thinks has no power, and a person presses the button.

## Who it is for

A hiring manager or engineer who has two minutes to decide whether this is real. They do not
know Kubernetes well. So: no command-line scrolling, no jargon without a half-sentence of meaning,
and the evidence on screen, not only in the voice-over.

## The five minutes

| Time | What is on screen | What is said (the gist, in your own words) | The proof behind it |
|---|---|---|---|
| 0:00 to 0:25 | The README's top and the one-line flow drawing | The sentence above. Then: two kinds of trouble, a failing service and wasted money, one machine | [README](../../README.md) |
| 0:25 to 1:00 | The System view of the dashboard, with the agent box and the executor box highlighted | The agent can only write a row in a table. The executor is the only part that can change anything. A confused or tricked model can only suggest harm | [overview.md](../architecture/overview.md), [threat model](../architecture/threat-model.md) |
| 1:00 to 2:30 | **The fault, on staging.** A service is made to crash; the incident appears; Kaval's diagnosis appears; the proposal waits for a person | Say what broke in plain words, then read the proposal out: what it thinks, how sure it is, what it wants to do. Say on screen that staging has no Slack, so the approval here is typed | [Game day, 2026-10-15](../postmortems/2026-10-15-oom-diagnosed-by-itself.md) |
| 2:30 to 3:15 | The approval, then the executor's record and the service recovering | One approval. Then the time breakdown: detect, diagnose, wait for the person, queue, fix | The same game day: 103 s of machine time, 194 s with the person; chart: [mttr.svg](../architecture/mttr.svg) |
| 3:15 to 4:00 | **The real Slack card, on a phone, from production.** A cost card (a forgotten snapshot): press **Deny**. Show the row saying "denied" and the snapshot still there | A "no" is recorded too, and nothing happens. The same card has an Approve button; that is what a person presses to let the removal worker act. Autonomy is earned: every action class is still "ask" | [policy](../../policy/README.md), `policy/promotions.json` is empty |
| 4:00 to 4:40 | A release page: image signed, an unsigned test image refused by the cluster | Nothing unsigned can run, in staging or production | [Lab 97](../labs/lab-97-production-refuses-unsigned-images.md) |
| 4:40 to 5:00 | The cost page, the "does not do" section, the lab count | It costs cents when it is up and is switched off between sessions. It is a one-person project; here is what it does not do. 101 labs and 100 decision records let you redo it | [budget plan](../cost/budget-plan.md), [README](../../README.md) |

If it runs long, cut the 4:00 to 4:40 block first. It is the easiest to say in words.

## What has to be true before recording

1. **Two live sessions, each with your own typed go.** The fault part (1:00 to 3:15) needs staging
   up (`make staging-up`, about 5 cents an hour, parks itself after 4 idle hours and is stopped at
   02:00 IST) and a game day run (`make chaos-run`, [runbook](../runbooks/README.md)). The Slack
   part (3:15 to 4:00) needs **production** up (about 4.5 cents an hour), because only production
   is connected to Slack. They can be recorded on different days and edited together; say which is
   which on screen. Nothing here is started without you saying so.
2. **Press Deny on camera, not Approve.** On production an Approve deletes a real snapshot. Deny
   is recorded and changes nothing. If you want an Approve on screen, choose an old snapshot you
   would have deleted anyway, and check it first.
3. **A card has to exist.** The waste scan files asks when it finds something; unanswered ones
   expire after 4 hours ([ADR-0048](../adr/0048-approvals-nobody-answers-expiry-and-escalation.md)),
   so record soon after production comes up.
4. **A rehearsal against a stopwatch, twice** ([future-scope FS-2](../future-scope.md)): the first
   take always runs long.
5. **Record the screen at a size a phone can read.** Zoom the terminal until the text is large.
6. **A fallback.** If the live fault does not behave on the day, play back the recorded game day's
   log and say so on screen. Never imply a recording is live.

## What must never appear on screen

The repository is public and so will the video be.

- AWS account numbers, ARNs, endpoints, Slack tokens, channel and user IDs, the approver token.
  The scripts that read the cluster already hide twelve-digit numbers; the Slack app and AWS
  console pages do not. Use the repository and the dashboard for visuals, and keep the AWS and
  Slack settings pages closed.
- `.env`, `C:\Users\...\.aws\credentials` and any terminal that has printed a secret.
- A browser's saved logins or the bookmarks bar.

## After recording

Upload it somewhere that lets you edit the description later, put the link here and in the
README's top, update FS-2 in `docs/future-scope.md` and the struck-through line in the roadmap,
and run `make docs-sync`.

**Recorded video:** *(not recorded; parked in future scope, FS-2)*
