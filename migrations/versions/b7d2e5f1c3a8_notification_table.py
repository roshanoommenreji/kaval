"""notification: a record of what a person was told that was not a proposal (KAV-78)

Revision ID: b7d2e5f1c3a8
Revises: a1c4f9b0e3d2
Create Date: 2026-10-11 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7d2e5f1c3a8'
down_revision: str | None = 'a1c4f9b0e3d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('notification',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notification_key_created_at', 'notification', ['key', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_notification_key_created_at', table_name='notification')
    op.drop_table('notification')
