"""Add user roles and workflow ownership for authenticated access.

Revision ID: 0003_authentication
Revises: 0002_persistence
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_authentication"
down_revision: Union[str, None] = "0002_persistence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    # 0002 originally used metadata.create_all. On a fresh install with the
    # current metadata these objects can already exist, while upgraded databases
    # need the additive DDL below.
    if "user_accounts" not in tables:
        op.create_table(
            "user_accounts",
            sa.Column("user_id", sa.String(length=36), primary_key=True),
            sa.Column("username", sa.String(length=190), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(length=300), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False, server_default="user"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("role IN ('user','admin')", name="ck_user_accounts_role"),
        )
    index_names = {item["name"] for item in sa.inspect(bind).get_indexes("user_accounts")}
    if "ix_user_accounts_username" not in index_names:
        op.create_index("ix_user_accounts_username", "user_accounts", ["username"], unique=True)
    workflow_columns = {item["name"] for item in sa.inspect(bind).get_columns("workflows")}
    if "owner_id" not in workflow_columns:
        op.add_column("workflows", sa.Column("owner_id", sa.String(length=36), nullable=True))
        op.create_foreign_key("fk_workflows_owner_id_user_accounts", "workflows", "user_accounts",
                              ["owner_id"], ["user_id"], ondelete="SET NULL")
    workflow_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("workflows")}
    if "ix_workflows_owner_id" not in workflow_indexes:
        op.create_index("ix_workflows_owner_id", "workflows", ["owner_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "workflows" in inspector.get_table_names():
        if "ix_workflows_owner_id" in {item["name"] for item in inspector.get_indexes("workflows")}:
            op.drop_index("ix_workflows_owner_id", table_name="workflows")
        foreign_keys = {item["name"] for item in inspector.get_foreign_keys("workflows")}
        if "fk_workflows_owner_id_user_accounts" in foreign_keys:
            op.drop_constraint("fk_workflows_owner_id_user_accounts", "workflows", type_="foreignkey")
        if "owner_id" in {item["name"] for item in inspector.get_columns("workflows")}:
            op.drop_column("workflows", "owner_id")
    if "user_accounts" in inspector.get_table_names():
        if "ix_user_accounts_username" in {item["name"] for item in inspector.get_indexes("user_accounts")}:
            op.drop_index("ix_user_accounts_username", table_name="user_accounts")
        op.drop_table("user_accounts")
