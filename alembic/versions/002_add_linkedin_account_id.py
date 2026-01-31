"""Add linkedin_account_id to conversations.

Revision ID: 002
Revises: 001
Create Date: 2026-01-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if column already exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'conversations' AND column_name = 'linkedin_account_id'"
    ))

    if not result.fetchone():
        print("=== ADDING linkedin_account_id column ===", flush=True)
        op.add_column(
            'conversations',
            sa.Column('linkedin_account_id', sa.String(100), nullable=True)
        )
    else:
        print("=== linkedin_account_id column already exists ===", flush=True)


def downgrade() -> None:
    op.drop_column('conversations', 'linkedin_account_id')
