"""Create durable workflow, provenance, consent, outreach, and KPI tables.

Revision ID: 0002_persistence
Revises: 0001_foundation
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op

from app.db.base import Base
import app.models  # noqa: F401 - registers mapped metadata


revision: str = "0002_persistence"
down_revision: Union[str, None] = "0001_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # DDL is emitted from the reviewed SQLAlchemy metadata so constraints,
    # foreign keys, indexes, and types stay aligned with the application models.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
