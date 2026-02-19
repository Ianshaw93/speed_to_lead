"""Add triggering_message column to drafts table.

Revision ID: 022
Revises: 021
Create Date: 2026-02-19

Stores the last outbound message that triggered a prospect's reply,
enabling message effectiveness tracking.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '022'
down_revision: Union[str, None] = '021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_name = '{table}' AND column_name = '{column}'"
    ))
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    if _column_exists(conn, 'drafts', 'triggering_message'):
        print("=== triggering_message column already exists ===", flush=True)
    else:
        print("=== ADDING triggering_message column to drafts ===", flush=True)
        op.add_column('drafts', sa.Column('triggering_message', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('drafts', 'triggering_message')
