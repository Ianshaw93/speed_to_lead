"""Add funnel stage tracking columns to prospects table.

Revision ID: 012
Revises: 011
Create Date: 2026-02-10

Adds columns for tracking funnel progression:
- pitched_at: When prospect was pitched (sent calendar/call invite)
- booked_at: When prospect booked a meeting
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if columns already exist
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'prospects' AND column_name = 'pitched_at'"
    ))
    if result.fetchone():
        print("=== Funnel stage columns already exist ===", flush=True)
        return

    print("=== ADDING funnel stage columns to prospects ===", flush=True)

    op.add_column('prospects', sa.Column(
        'pitched_at',
        sa.DateTime(timezone=True),
        nullable=True,
    ))
    op.add_column('prospects', sa.Column(
        'booked_at',
        sa.DateTime(timezone=True),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('prospects', 'booked_at')
    op.drop_column('prospects', 'pitched_at')
