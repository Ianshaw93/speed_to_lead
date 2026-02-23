"""Add cost_log table and backfill from pipeline_runs + daily_metrics.

Revision ID: 025
Revises: 024
Create Date: 2026-02-22

Creates the cost_log append-only ledger for tracking API costs across all 3 repos.
Backfills historical costs from PipelineRun and DailyMetrics tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '025'
down_revision: Union[str, None] = '024'
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

    # --- Create cost_log table ---
    if _table_exists(conn, 'cost_log'):
        print("=== cost_log table already exists ===", flush=True)
    else:
        print("=== CREATING cost_log table ===", flush=True)
        op.create_table(
            'cost_log',
            sa.Column('id', sa.Uuid(), primary_key=True),
            sa.Column('incurred_at', sa.DateTime(timezone=True), nullable=False, index=True),
            sa.Column('project', sa.String(50), nullable=False, index=True),
            sa.Column('provider', sa.String(50), nullable=False, index=True),
            sa.Column('operation', sa.String(100), nullable=False),
            sa.Column('cost_usd', sa.Numeric(10, 6), nullable=False),
            sa.Column('units', sa.Integer(), nullable=True),
            sa.Column('unit_type', sa.String(50), nullable=True),
            sa.Column('pipeline_run_id', sa.Uuid(), sa.ForeignKey('pipeline_runs.id'), nullable=True, index=True),
            sa.Column('daily_metrics_id', sa.Uuid(), sa.ForeignKey('daily_metrics.id'), nullable=True),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # --- Backfill from PipelineRun ---
    print("=== Backfilling cost_log from pipeline_runs ===", flush=True)

    # Map: (cost_column, provider, operation, count_column, unit_type)
    pipeline_mappings = [
        ('cost_apify_google', 'apify', 'google_search', 'count_google_searches', 'searches'),
        ('cost_apify_reactions', 'apify', 'post_reactions', 'count_posts_scraped', 'posts'),
        ('cost_apify_profiles', 'apify', 'profile_scrape', 'count_profiles_scraped', 'profiles'),
        ('cost_deepseek_icp', 'deepseek', 'icp_check', 'count_icp_checks', 'checks'),
        ('cost_deepseek_personalize', 'deepseek', 'personalization', 'count_personalizations', 'messages'),
    ]

    total_backfilled = 0
    for cost_col, provider, operation, count_col, unit_type in pipeline_mappings:
        result = conn.execute(sa.text(f"""
            INSERT INTO cost_log (id, incurred_at, project, provider, operation, cost_usd, units, unit_type, pipeline_run_id, created_at)
            SELECT
                gen_random_uuid(),
                COALESCE(completed_at, started_at, created_at),
                'multichannel_outreach',
                '{provider}',
                '{operation}',
                {cost_col},
                {count_col},
                '{unit_type}',
                id,
                NOW()
            FROM pipeline_runs
            WHERE {cost_col} > 0
              AND id NOT IN (
                  SELECT pipeline_run_id FROM cost_log
                  WHERE pipeline_run_id IS NOT NULL
                    AND provider = '{provider}'
                    AND operation = '{operation}'
              )
        """))
        count = result.rowcount
        total_backfilled += count
        if count > 0:
            print(f"  {provider}/{operation}: {count} rows", flush=True)

    print(f"=== Backfilled {total_backfilled} rows from pipeline_runs ===", flush=True)

    # --- Backfill from DailyMetrics (engagement costs only) ---
    print("=== Backfilling cost_log from daily_metrics (engagement only) ===", flush=True)

    engagement_mappings = [
        ('engagement_apify_cost', 'apify', 'engagement_monitor'),
        ('engagement_deepseek_cost', 'deepseek', 'engagement_comment'),
    ]

    engagement_total = 0
    for cost_col, provider, operation in engagement_mappings:
        result = conn.execute(sa.text(f"""
            INSERT INTO cost_log (id, incurred_at, project, provider, operation, cost_usd, daily_metrics_id, created_at)
            SELECT
                gen_random_uuid(),
                date::timestamp AT TIME ZONE 'UTC',
                'speed_to_lead',
                '{provider}',
                '{operation}',
                {cost_col},
                id,
                NOW()
            FROM daily_metrics
            WHERE {cost_col} > 0
              AND id NOT IN (
                  SELECT daily_metrics_id FROM cost_log
                  WHERE daily_metrics_id IS NOT NULL
                    AND provider = '{provider}'
                    AND operation = '{operation}'
              )
        """))
        count = result.rowcount
        engagement_total += count
        if count > 0:
            print(f"  {provider}/{operation}: {count} rows", flush=True)

    print(f"=== Backfilled {engagement_total} rows from daily_metrics ===", flush=True)


def downgrade() -> None:
    op.drop_table('cost_log')
