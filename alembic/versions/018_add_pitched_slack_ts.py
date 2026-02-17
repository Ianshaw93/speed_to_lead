"""Add pitched_slack_ts column to prospects table.

Revision ID: 018
Revises: 017
Create Date: 2026-02-17

Adds column for tracking pitched channel Slack message timestamp.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '018'
down_revision: Union[str, None] = '017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if column already exists
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'prospects' AND column_name = 'pitched_slack_ts'"
    ))
    if result.fetchone():
        print("=== pitched_slack_ts column already exists ===", flush=True)
        return

    print("=== ADDING pitched_slack_ts column to prospects ===", flush=True)

    op.add_column('prospects', sa.Column(
        'pitched_slack_ts',
        sa.String(50),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('prospects', 'pitched_slack_ts')
