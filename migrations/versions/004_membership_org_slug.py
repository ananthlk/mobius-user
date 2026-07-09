"""Key user_org_membership to canonical org_slug from the master org registry.

Ownership decision (Ananth, 2026-07-08): the provider-roster-credentialing
service owns the master org registry; mobius-user consumes its canonical
slugs. Platform flow: org set up first in the master, then users, then
enrollment.

- ``org_name`` (free-text display string) becomes ``org_slug`` (canonical
  key, master's slug grammar). No cross-DB FK — validated against the
  master's API at write time.
- ``org_display_name`` is denormalized at write time so profile reads never
  need a live call to the master.
- Existing rows are mapped by lowercase-hyphenation, matching the master's
  current derivation ("David Lawrence Center" → "david-lawrence-center",
  the slug the Roster & Credentialing Agent confirmed canonical).

Revision ID: 004_membership_org_slug
Revises: 003_identity_directory
Create Date: 2026-07-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004_membership_org_slug"
down_revision: Union[str, None] = "003_identity_directory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_org_membership",
        sa.Column("org_display_name", sa.String(255), nullable=True),
    )
    # Preserve the free-text value as the display name, then canonicalize
    # the key column in place (lowercase, spaces/underscores → hyphens).
    op.execute("UPDATE user_org_membership SET org_display_name = org_name")
    op.execute(
        """
        UPDATE user_org_membership
        SET org_name = regexp_replace(
            regexp_replace(lower(trim(org_name)), '[^a-z0-9]+', '-', 'g'),
            '(^-+|-+$)', '', 'g'
        )
        """
    )
    op.alter_column("user_org_membership", "org_name", new_column_name="org_slug")
    op.drop_index("ix_user_org_membership_org_name", table_name="user_org_membership")
    op.create_index("ix_user_org_membership_org_slug", "user_org_membership", ["org_slug"])


def downgrade() -> None:
    op.drop_index("ix_user_org_membership_org_slug", table_name="user_org_membership")
    op.alter_column("user_org_membership", "org_slug", new_column_name="org_name")
    op.create_index("ix_user_org_membership_org_name", "user_org_membership", ["org_name"])
    op.execute("UPDATE user_org_membership SET org_name = COALESCE(org_display_name, org_name)")
    op.drop_column("user_org_membership", "org_display_name")
