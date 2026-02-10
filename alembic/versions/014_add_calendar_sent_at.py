"""Add calendar_sent_at column to prospects table.

Revision ID: 014
Revises: 013
Create Date: 2026-02-10

Adds column for tracking when calendar link was sent to prospect.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '014'
down_revision: Union[str, None] = '013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if column already exists
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'prospects' AND column_name = 'calendar_sent_at'"
    ))
    if result.fetchone():
        print("=== calendar_sent_at column already exists ===", flush=True)
        return

    print("=== ADDING calendar_sent_at column to prospects ===", flush=True)

    op.add_column('prospects', sa.Column(
        'calendar_sent_at',
        sa.DateTime(timezone=True),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('prospects', 'calendar_sent_at')
