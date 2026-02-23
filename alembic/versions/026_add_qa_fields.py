"""Add QA audit trail fields and learning/guideline tables.

Revision ID: 026
Revises: 025
Create Date: 2026-02-22

Phase 1 of multi-agent QA loop:
- Adds QA fields to drafts table (original_ai_draft, human_edited_draft, qa_score, etc.)
- Creates draft_learnings table for tracking what humans change
- Creates qa_guidelines table for learned QA rules
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '026'
down_revision: Union[str, None] = '025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_name = '{table}' AND column_name = '{column}'"
    ))
    return result.fetchone() is not None


def _table_exists(conn, table: str) -> bool:
    result = conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_name = '{table}'"
    ))
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # --- Add QA columns to drafts table ---
    draft_columns = {
        'original_ai_draft': sa.Text(),
        'human_edited_draft': sa.Text(),
        'qa_score': sa.Numeric(3, 1),
        'qa_verdict': sa.String(20),
        'qa_issues': sa.JSON(),
        'qa_model': sa.String(100),
        'qa_cost_usd': sa.Numeric(10, 6),
    }
    for col_name, col_type in draft_columns.items():
        if _column_exists(conn, 'drafts', col_name):
            print(f"=== {col_name} column already exists ===", flush=True)
        else:
            print(f"=== ADDING {col_name} column to drafts ===", flush=True)
            op.add_column('drafts', sa.Column(col_name, col_type, nullable=True))

    # --- Create draft_learnings table ---
    if _table_exists(conn, 'draft_learnings'):
        print("=== draft_learnings table already exists ===", flush=True)
    else:
        print("=== CREATING draft_learnings table ===", flush=True)
        # Create the enum type via raw SQL (IF NOT EXISTS avoids duplicate error)
        conn.execute(sa.text(
            "DO $$ BEGIN "
            "CREATE TYPE learning_type AS ENUM ('tone', 'content', 'structure', 'skip_detection', 'product_knowledge'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "END $$"
        ))

        op.create_table(
            'draft_learnings',
            sa.Column('id', sa.UUID(), primary_key=True),
            sa.Column('draft_id', sa.UUID(), sa.ForeignKey('drafts.id'), nullable=False, index=True),
            sa.Column('learning_type', postgresql.ENUM('tone', 'content', 'structure', 'skip_detection', 'product_knowledge', name='learning_type', create_type=False), nullable=False),
            sa.Column('original_text', sa.Text(), nullable=False),
            sa.Column('corrected_text', sa.Text(), nullable=False),
            sa.Column('diff_summary', sa.Text(), nullable=False),
            sa.Column('stage', sa.String(50), nullable=True),
            sa.Column('confidence', sa.Numeric(3, 2), nullable=False, server_default='0.5'),
            sa.Column('applied_to_prompt', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    # --- Create qa_guidelines table ---
    if _table_exists(conn, 'qa_guidelines'):
        print("=== qa_guidelines table already exists ===", flush=True)
    else:
        print("=== CREATING qa_guidelines table ===", flush=True)
        # Create the enum type via raw SQL (IF NOT EXISTS avoids duplicate error)
        conn.execute(sa.text(
            "DO $$ BEGIN "
            "CREATE TYPE guideline_type AS ENUM ('do', 'dont', 'example', 'tone_rule'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "END $$"
        ))

        op.create_table(
            'qa_guidelines',
            sa.Column('id', sa.UUID(), primary_key=True),
            sa.Column('stage', sa.String(50), nullable=False, index=True),
            sa.Column('guideline_type', postgresql.ENUM('do', 'dont', 'example', 'tone_rule', name='guideline_type', create_type=False), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('source_learning_ids', sa.JSON(), nullable=True),
            sa.Column('occurrences', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true', index=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table('qa_guidelines')
    op.drop_table('draft_learnings')

    # Drop enum types
    sa.Enum(name='guideline_type').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='learning_type').drop(op.get_bind(), checkfirst=True)

    # Drop columns from drafts
    for col in ['original_ai_draft', 'human_edited_draft', 'qa_score',
                'qa_verdict', 'qa_issues', 'qa_model', 'qa_cost_usd']:
        op.drop_column('drafts', col)
