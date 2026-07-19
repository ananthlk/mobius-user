"""User capabilities — revocable, auditable authority grants.

First consumer: the PHI false-positive override
(docs/phi_false_positive_override_spec.md) — `phi_override` gates the
"attest this is not patient PHI" affordance in Providr and its server-side
re-validation in RAG. Ananth's direction: a real, revocable capability on
the profile, NOT a hardcoded role.

Design: append-only history — grants insert, revocations stamp
revoked_by/revoked_at, rows are never deleted, so who-granted-what-when
(both directions) is queryable without a side audit table. Active
capability = revoked_at IS NULL. org_slug NULL = global grant; a non-NULL
org_slug scopes the authority to one org (future-proofing — v1 grants are
global). Capability vocabulary is an open set, same philosophy as roles.

Revision ID: 009_user_capabilities
Revises: 008_preference_audit
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "009_user_capabilities"
down_revision: Union[str, None] = "008_preference_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_capability",
        sa.Column(
            "capability_id",
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
        sa.Column("capability", sa.String(50), nullable=False),
        sa.Column("org_slug", sa.String(255), nullable=True),
        sa.Column("granted_by", sa.String(255), nullable=True),
        sa.Column("granted_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("revoked_by", sa.String(255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_user_capability_user", "user_capability", ["user_id"])
    # COALESCE in the unique expression: org_slug NULL = global grant, and
    # plain unique indexes treat NULLs as distinct — without this, duplicate
    # active GLOBAL grants would pass (elastic-blackburn's catch on the 009
    # announcement). COALESCE works on any PG version, unlike NULLS NOT
    # DISTINCT (PG15+).
    op.create_index(
        "ux_user_capability_active",
        "user_capability",
        ["user_id", "capability", sa.text("COALESCE(org_slug, '')")],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_user_capability_active", table_name="user_capability")
    op.drop_index("ix_user_capability_user", table_name="user_capability")
    op.drop_table("user_capability")
