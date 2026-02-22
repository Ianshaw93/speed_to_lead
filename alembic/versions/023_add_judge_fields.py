"""Add judge quality scoring fields to drafts table.

Revision ID: 023
Revises: 022
Create Date: 2026-02-22

Adds judge_score, judge_feedback, and revision_count columns to support
the Draft -> Judge -> Revise quality loop for AI reply drafts.
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

    if _column_exists(conn, 'drafts', 'judge_score'):
        print("=== judge_score column already exists ===", flush=True)
    else:
        print("=== ADDING judge_score column to drafts ===", flush=True)
        op.add_column('drafts', sa.Column('judge_score', sa.Numeric(3, 2), nullable=True))

    if _column_exists(conn, 'drafts', 'judge_feedback'):
        print("=== judge_feedback column already exists ===", flush=True)
    else:
        print("=== ADDING judge_feedback column to drafts ===", flush=True)
        op.add_column('drafts', sa.Column('judge_feedback', sa.Text(), nullable=True))

    if _column_exists(conn, 'drafts', 'revision_count'):
        print("=== revision_count column already exists ===", flush=True)
    else:
        print("=== ADDING revision_count column to drafts ===", flush=True)
        op.add_column('drafts', sa.Column('revision_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('drafts', 'revision_count')
    op.drop_column('drafts', 'judge_feedback')
    op.drop_column('drafts', 'judge_score')
