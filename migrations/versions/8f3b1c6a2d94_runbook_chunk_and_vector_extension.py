"""runbook_chunk table and the pgvector extension (KAV-40, ADR-0015)

Revision ID: 8f3b1c6a2d94
Revises: 5c2d8e41a907
Create Date: 2026-09-28 21:00:00.000000

"""
from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8f3b1c6a2d94'
down_revision: str | None = '5c2d8e41a907'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The postgres image is pgvector/pgvector:pg16 (the extension ships in it), but it must
    # still be enabled per-database. Left enabled on downgrade: dropping it would be a
    # separate, larger decision than "remove this one table", and it's idempotent either way.
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.create_table('runbook_chunk',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('heading', sa.Text(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.Vector(384), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('path', 'heading', name='uq_runbook_chunk_path_heading')
    )


def downgrade() -> None:
    op.drop_table('runbook_chunk')
