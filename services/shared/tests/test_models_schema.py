"""Schema-shape checks against SQLAlchemy metadata. No database needed — these catch
typos in table/column/FK definitions before they ever reach a migration.
"""

from kaval_shared.models import Base

TABLES = Base.metadata.tables


def test_all_spine_tables_present() -> None:
    # A subset, not equality: other tables may exist alongside the append-only spine (e.g.
    # runbook_chunk, KAV-40's reference index, which is explicitly not part of it). This
    # still catches a typo dropping a spine table, which is the check's actual job.
    spine = {
        "signal",
        "incident",
        "incident_signal",
        "proposal",
        "action",
        "decision",
        "execution",
        "outcome",
    }
    assert spine.issubset(set(TABLES))


def test_every_table_has_id_and_created_at() -> None:
    # incident_signal is a pure association table — its primary key is the
    # (incident_id, signal_id) pair, by design, not a surrogate id.
    for name, table in TABLES.items():
        if name == "incident_signal":
            continue
        assert "id" in table.columns, f"{name} missing id"
        assert "created_at" in table.columns, f"{name} missing created_at"


def _fk_targets(table_name: str, column_name: str) -> set[str]:
    column = TABLES[table_name].columns[column_name]
    return {f"{fk.column.table.name}.{fk.column.name}" for fk in column.foreign_keys}


def test_foreign_keys_point_at_the_right_parent() -> None:
    assert _fk_targets("incident_signal", "incident_id") == {"incident.id"}
    assert _fk_targets("incident_signal", "signal_id") == {"signal.id"}
    assert _fk_targets("proposal", "incident_id") == {"incident.id"}
    assert _fk_targets("action", "proposal_id") == {"proposal.id"}
    assert _fk_targets("decision", "action_id") == {"action.id"}
    assert _fk_targets("execution", "action_id") == {"action.id"}
    assert _fk_targets("outcome", "incident_id") == {"incident.id"}


def test_decision_and_execution_are_one_per_action() -> None:
    # These enforce the "one decision, one execution per action" design call —
    # a re-decision or a retry is a new `action` row, not an update in place.
    assert _has_unique_constraint_on("decision", "action_id")
    assert _has_unique_constraint_on("execution", "action_id")


def _has_unique_constraint_on(table_name: str, column_name: str) -> bool:
    from sqlalchemy import UniqueConstraint

    table = TABLES[table_name]
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            if {c.name for c in constraint.columns} == {column_name}:
                return True
    return False


def test_proposal_confidence_is_bounded() -> None:
    from sqlalchemy import CheckConstraint

    checks = [c for c in TABLES["proposal"].constraints if isinstance(c, CheckConstraint)]
    assert any("confidence" in str(c.sqltext) for c in checks)


def test_nothing_but_incident_closed_at_and_execution_finished_at_is_nullable_by_accident() -> None:
    # A loose net for the append-only claim: everything should be required at write time
    # except the handful of fields that are genuinely set later (closing an incident,
    # finishing an execution) or are optional by design (reason, mttr on an unresolved
    # outcome, execution stdout/state on a skipped action).
    allowed_nullable = {
        ("incident", "closed_at"),
        ("decision", "reason"),
        ("execution", "stdout"),
        ("execution", "before_state"),
        ("execution", "after_state"),
        ("execution", "finished_at"),
        ("outcome", "mttr_sec"),
    }
    for table_name, table in TABLES.items():
        for column in table.columns:
            if column.nullable and column.name not in ("id",):
                assert (table_name, column.name) in allowed_nullable, (
                    f"unexpected nullable column {table_name}.{column.name}"
                )
