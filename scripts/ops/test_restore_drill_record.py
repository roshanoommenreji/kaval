"""The verdict and the record of a restore drill (KAV-82, ADR-0049)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import restore_drill_record as rdr  # noqa: E402
from restore_drill_record import Drill  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# Two revisions in a chain; "bbb222" is the head. The real chain is checked separately below.
KNOWN = {"aaa111": None, "bbb222": "aaa111"}
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
TABLES = {t: 5 for t in rdr.EXPECTED_TABLES}
NOTIFICATION_REVISION = rdr.ADDED_BY["notification"]


def _drill(**changes: object) -> Drill:
    base: dict[str, object] = dict(
        key="postgres/kaval-20261013T193012Z.dump", taken=NOW - timedelta(hours=13),
        size_bytes=3_400_000, drilled_at=NOW, download=timedelta(seconds=4),
        restore=timedelta(seconds=19), restore_ok=True, alembic_found="bbb222",
        tables=dict(TABLES),
    )
    base.update(changes)
    return Drill(**base)  # type: ignore[arg-type]


def test_a_clean_restore_of_a_recent_dump_with_data_passes() -> None:
    assert rdr.verdict(_drill(), KNOWN) == []


def test_errors_during_the_strict_restore_fail() -> None:
    assert "did not restore cleanly" in rdr.verdict(_drill(restore_ok=False), KNOWN)[0]


def test_a_dump_of_an_empty_database_passes_but_says_what_it_does_not_prove() -> None:
    # The first real drill: production had run too little to hold any signal.
    empty = _drill(tables=dict(TABLES, signal=0, incident=0))

    assert rdr.verdict(empty, KNOWN) == []
    text = rdr.render(empty, [], head="bbb222", parents=KNOWN)
    assert "**PASS**" in text and "held no rows in signal, incident" in text
    assert rdr.notes(_drill()) == []


def test_a_missing_table_fails_and_is_named() -> None:
    tables = {k: v for k, v in TABLES.items() if k != "decision"}

    problems = rdr.verdict(_drill(tables=tables), KNOWN)

    assert len(problems) == 1 and "decision" in problems[0]


def test_a_schema_version_the_repository_has_never_heard_of_fails() -> None:
    assert "not one this repository knows" in rdr.verdict(_drill(alembic_found="zzz999"), KNOWN)[0]


def test_an_older_but_known_schema_version_is_fine() -> None:
    assert rdr.verdict(_drill(alembic_found="aaa111"), KNOWN) == []


def test_a_dump_older_than_a_day_passes_with_a_note_that_explains_why() -> None:
    old = _drill(taken=NOW - timedelta(hours=63))

    assert rdr.verdict(old, KNOWN) == []
    text = rdr.render(old, [], head="bbb222", parents=KNOWN)
    assert "older than the 24-hour target" in text and "volume snapshot" in text
    assert "older than the 24-hour target" not in rdr.render(_drill(), [], "bbb222", KNOWN)


def test_a_week_old_dump_proves_little_and_is_called_out() -> None:
    problems = rdr.verdict(_drill(taken=NOW - timedelta(days=8)), KNOWN)

    assert len(problems) == 1 and "more than a week old" in problems[0]


def test_a_restore_that_would_blow_the_recovery_target_fails() -> None:
    problems = rdr.verdict(_drill(restore=timedelta(minutes=40)), KNOWN)

    assert len(problems) == 1 and "over the target" in problems[0]


def test_the_record_states_the_data_loss_and_the_recovery_time_in_words() -> None:
    text = rdr.render(_drill(), [], head="bbb222", parents=KNOWN)

    assert "# Restore drill — 2026-10-14" in text and "**PASS**" in text
    assert "| **13 h 0 min** (target: at most 24 h 0 min) |" in text
    assert "Estimated recovery time" in text and "5 min 23 s" in text
    assert "this repository's newest" in text


def test_a_failing_record_lists_what_was_wrong() -> None:
    problems = ["the dump did not restore cleanly"]
    text = rdr.render(_drill(restore_ok=False), problems, "bbb222", KNOWN)

    assert "**FAIL**" in text and "- the dump did not restore cleanly" in text


def test_an_older_dump_without_the_table_a_later_migration_added_still_passes() -> None:
    # The first real drill: a dump from before the notification table existed.
    parents = {"aaa111": None, "bbb222": "aaa111"}
    parents[NOTIFICATION_REVISION] = "bbb222"
    older = _drill(alembic_found="bbb222")

    assert "notification" not in older.tables
    assert rdr.verdict(older, parents) == []


def test_a_dump_from_after_that_migration_must_have_the_table() -> None:
    parents = {"aaa111": None, NOTIFICATION_REVISION: "aaa111"}
    newer = _drill(alembic_found=NOTIFICATION_REVISION)

    problems = rdr.verdict(newer, parents)

    assert len(problems) == 1 and "notification" in problems[0]
    assert rdr.verdict(_drill(alembic_found=NOTIFICATION_REVISION,
                               tables=dict(TABLES, notification=2)), parents) == []


def test_the_real_migrations_have_exactly_one_head_and_it_is_found() -> None:
    parents, head = rdr.revisions(ROOT / "migrations" / "versions")

    assert head and head in parents and len(parents) >= 6
    assert rdr.ADDED_BY["notification"] in parents


def test_a_failed_drill_is_not_named_like_a_passing_one(tmp_path: Path) -> None:
    args = ["--key", "postgres/kaval-20261013T193012Z.dump", "--taken", "2026-10-13T19:30:12Z",
            "--bytes", "100", "--download-seconds", "1", "--restore-seconds", "1",
            "--restore-ok", "no", "--alembic", "nope", "--tables", "signal=0",
            "--migrations", str(ROOT / "migrations" / "versions"), "--out-dir", str(tmp_path)]

    assert rdr.main(args) == 1

    names = [p.name for p in tmp_path.iterdir()]
    assert len(names) == 1 and names[0].startswith("restore-failed-")
