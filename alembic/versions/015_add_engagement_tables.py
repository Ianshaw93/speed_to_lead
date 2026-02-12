"""Add watched_profiles and engagement_posts tables.

Revision ID: 015
Revises: 014
Create Date: 2026-02-12

Adds tables for LinkedIn engagement monitoring:
- watched_profiles: Profiles to monitor for posts
- engagement_posts: Posts found with draft comments
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '015'
down_revision: Union[str, None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum_exists(conn, enum_name: str) -> bool:
    """Check if a PostgreSQL enum type exists."""
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = :name"
    ), {"name": enum_name})
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # Create enum types (check existence first to handle partial runs)
    if not _enum_exists(conn, 'watched_profile_category'):
        print("=== CREATING watched_profile_category enum ===", flush=True)
        conn.execute(sa.text(
            "CREATE TYPE watched_profile_category AS ENUM "
            "('prospect', 'influencer', 'icp_peer', 'competitor')"
        ))
    else:
        print("=== watched_profile_category enum already exists ===", flush=True)

    if not _enum_exists(conn, 'engagement_post_status'):
        print("=== CREATING engagement_post_status enum ===", flush=True)
        conn.execute(sa.text(
            "CREATE TYPE engagement_post_status AS ENUM "
            "('pending', 'done', 'edited', 'skipped')"
        ))
    else:
        print("=== engagement_post_status enum already exists ===", flush=True)

    # Check if watched_profiles already exists
    result = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name = 'watched_profiles'"
    ))
    if result.fetchone():
        print("=== watched_profiles table already exists ===", flush=True)
    else:
        print("=== CREATING watched_profiles table ===", flush=True)

        op.create_table(
            'watched_profiles',
            sa.Column('id', sa.Uuid(), nullable=False, primary_key=True),
            sa.Column('linkedin_url', sa.String(500), nullable=False),
            sa.Column('name', sa.String(255), nullable=False),
            sa.Column('headline', sa.Text(), nullable=True),
            sa.Column('category', sa.Enum(
                'prospect', 'influencer', 'icp_peer', 'competitor',
                name='watched_profile_category', create_type=False,
            ), nullable=False, server_default='prospect'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index('ix_watched_profiles_linkedin_url', 'watched_profiles', ['linkedin_url'], unique=True)

    # Check if engagement_posts already exists
    result = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name = 'engagement_posts'"
    ))
    if result.fetchone():
        print("=== engagement_posts table already exists ===", flush=True)
    else:
        print("=== CREATING engagement_posts table ===", flush=True)

        op.create_table(
            'engagement_posts',
            sa.Column('id', sa.Uuid(), nullable=False, primary_key=True),
            sa.Column('watched_profile_id', sa.Uuid(), sa.ForeignKey('watched_profiles.id'), nullable=False),
            sa.Column('post_url', sa.String(500), nullable=False),
            sa.Column('post_snippet', sa.Text(), nullable=True),
            sa.Column('post_summary', sa.Text(), nullable=True),
            sa.Column('draft_comment', sa.Text(), nullable=True),
            sa.Column('status', sa.Enum(
                'pending', 'done', 'edited', 'skipped',
                name='engagement_post_status', create_type=False,
            ), nullable=False, server_default='pending'),
            sa.Column('slack_message_ts', sa.String(50), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index('ix_engagement_posts_post_url', 'engagement_posts', ['post_url'], unique=True)
        op.create_index('ix_engagement_posts_watched_profile_id', 'engagement_posts', ['watched_profile_id'])


def downgrade() -> None:
    op.drop_table('engagement_posts')
    op.drop_table('watched_profiles')
    op.execute("DROP TYPE IF EXISTS engagement_post_status")
    op.execute("DROP TYPE IF EXISTS watched_profile_category")
