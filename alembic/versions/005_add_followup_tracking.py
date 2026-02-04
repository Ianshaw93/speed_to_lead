"""Add follow-up tracking fields to prospects table.

Revision ID: 005
Revises: 004
Create Date: 2026-02-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if followup_list_id column already exists
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'prospects' AND column_name = 'followup_list_id'"
    ))
    if not result.fetchone():
        print("=== ADDING followup_list_id column ===", flush=True)
        op.add_column('prospects', sa.Column('followup_list_id', sa.Integer(), nullable=True))
    else:
        print("=== followup_list_id column already exists ===", flush=True)

    # Check if added_to_followup_at column already exists
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'prospects' AND column_name = 'added_to_followup_at'"
    ))
    if not result.fetchone():
        print("=== ADDING added_to_followup_at column ===", flush=True)
        op.add_column('prospects', sa.Column('added_to_followup_at', sa.DateTime(timezone=True), nullable=True))
    else:
        print("=== added_to_followup_at column already exists ===", flush=True)


def downgrade() -> None:
    op.drop_column('prospects', 'added_to_followup_at')
    op.drop_column('prospects', 'followup_list_id')
