"""Hesitation chips — direct fear capture from training-mode onboarding.

PA's training-mode welcome (docs/welcome-onboarding-spec.md, step 5,
UX-approved): optional multi-select chips; each pick is a hesitation the
tailored experience should ease. Empty array = step skipped — never
interpret as "no hesitations".

Revision ID: 007_hesitations
Revises: 006_membership_approval
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "007_hesitations"
down_revision: Union[str, None] = "006_membership_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_preference",
        sa.Column(
            "hesitations",
            postgresql.ARRAY(sa.String(50)),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_preference", "hesitations")
