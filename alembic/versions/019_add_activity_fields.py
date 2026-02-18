"""Add activity scoring fields to prospects table.

Revision ID: 019
Revises: 018
Create Date: 2026-02-18

Adds columns for LinkedIn activity scoring used by gift leads pipeline.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '019'
down_revision: Union[str, None] = '018'
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

    columns = [
        ('connection_count', sa.Integer(), {}),
        ('follower_count', sa.Integer(), {}),
        ('is_creator', sa.Boolean(), {}),
        ('activity_score', sa.Numeric(8, 2), {}),
    ]

    for col_name, col_type, kwargs in columns:
        if _column_exists(conn, 'prospects', col_name):
            print(f"=== {col_name} column already exists ===", flush=True)
        else:
            print(f"=== ADDING {col_name} column to prospects ===", flush=True)
            op.add_column('prospects', sa.Column(col_name, col_type, nullable=True, **kwargs))


def downgrade() -> None:
    for col_name in ['activity_score', 'is_creator', 'follower_count', 'connection_count']:
        op.drop_column('prospects', col_name)
