"""Empty foundation revision; domain schema will be introduced later.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
