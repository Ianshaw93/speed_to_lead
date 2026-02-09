"""Add daily_metrics table for reporting system.

Revision ID: 008
Revises: 007
Create Date: 2026-02-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '008'
down_revision: Union[str, None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if table exists
    result = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name = 'daily_metrics'"
    ))
    if result.fetchone():
        print("=== daily_metrics table already exists ===", flush=True)
        return

    print("=== CREATING daily_metrics table ===", flush=True)
    op.create_table(
        'daily_metrics',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('date', sa.Date(), nullable=False),
        # Outreach metrics (from multichannel-outreach)
        sa.Column('posts_scraped', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('profiles_scraped', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('icp_qualified', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('heyreach_uploaded', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('apify_cost', sa.Numeric(10, 4), nullable=False, server_default='0'),
        sa.Column('deepseek_cost', sa.Numeric(10, 4), nullable=False, server_default='0'),
        # Content metrics (from contentCreator)
        sa.Column('content_drafts_created', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('content_drafts_scheduled', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('content_drafts_posted', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('hooks_generated', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('ideas_added', sa.Integer(), nullable=False, server_default='0'),
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date'),
    )
    op.create_index('ix_daily_metrics_date', 'daily_metrics', ['date'])


def downgrade() -> None:
    op.drop_index('ix_daily_metrics_date', table_name='daily_metrics')
    op.drop_table('daily_metrics')
