"""blast_radius.resource: one cloud resource, e.g. a disk or a backup copy (KAV-95)

The existing radii (pod ... account) have no word for one disk; `account` is refused by the policy
and `node` would be a lie. Removal (ADR-0063) files its actions at this radius. It only adds a
value, so the version still serving while this runs is not affected (it never writes it).

Revision ID: d4f7a2c9e815
Revises: c3e8a1d7f402
Create Date: 2026-10-19 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4f7a2c9e815'
down_revision: str | None = 'c3e8a1d7f402'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL 12+ allows this inside a transaction as long as the new value is not used in it.
    op.execute("ALTER TYPE blast_radius ADD VALUE IF NOT EXISTS 'resource'")


def downgrade() -> None:
    # PostgreSQL cannot remove a value from an enum. Leaving it is harmless: nothing reads a
    # radius it does not know, and the older version never writes this one.
    pass
