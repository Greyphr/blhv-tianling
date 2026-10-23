"""profile needs update

Revision ID: 0002_profile_needs_update
Revises: 0001_init
Create Date: 2026-10-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_profile_needs_update"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "game_profiles",
        sa.Column("needs_update", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "game_profiles",
        sa.Column("needs_update_reason", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("game_profiles", "needs_update_reason")
    op.drop_column("game_profiles", "needs_update")
