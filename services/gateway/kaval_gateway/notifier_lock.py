"""Only one gateway copy notifies at a time (KAV-100, ADR-0069).

Production runs two gateway copies, and each runs the notifier loop. Every pass asks the database
"was this message already sent?" and then posts, and nothing stood between the two steps, so on
2026-10-27 both copies posted the same "nobody has answered" alert in the same minute.

The fix is a Postgres advisory lock: a number any connection may try to hold, that the database
gives to one connection at a time and takes back by itself the moment that connection ends. A copy
that cannot get it skips the round and tries again at the next poll.

It is held on a connection of its own, not on the pass's session: a session hands its connection
back to the pool every time it commits (the passes commit after each message), and a session-level
lock belongs to the connection, not to the session."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)

# An arbitrary constant, the same in every copy. Nothing else in Kaval takes advisory locks.
NOTIFIER_LOCK_KEY = 7_340_100


@contextmanager
def only_one_notifier(engine: Engine) -> Iterator[bool]:
    """Yield True if this copy now holds the notifier lock, False if another copy does.

    The lock is released on the way out, also when the body raises. If releasing it fails the
    connection is thrown away rather than returned to the pool, which ends the lock too."""
    connection = engine.connect()
    try:
        got = bool(connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": NOTIFIER_LOCK_KEY}
        ).scalar())
        connection.commit()
        try:
            yield got
        finally:
            if got:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": NOTIFIER_LOCK_KEY}
                    )
                    connection.commit()
                except Exception:  # noqa: BLE001 - see docstring
                    logger.exception("notifier_lock: could not release the lock, dropping the "
                                     "connection so the database releases it")
                    connection.invalidate()
    finally:
        connection.close()
