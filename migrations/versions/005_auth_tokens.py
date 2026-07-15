"""Auth tokens: single-use invite / password-reset tokens.

Backs the org-agent employee-onboarding contract (2026-07-15):

- ``auth_token`` — single-use, expiring, hashed tokens. The raw token
  exists only inside the emailed link; we store sha256(token) so a DB
  leak cannot mint working links. purpose ∈ {invite, reset}.

Revision ID: 005_auth_tokens
Revises: 004_membership_org_slug
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "005_auth_tokens"
down_revision: Union[str, None] = "004_membership_org_slug"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_token",
        sa.Column(
            "token_id",
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
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
    )
    op.create_index(
        "ux_auth_token_hash", "auth_token", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_auth_token_user_purpose", "auth_token", ["user_id", "purpose"]
    )


def downgrade() -> None:
    op.drop_index("ix_auth_token_user_purpose", table_name="auth_token")
    op.drop_index("ux_auth_token_hash", table_name="auth_token")
    op.drop_table("auth_token")
