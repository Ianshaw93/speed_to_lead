"""Add campaign columns to message_log table.

Revision ID: 011
Revises: 010
Create Date: 2026-02-10

Adds columns for tracking campaign info on outbound messages:
- campaign_id: HeyReach campaign ID
- campaign_name: HeyReach campaign name
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if columns already exist
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'message_log' AND column_name = 'campaign_id'"
    ))
    if result.fetchone():
        print("=== Campaign columns already exist on message_log ===", flush=True)
        return

    print("=== ADDING campaign columns to message_log ===", flush=True)

    op.add_column('message_log', sa.Column(
        'campaign_id',
        sa.Integer(),
        nullable=True,
    ))
    op.add_column('message_log', sa.Column(
        'campaign_name',
        sa.String(255),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('message_log', 'campaign_name')
    op.drop_column('message_log', 'campaign_id')
