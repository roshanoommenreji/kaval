"""action.slack_ts: which Slack message carries this action's buttons (KAV-82)

Revision ID: c3e8a1d7f402
Revises: b7d2e5f1c3a8
Create Date: 2026-10-13 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3e8a1d7f402'
down_revision: str | None = 'b7d2e5f1c3a8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('action', sa.Column('slack_ts', sa.Text()))


def downgrade() -> None:
    op.drop_column('action', 'slack_ts')
