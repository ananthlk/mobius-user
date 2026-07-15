"""Membership approval: self-claim → pending → approve.

Green-lit by Ananth 2026-07-15 (relayed via PA agent). Self-claimed org
memberships (preferences Organization field) now land as status='pending'
and activate on approval; admin/invite-granted memberships stay immediate.
Existing rows are grandfathered active (they were admin- or invite-created,
or pre-date the flow with Ananth's knowledge).

Consumers: instant-RAG org-tier retrieval filter (named consumer), welcome
block org_status="pending", org-agent approval surface.

Revision ID: 006_membership_approval
Revises: 005_auth_tokens
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006_membership_approval"
down_revision: Union[str, None] = "005_auth_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_org_membership",
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    )
    op.add_column(
        "user_org_membership",
        sa.Column("approved_by", sa.String(255), nullable=True),
    )
    op.add_column(
        "user_org_membership",
        sa.Column("approved_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_user_org_membership_org_status",
        "user_org_membership",
        ["org_slug", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_org_membership_org_status", table_name="user_org_membership")
    op.drop_column("user_org_membership", "approved_at")
    op.drop_column("user_org_membership", "approved_by")
    op.drop_column("user_org_membership", "status")
