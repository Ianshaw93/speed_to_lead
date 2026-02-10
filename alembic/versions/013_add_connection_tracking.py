"""Add connection tracking columns to prospects table.

Revision ID: 013
Revises: 012
Create Date: 2026-02-10

Adds columns for tracking LinkedIn connection events:
- connection_sent_at: When connection request was sent
- connection_accepted_at: When connection request was accepted
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '013'
down_revision: Union[str, None] = '012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if columns already exist
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'prospects' AND column_name = 'connection_sent_at'"
    ))
    if result.fetchone():
        print("=== Connection tracking columns already exist ===", flush=True)
        return

    print("=== ADDING connection tracking columns to prospects ===", flush=True)

    op.add_column('prospects', sa.Column(
        'connection_sent_at',
        sa.DateTime(timezone=True),
        nullable=True,
    ))
    op.add_column('prospects', sa.Column(
        'connection_accepted_at',
        sa.DateTime(timezone=True),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('prospects', 'connection_accepted_at')
    op.drop_column('prospects', 'connection_sent_at')
