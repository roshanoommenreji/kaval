# Lab 68 — Only one gateway copy notifies at a time: built and proven locally

**Phase:** 6 follow-up · **Story:** `KAV-100` · **Time:** about 2 hours · **Cost:** $0 so far (the staging proof is about $0.50 to $1, still to come)

[Lab 64](lab-64-closing-phase-6-on-production.md) found that production's two gateway copies sent the same "nobody has answered" alert twice. This lab closes the gap, test first, against a real Postgres. The decision is in [ADR-0069](../adr/0069-only-one-gateway-copy-notifies-at-a-time.md).

## Prerequisites

- The repository on a short branch, the project's `.venv` on the path
- Docker running, for a throwaway Postgres (no AWS, no cost)

## Part 1 — A throwaway database

```bash
export MSYS_NO_PATHCONV=1
docker run -d --rm --name kaval-test-pg -p 127.0.0.1:55432:5432 \
  -e POSTGRES_PASSWORD=testonly -e POSTGRES_USER=kaval -e POSTGRES_DB=kaval pgvector/pgvector:pg16
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_USER=kaval POSTGRES_PASSWORD=testonly POSTGRES_DB=kaval
sleep 8 && alembic upgrade head
```

`testonly` is a made-up password for a container that exists for minutes. Stop it with `docker rm -f kaval-test-pg` afterwards.

## Part 2 — The tests first

`services/gateway/tests/test_notifier_lock.py` commits real rows (the shared database fixture rolls back, which cannot show two connections racing) and removes them afterwards. Slack is a fake that takes 0.3 seconds to post, so both "copies" (two threads that start together) are inside the gap at once.

| Test | What it shows |
|---|---|
| `test_without_the_lock_two_copies_send_the_same_message_twice` | **The bug.** Two copies running the old loop send one expiry message twice. Kept so a regression would be caught |
| `test_with_the_lock_two_copies_send_it_once` | One message, one record, and exactly one copy did the round (`[False, True]`) |
| `test_a_copy_that_stood_aside_sends_nothing_and_a_later_round_still_works` | The copy that cannot get the lock sends nothing, and the next round works |
| `test_the_first_copy_gets_the_lock_and_a_second_copy_does_not` | The lock itself |
| `test_the_lock_is_free_again_after_a_pass` | Released on the normal path |
| `test_the_lock_is_released_even_when_the_pass_fails` | Released when a pass raises |

Run before the change: the file cannot even be imported (`No module named 'kaval_gateway.notifier_lock'`).

## Part 3 — The change

- `services/gateway/kaval_gateway/notifier_lock.py`: `only_one_notifier(engine)` takes `pg_try_advisory_lock` on a connection of its own and gives it back on the way out; if giving it back fails, the connection is dropped, which ends the lock.
- `services/gateway/kaval_gateway/slack_chatops.py`: `notify_round` runs the three passes (the first post of new approvals, the reminders, the health alerts) only when it holds the lock, and the loop calls it.
- `scripts/ops/approvals_drill.py` (`make approvals-report`): lists any approval message recorded twice. Tried against a planted duplicate (shown) and a clean table (`none`).

Re-run: 6 new tests pass; `pytest services` 871 passed, 19 skipped; ruff clean on what changed; mypy shows only the two errors that were there before. The database held no leftover rows afterwards.

## Part 4 — Still to do

1. **Staging with two gateway copies.** Staging runs one, so the proof run sets two for that run. Needs the user's go (`make staging-up`, about $0.50 to $1). File one approval request (`make approvals-file`), let it pass its reminder time, and check `make approvals-report`: the message was recorded once.
2. **Production** picks the change up at its next bring-up; the same report check follows `make prod-smoke`.

## Done when

- The six tests pass, and the first of them shows the bug without the lock
- ADR-0069, this lab, the runbook note, the roadmap line, the journal and the repo guide are updated
- Still open: the staging proof with two copies, then production

## Cost

$0 for the build. The staging proof, when done, is about $0.50 to $1.
