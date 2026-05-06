"""Add user profile JSON to user_preference.

Three columns added so consumers (chat, rag, etc.) can pull a structured
user profile from /me — instead of each one re-deriving it from raw prefs.

Revision ID: 002_user_profile
Revises: 001_initial
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "002_user_profile"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_preference",
        sa.Column("profile_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "user_preference",
        sa.Column("profile_version", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "user_preference",
        sa.Column("profile_generated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_preference", "profile_generated_at")
    op.drop_column("user_preference", "profile_version")
    op.drop_column("user_preference", "profile_json")
