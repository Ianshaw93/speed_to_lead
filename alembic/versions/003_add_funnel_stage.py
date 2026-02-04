"""Add funnel_stage to conversations.

Revision ID: 003
Revises: 002
Create Date: 2026-02-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if funnel_stage type already exists
    result = conn.execute(sa.text(
        "SELECT typname FROM pg_type WHERE typname = 'funnel_stage'"
    ))
    if not result.fetchone():
        print("=== CREATING funnel_stage enum type ===", flush=True)
        conn.execute(sa.text(
            "CREATE TYPE funnel_stage AS ENUM "
            "('initiated', 'positive_reply', 'pitched', 'calendar_sent', 'booked', 'regeneration')"
        ))
    else:
        print("=== funnel_stage enum type already exists ===", flush=True)

    # Check if column already exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'conversations' AND column_name = 'funnel_stage'"
    ))

    if not result.fetchone():
        print("=== ADDING funnel_stage column ===", flush=True)
        op.add_column(
            'conversations',
            sa.Column(
                'funnel_stage',
                postgresql.ENUM(
                    'initiated', 'positive_reply', 'pitched',
                    'calendar_sent', 'booked', 'regeneration',
                    name='funnel_stage',
                    create_type=False
                ),
                nullable=True
            )
        )
    else:
        print("=== funnel_stage column already exists ===", flush=True)


def downgrade() -> None:
    op.drop_column('conversations', 'funnel_stage')
    sa.Enum(name='funnel_stage').drop(op.get_bind(), checkfirst=True)
