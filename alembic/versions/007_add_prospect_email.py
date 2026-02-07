"""Add email column to prospects table.

Revision ID: 007
Revises: 006
Create Date: 2026-02-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '007'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Add email column to prospects
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'prospects' AND column_name = 'email'"
    ))
    if not result.fetchone():
        print("=== ADDING email column to prospects ===", flush=True)
        op.add_column('prospects', sa.Column('email', sa.String(255), nullable=True))
        op.create_index('ix_prospects_email', 'prospects', ['email'])
    else:
        print("=== email column already exists ===", flush=True)


def downgrade() -> None:
    op.drop_index('ix_prospects_email', table_name='prospects')
    op.drop_column('prospects', 'email')
