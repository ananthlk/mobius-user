"""Preference audit trail — per-field change history on the update path.

Powers the training-mode "edit-later churn" success metric (ratified by
Ananth 2026-07-15): a preference re-edited after training-mode capture is
the mis-capture signal. `source` distinguishes writers (training_mode /
preferences_modal / api) so training's own writes are never counted as
churn. Append-only; initial onboarding capture is intentionally NOT
audited (initial writes aren't churn).

Revision ID: 008_preference_audit
Revises: 007_hesitations
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "008_preference_audit"
down_revision: Union[str, None] = "007_hesitations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_preference_audit",
        sa.Column(
            "audit_id",
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
        sa.Column("field", sa.String(50), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("source", sa.String(30), nullable=True),
        sa.Column("changed_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index(
        "ix_user_preference_audit_user_changed",
        "user_preference_audit",
        ["user_id", "changed_at"],
    )
    op.create_index("ix_user_preference_audit_field", "user_preference_audit", ["field"])


def downgrade() -> None:
    op.drop_index("ix_user_preference_audit_field", table_name="user_preference_audit")
    op.drop_index("ix_user_preference_audit_user_changed", table_name="user_preference_audit")
    op.drop_table("user_preference_audit")
