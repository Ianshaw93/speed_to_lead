"""Add actual_sent_text column to drafts table.

Revision ID: 023
Revises: 022
Create Date: 2026-02-23

Captures what was actually sent (which may differ from ai_draft if the
user edited before sending). Used by the dynamic example retriever to
prefer examples where the AI got it right.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '023'
down_revision: Union[str, None] = '022'
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

    if _column_exists(conn, 'drafts', 'actual_sent_text'):
        print("=== actual_sent_text column already exists ===", flush=True)
    else:
        print("=== ADDING actual_sent_text column to drafts ===", flush=True)
        op.add_column('drafts', sa.Column('actual_sent_text', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('drafts', 'actual_sent_text')
