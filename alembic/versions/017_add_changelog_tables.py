"""Add changelog and prompt_versions tables.

Revision ID: 017
Revises: 016
Create Date: 2026-02-16

Adds tables for tracking system changes and prompt versioning:
- prompt_versions: Snapshots of prompt templates for linking to prospects
- changelog: All changes affecting outreach results
- prompt_version_id column on prospects table
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '017'
down_revision: Union[str, None] = '016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum_exists(conn, enum_name: str) -> bool:
    """Check if a PostgreSQL enum type exists."""
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = :name"
    ), {"name": enum_name})
    return result.fetchone() is not None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists."""
    result = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name = :name"
    ), {"name": table_name})
    return result.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists on a table."""
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = :table AND column_name = :col"
    ), {"table": table_name, "col": column_name})
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # Create changelog_category enum type
    if not _enum_exists(conn, 'changelog_category'):
        print("=== CREATING changelog_category enum ===", flush=True)
        conn.execute(sa.text(
            "CREATE TYPE changelog_category AS ENUM "
            "('prompt', 'icp_filter', 'prospect_source', 'pipeline_config', "
            "'validation', 'model', 'ab_test', 'infrastructure', 'heyreach', 'stage_prompt')"
        ))
    else:
        print("=== changelog_category enum already exists ===", flush=True)

    # Create prompt_versions table
    if _table_exists(conn, 'prompt_versions'):
        print("=== prompt_versions table already exists ===", flush=True)
    else:
        print("=== CREATING prompt_versions table ===", flush=True)
        op.create_table(
            'prompt_versions',
            sa.Column('id', sa.Uuid(), nullable=False, primary_key=True),
            sa.Column('prompt_name', sa.String(255), nullable=False),
            sa.Column('prompt_hash', sa.String(64), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('git_commit', sa.String(40), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index('ix_prompt_versions_prompt_name', 'prompt_versions', ['prompt_name'])
        op.create_index('ix_prompt_versions_prompt_hash', 'prompt_versions', ['prompt_hash'], unique=True)

    # Create changelog table
    if _table_exists(conn, 'changelog'):
        print("=== changelog table already exists ===", flush=True)
    else:
        print("=== CREATING changelog table ===", flush=True)
        op.create_table(
            'changelog',
            sa.Column('id', sa.Uuid(), nullable=False, primary_key=True),
            sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
            sa.Column('category', sa.Enum(
                'prompt', 'icp_filter', 'prospect_source', 'pipeline_config',
                'validation', 'model', 'ab_test', 'infrastructure', 'heyreach', 'stage_prompt',
                name='changelog_category', create_type=False,
            ), nullable=False),
            sa.Column('component', sa.String(255), nullable=False),
            sa.Column('change_type', sa.String(50), nullable=False),
            sa.Column('description', sa.Text(), nullable=False),
            sa.Column('details', sa.JSON(), nullable=True),
            sa.Column('git_commit', sa.String(40), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index('ix_changelog_timestamp', 'changelog', ['timestamp'])

    # Add prompt_version_id column to prospects
    if _column_exists(conn, 'prospects', 'prompt_version_id'):
        print("=== prompt_version_id column already exists on prospects ===", flush=True)
    else:
        print("=== ADDING prompt_version_id to prospects ===", flush=True)
        op.add_column(
            'prospects',
            sa.Column('prompt_version_id', sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            'fk_prospects_prompt_version_id',
            'prospects',
            'prompt_versions',
            ['prompt_version_id'],
            ['id'],
        )


def downgrade() -> None:
    op.drop_constraint('fk_prospects_prompt_version_id', 'prospects', type_='foreignkey')
    op.drop_column('prospects', 'prompt_version_id')
    op.drop_table('changelog')
    op.drop_table('prompt_versions')
    op.execute("DROP TYPE IF EXISTS changelog_category")
