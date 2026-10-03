"""action.slack_notified_at: when the Slack ChatOps notifier posted this action (KAV-55)

Revision ID: a1c4f9b0e3d2
Revises: 8f3b1c6a2d94
Create Date: 2026-10-03 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c4f9b0e3d2'
down_revision: str | None = '8f3b1c6a2d94'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('action', sa.Column('slack_notified_at', sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column('action', 'slack_notified_at')
