"""Add engagement cost fields to daily_metrics.

Revision ID: 021
Revises: 020
Create Date: 2026-02-19

Adds engagement monitoring cost tracking columns to daily_metrics table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '021'
down_revision: Union[str, None] = '020'
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
        ("engagement_apify_cost", sa.Numeric(10, 4), "0"),
        ("engagement_deepseek_cost", sa.Numeric(10, 4), "0"),
        ("engagement_checks", sa.Integer(), "0"),
        ("engagement_posts_found", sa.Integer(), "0"),
    ]

    for col_name, col_type, default in columns:
        if _column_exists(conn, "daily_metrics", col_name):
            print(f"=== Column {col_name} already exists ===", flush=True)
        else:
            print(f"=== Adding column {col_name} ===", flush=True)
            op.add_column(
                "daily_metrics",
                sa.Column(col_name, col_type, server_default=default, nullable=False),
            )


def downgrade() -> None:
    op.drop_column("daily_metrics", "engagement_posts_found")
    op.drop_column("daily_metrics", "engagement_checks")
    op.drop_column("daily_metrics", "engagement_deepseek_cost")
    op.drop_column("daily_metrics", "engagement_apify_cost")
