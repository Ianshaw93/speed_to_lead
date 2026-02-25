"""Add clients table for ex-client info store.

Revision ID: 027
Revises: 026
Create Date: 2026-02-24

Stores client relationship data and case study info for re-engagement outreach.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '027'
down_revision: Union[str, None] = '026'
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

    if _table_exists(conn, 'clients'):
        print("=== clients table already exists ===", flush=True)
        return

    print("=== CREATING clients table ===", flush=True)

    # Create the enum type
    conn.execute(sa.text(
        "DO $$ BEGIN "
        "CREATE TYPE client_status AS ENUM ('active', 'paused', 'churned', 'ex_client'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    ))

    op.create_table(
        'clients',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('linkedin_url', sa.String(500), nullable=True),
        sa.Column('company', sa.String(255), nullable=True),
        sa.Column('status', postgresql.ENUM(
            'active', 'paused', 'churned', 'ex_client',
            name='client_status', create_type=False,
        ), nullable=False, server_default='active'),
        sa.Column('case_study_data', sa.JSON(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('prospect_id', sa.UUID(), sa.ForeignKey('prospects.id'), nullable=True, index=True),
        sa.Column('started_at', sa.Date(), nullable=True),
        sa.Column('ended_at', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('clients')
    sa.Enum(name='client_status').drop(op.get_bind(), checkfirst=True)
