"""Soft org-scoped removal — Team & Access "Remove" primitive.

Ananth's rule: removing a user DEACTIVATES, never deletes. Two orthogonal
layers, both soft + stamped:

- ORG-SCOPED (this migration): membership status gains 'removed'
  (active|pending|removed) with removed_by/removed_at stamps — the user
  drops out of that org's directory/mentions/sign-in context but the row
  and its roles survive for restore. A removed membership also blocks
  self-reclaim via preferences (the row exists, so the self-claim path
  won't recreate it) — reinstatement is an admin action.
- ACCOUNT-LEVEL (no schema change): app_user.status='disabled' +
  session revoke + capability stamp-revoke, via new routes.

Revision ID: 010_membership_removal
Revises: 009_user_capabilities
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "010_membership_removal"
down_revision: Union[str, None] = "009_user_capabilities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_org_membership",
        sa.Column("removed_by", sa.String(255), nullable=True),
    )
    op.add_column(
        "user_org_membership",
        sa.Column("removed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_org_membership", "removed_at")
    op.drop_column("user_org_membership", "removed_by")
