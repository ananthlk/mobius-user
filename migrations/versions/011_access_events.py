"""Usage ledger — per-surface access events for the Users & Usage console.

Append-only. Each gated surface fire-and-forgets an access beacon after it
validates the user's token, so the admin console can show who used what.
PHI-in-logs standard: categories/counts only — `action` is a coarse enum,
NEVER raw query/message/document text. Additive, single-owner (mobius_user)
DDL; announced to the migration peer (elastic-blackburn) + DB-seat.

Revision ID: 011_access_events
Revises: 010_membership_removal
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "011_access_events"
down_revision: Union[str, None] = "010_membership_removal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_access_event",
        sa.Column(
            "access_event_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # nullable: pre-auth landing hits (funnel) can beacon anonymously.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("surface", sa.String(40), nullable=False),
        sa.Column("org_slug", sa.String(255), nullable=True),
        sa.Column("action", sa.String(40), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_access_event_user", "user_access_event", ["user_id", "occurred_at"])
    op.create_index("ix_access_event_surface", "user_access_event", ["surface", "occurred_at"])
    op.create_index("ix_access_event_org", "user_access_event", ["org_slug", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_access_event_org", table_name="user_access_event")
    op.drop_index("ix_access_event_surface", table_name="user_access_event")
    op.drop_index("ix_access_event_user", table_name="user_access_event")
    op.drop_table("user_access_event")
