"""Identity directory: agent principals, aliases, org memberships.

Adds the pieces the task-manager assignee-identity contract needs:

- ``app_user.is_agent`` + ``app_user.canonical_handle`` — agents enroll as
  app_user rows so /users/resolve finds them, while canonical_handle
  preserves their existing ``agent:{name}`` assignee format.
- ``user_alias`` — natural-language handles ("sam", "sammy") for ranked
  resolution in /users/resolve.
- ``user_org_membership`` — org scoping for task queues. org_name is the
  task/platform org display string (free text today), deliberately NOT
  tenant_id; the two vocabularies are unrelated until an org registry
  exists.

Revision ID: 003_identity_directory
Revises: 002_user_profile
Create Date: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "003_identity_directory"
down_revision: Union[str, None] = "002_user_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("is_agent", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "app_user",
        sa.Column("canonical_handle", sa.String(255), nullable=True),
    )
    op.create_index(
        "ux_app_user_canonical_handle",
        "app_user",
        ["canonical_handle"],
        unique=True,
        postgresql_where=sa.text("canonical_handle IS NOT NULL"),
    )

    op.create_table(
        "user_alias",
        sa.Column(
            "alias_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("user_id", "alias", name="uq_user_alias_user_alias"),
    )
    op.create_index("ix_user_alias_user_id", "user_alias", ["user_id"])
    op.create_index(
        "ix_user_alias_alias_lower",
        "user_alias",
        [sa.text("lower(alias)")],
    )

    op.create_table(
        "user_org_membership",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("org_name", sa.String(255), primary_key=True),
        sa.Column(
            "roles",
            postgresql.ARRAY(sa.String(100)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_user_org_membership_org_name", "user_org_membership", ["org_name"])


def downgrade() -> None:
    op.drop_index("ix_user_org_membership_org_name", table_name="user_org_membership")
    op.drop_table("user_org_membership")
    op.drop_index("ix_user_alias_alias_lower", table_name="user_alias")
    op.drop_index("ix_user_alias_user_id", table_name="user_alias")
    op.drop_table("user_alias")
    op.drop_index("ux_app_user_canonical_handle", table_name="app_user")
    op.drop_column("app_user", "canonical_handle")
    op.drop_column("app_user", "is_agent")
