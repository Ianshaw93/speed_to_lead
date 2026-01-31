"""Initial schema.

Revision ID: 001
Revises:
Create Date: 2026-01-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if types already exist and skip if so
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = 'draft_status'"
    ))
    if not result.fetchone():
        conn.execute(sa.text(
            "CREATE TYPE draft_status AS ENUM ('pending', 'approved', 'rejected', 'snoozed')"
        ))

    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = 'message_direction'"
    ))
    if not result.fetchone():
        conn.execute(sa.text(
            "CREATE TYPE message_direction AS ENUM ('inbound', 'outbound')"
        ))

    # Check if conversations table exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'conversations'"
    ))
    if not result.fetchone():
        # Create conversations table
        op.create_table(
            'conversations',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('heyreach_lead_id', sa.String(255), nullable=False),
            sa.Column('linkedin_profile_url', sa.String(500), nullable=False),
            sa.Column('lead_name', sa.String(255), nullable=False),
            sa.Column('conversation_history', postgresql.JSONB(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            'ix_conversations_heyreach_lead_id',
            'conversations',
            ['heyreach_lead_id'],
        )

    # Check if drafts table exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'drafts'"
    ))
    if not result.fetchone():
        # Create drafts table
        op.create_table(
            'drafts',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('conversation_id', sa.UUID(), nullable=False),
            sa.Column(
                'status',
                sa.Enum('pending', 'approved', 'rejected', 'snoozed', name='draft_status', create_type=False),
                nullable=False,
            ),
            sa.Column('ai_draft', sa.Text(), nullable=False),
            sa.Column('slack_message_ts', sa.String(50), nullable=True),
            sa.Column('snooze_until', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        )
        op.create_index(
            'ix_drafts_conversation_id',
            'drafts',
            ['conversation_id'],
        )

    # Check if message_log table exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'message_log'"
    ))
    if not result.fetchone():
        # Create message_log table
        op.create_table(
            'message_log',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('conversation_id', sa.UUID(), nullable=False),
            sa.Column(
                'direction',
                sa.Enum('inbound', 'outbound', name='message_direction', create_type=False),
                nullable=False,
            ),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        )
        op.create_index(
            'ix_message_log_conversation_id',
            'message_log',
            ['conversation_id'],
        )


def downgrade() -> None:
    op.drop_index('ix_message_log_conversation_id', table_name='message_log')
    op.drop_table('message_log')

    op.drop_index('ix_drafts_conversation_id', table_name='drafts')
    op.drop_table('drafts')

    op.drop_index('ix_conversations_heyreach_lead_id', table_name='conversations')
    op.drop_table('conversations')

    # Drop enums
    sa.Enum(name='message_direction').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='draft_status').drop(op.get_bind(), checkfirst=True)
