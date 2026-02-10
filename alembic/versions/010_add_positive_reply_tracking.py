"""Add positive reply tracking columns to prospects table.

Revision ID: 010
Revises: 009
Create Date: 2026-02-10

Adds columns for tracking positive replies:
- positive_reply_at: When prospect first replied positively
- positive_reply_notes: Notes about the reply/follow-up status
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '010'
down_revision: Union[str, None] = '009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if columns already exist
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'prospects' AND column_name = 'positive_reply_at'"
    ))
    if result.fetchone():
        print("=== Positive reply columns already exist ===", flush=True)
        return

    print("=== ADDING positive reply columns to prospects ===", flush=True)

    op.add_column('prospects', sa.Column(
        'positive_reply_at',
        sa.DateTime(timezone=True),
        nullable=True,
    ))
    op.add_column('prospects', sa.Column(
        'positive_reply_notes',
        sa.Text(),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('prospects', 'positive_reply_notes')
    op.drop_column('prospects', 'positive_reply_at')
