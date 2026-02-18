"""Add pipeline_runs table and backfill prospect names from conversations.

Revision ID: 020
Revises: 019
Create Date: 2026-02-18

Creates pipeline_runs table for tracking pipeline executions with metrics and costs.
Also backfills prospect full_name/first_name/last_name from linked conversations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '020'
down_revision: Union[str, None] = '019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    result = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_name = '{table}'"
    ))
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # --- Create pipeline_runs table ---
    if _table_exists(conn, 'pipeline_runs'):
        print("=== pipeline_runs table already exists ===", flush=True)
    else:
        print("=== CREATING pipeline_runs table ===", flush=True)
        op.create_table(
            'pipeline_runs',
            sa.Column('id', sa.Uuid(), primary_key=True),
            sa.Column('run_type', sa.String(50), nullable=False, index=True),
            sa.Column('prospect_url', sa.String(500), nullable=True),
            sa.Column('prospect_name', sa.String(255), nullable=True),
            sa.Column('icp_description', sa.Text(), nullable=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='started'),

            # Pipeline metrics
            sa.Column('queries_generated', sa.Integer(), server_default='0'),
            sa.Column('posts_found', sa.Integer(), server_default='0'),
            sa.Column('engagers_found', sa.Integer(), server_default='0'),
            sa.Column('profiles_scraped', sa.Integer(), server_default='0'),
            sa.Column('location_filtered', sa.Integer(), server_default='0'),
            sa.Column('icp_qualified', sa.Integer(), server_default='0'),
            sa.Column('final_leads', sa.Integer(), server_default='0'),

            # Cost breakdown
            sa.Column('cost_apify_google', sa.Numeric(10, 4), server_default='0'),
            sa.Column('cost_apify_reactions', sa.Numeric(10, 4), server_default='0'),
            sa.Column('cost_apify_profiles', sa.Numeric(10, 4), server_default='0'),
            sa.Column('cost_deepseek_icp', sa.Numeric(10, 4), server_default='0'),
            sa.Column('cost_deepseek_personalize', sa.Numeric(10, 4), server_default='0'),
            sa.Column('cost_total', sa.Numeric(10, 4), server_default='0'),

            # API call counts
            sa.Column('count_google_searches', sa.Integer(), server_default='0'),
            sa.Column('count_posts_scraped', sa.Integer(), server_default='0'),
            sa.Column('count_profiles_scraped', sa.Integer(), server_default='0'),
            sa.Column('count_icp_checks', sa.Integer(), server_default='0'),
            sa.Column('count_personalizations', sa.Integer(), server_default='0'),

            # Timing
            sa.Column('started_at', sa.DateTime(timezone=True)),
            sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('duration_seconds', sa.Integer(), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),

            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # --- Backfill prospect names from conversations ---
    print("=== Backfilling prospect names from conversations ===", flush=True)
    result = conn.execute(sa.text("""
        UPDATE prospects p
        SET full_name = c.lead_name,
            first_name = split_part(c.lead_name, ' ', 1),
            last_name = CASE
                WHEN position(' ' in c.lead_name) > 0
                THEN substring(c.lead_name from position(' ' in c.lead_name) + 1)
                ELSE NULL
            END
        FROM conversations c
        WHERE p.conversation_id = c.id
          AND p.full_name IS NULL
          AND c.lead_name IS NOT NULL
          AND c.lead_name != ''
    """))
    print(f"=== Backfilled {result.rowcount} prospect names ===", flush=True)


def downgrade() -> None:
    op.drop_table('pipeline_runs')
