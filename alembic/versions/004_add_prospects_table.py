"""Add prospects table for tracking all outreach prospects.

Revision ID: 004
Revises: 003
Create Date: 2026-02-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if prospect_source type already exists
    result = conn.execute(sa.text(
        "SELECT typname FROM pg_type WHERE typname = 'prospect_source'"
    ))
    if not result.fetchone():
        print("=== CREATING prospect_source enum type ===", flush=True)
        conn.execute(sa.text(
            "CREATE TYPE prospect_source AS ENUM "
            "('competitor_post', 'cold_outreach', 'sales_nav', 'vayne', 'manual', 'other')"
        ))
    else:
        print("=== prospect_source enum type already exists ===", flush=True)

    # Check if prospects table already exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'prospects'"
    ))

    if not result.fetchone():
        print("=== CREATING prospects table ===", flush=True)
        op.create_table(
            'prospects',
            sa.Column('id', sa.UUID(), primary_key=True),
            sa.Column('linkedin_url', sa.String(500), unique=True, index=True, nullable=False),

            # Lead info
            sa.Column('full_name', sa.String(255), nullable=True),
            sa.Column('first_name', sa.String(100), nullable=True),
            sa.Column('last_name', sa.String(100), nullable=True),
            sa.Column('job_title', sa.String(255), nullable=True),
            sa.Column('company_name', sa.String(255), nullable=True),
            sa.Column('company_industry', sa.String(255), nullable=True),
            sa.Column('location', sa.String(255), nullable=True),
            sa.Column('headline', sa.Text(), nullable=True),

            # Source tracking
            sa.Column(
                'source_type',
                postgresql.ENUM(
                    'competitor_post', 'cold_outreach', 'sales_nav', 'vayne', 'manual', 'other',
                    name='prospect_source',
                    create_type=False
                ),
                nullable=False,
                server_default='other'
            ),
            sa.Column('source_keyword', sa.String(255), nullable=True),
            sa.Column('source_post_url', sa.String(500), nullable=True),

            # Engagement context
            sa.Column('engagement_type', sa.String(50), nullable=True),  # LIKE, INTEREST, CELEBRATE, etc.
            sa.Column('engagement_comment', sa.Text(), nullable=True),  # Their comment if applicable
            sa.Column('post_date', sa.DateTime(timezone=True), nullable=True),  # When the post was made
            sa.Column('scraped_at', sa.DateTime(timezone=True), nullable=True),  # When we scraped them

            # Outreach data
            sa.Column('personalized_message', sa.Text(), nullable=True),
            sa.Column('icp_match', sa.Boolean(), nullable=True),
            sa.Column('icp_reason', sa.Text(), nullable=True),

            # HeyReach tracking
            sa.Column('heyreach_list_id', sa.Integer(), nullable=True),
            sa.Column('heyreach_uploaded_at', sa.DateTime(timezone=True), nullable=True),

            # Link to conversation
            sa.Column('conversation_id', sa.UUID(), sa.ForeignKey('conversations.id'), nullable=True, index=True),

            # Timestamps
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        )
    else:
        print("=== prospects table already exists ===", flush=True)


def downgrade() -> None:
    op.drop_table('prospects')
    sa.Enum(name='prospect_source').drop(op.get_bind(), checkfirst=True)
