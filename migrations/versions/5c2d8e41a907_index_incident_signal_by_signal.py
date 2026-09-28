"""index incident_signal by signal: correlation asks "is this signal linked yet?" (KAV-39)

Revision ID: 5c2d8e41a907
Revises: 0e7a61c13abe
Create Date: 2026-09-28 20:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5c2d8e41a907'
down_revision: str | None = '0e7a61c13abe'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_incident_signal_signal_id', 'incident_signal', ['signal_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_incident_signal_signal_id', table_name='incident_signal')
