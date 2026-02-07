"""Add classification fields to drafts and ICP feedback table.

Revision ID: 006
Revises: 005
Create Date: 2026-02-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create reply_classification enum type
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = 'reply_classification'"
    ))
    if not result.fetchone():
        print("=== CREATING reply_classification enum ===", flush=True)
        op.execute(
            "CREATE TYPE reply_classification AS ENUM ('positive', 'not_interested', 'not_icp')"
        )
    else:
        print("=== reply_classification enum already exists ===", flush=True)

    # Add is_first_reply column to drafts
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'drafts' AND column_name = 'is_first_reply'"
    ))
    if not result.fetchone():
        print("=== ADDING is_first_reply column ===", flush=True)
        op.add_column('drafts', sa.Column('is_first_reply', sa.Boolean(), nullable=False, server_default='false'))
    else:
        print("=== is_first_reply column already exists ===", flush=True)

    # Add classification column to drafts
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'drafts' AND column_name = 'classification'"
    ))
    if not result.fetchone():
        print("=== ADDING classification column ===", flush=True)
        op.add_column(
            'drafts',
            sa.Column(
                'classification',
                sa.Enum('positive', 'not_interested', 'not_icp', name='reply_classification'),
                nullable=True
            )
        )
    else:
        print("=== classification column already exists ===", flush=True)

    # Add classified_at column to drafts
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'drafts' AND column_name = 'classified_at'"
    ))
    if not result.fetchone():
        print("=== ADDING classified_at column ===", flush=True)
        op.add_column('drafts', sa.Column('classified_at', sa.DateTime(timezone=True), nullable=True))
    else:
        print("=== classified_at column already exists ===", flush=True)

    # Create icp_feedback table
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'icp_feedback'"
    ))
    if not result.fetchone():
        print("=== CREATING icp_feedback table ===", flush=True)
        op.create_table(
            'icp_feedback',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('lead_name', sa.String(255), nullable=False),
            sa.Column('linkedin_url', sa.String(500), nullable=False),
            sa.Column('job_title', sa.String(255), nullable=True),
            sa.Column('company_name', sa.String(255), nullable=True),
            sa.Column('original_icp_match', sa.Boolean(), nullable=True),
            sa.Column('original_icp_reason', sa.Text(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('marked_by_slack_user', sa.String(100), nullable=True),
            sa.Column('draft_id', sa.UUID(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['draft_id'], ['drafts.id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_icp_feedback_linkedin_url', 'icp_feedback', ['linkedin_url'])
        op.create_index('ix_icp_feedback_draft_id', 'icp_feedback', ['draft_id'])
    else:
        print("=== icp_feedback table already exists ===", flush=True)


def downgrade() -> None:
    # Drop icp_feedback table
    op.drop_index('ix_icp_feedback_draft_id', table_name='icp_feedback')
    op.drop_index('ix_icp_feedback_linkedin_url', table_name='icp_feedback')
    op.drop_table('icp_feedback')

    # Drop columns from drafts
    op.drop_column('drafts', 'classified_at')
    op.drop_column('drafts', 'classification')
    op.drop_column('drafts', 'is_first_reply')

    # Drop enum type
    op.execute("DROP TYPE reply_classification")
