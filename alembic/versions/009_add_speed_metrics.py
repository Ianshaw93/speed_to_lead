"""Add speed metrics columns to daily_metrics table.

Revision ID: 009
Revises: 008
Create Date: 2026-02-09

Adds columns for tracking:
- Speed to Lead: Time from outreach (heyreach_uploaded_at) to prospect's first reply
- Speed to Reply: Time from prospect's message to our response
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '009'
down_revision: Union[str, None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if columns already exist
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'daily_metrics' AND column_name = 'avg_speed_to_lead_minutes'"
    ))
    if result.fetchone():
        print("=== Speed metrics columns already exist ===", flush=True)
        return

    print("=== ADDING speed metrics columns to daily_metrics ===", flush=True)

    # Speed to Lead metrics
    op.add_column('daily_metrics', sa.Column(
        'avg_speed_to_lead_minutes',
        sa.Integer(),
        nullable=True,
    ))
    op.add_column('daily_metrics', sa.Column(
        'speed_to_lead_count',
        sa.Integer(),
        nullable=False,
        server_default='0',
    ))

    # Speed to Reply metrics
    op.add_column('daily_metrics', sa.Column(
        'avg_speed_to_reply_minutes',
        sa.Integer(),
        nullable=True,
    ))
    op.add_column('daily_metrics', sa.Column(
        'speed_to_reply_count',
        sa.Integer(),
        nullable=False,
        server_default='0',
    ))


def downgrade() -> None:
    op.drop_column('daily_metrics', 'speed_to_reply_count')
    op.drop_column('daily_metrics', 'avg_speed_to_reply_minutes')
    op.drop_column('daily_metrics', 'speed_to_lead_count')
    op.drop_column('daily_metrics', 'avg_speed_to_lead_minutes')
