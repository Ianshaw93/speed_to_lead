"""Add buying_signal to prospect_source enum.

Revision ID: 016
Revises: 015
Create Date: 2026-02-15

Adds 'buying_signal' value to the prospect_source PostgreSQL enum type
for prospects received from the Gojiberry buying signal agent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE prospect_source ADD VALUE IF NOT EXISTS 'buying_signal'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly.
    # To fully reverse, you'd need to recreate the type without this value.
    pass
