"""member penalties

Revision ID: 0003_member_penalties
Revises: 0002_profile_needs_update
Create Date: 2026-10-23 01:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_member_penalties"
down_revision = "0002_profile_needs_update"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "member_penalties",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member_penalties")),
        sa.UniqueConstraint("guild_id", "member_id", name="uq_member_penalties_guild_member"),
    )
    op.create_index(op.f("ix_member_penalties_guild_id"), "member_penalties", ["guild_id"], unique=False)
    op.create_index(op.f("ix_member_penalties_member_id"), "member_penalties", ["member_id"], unique=False)
    op.create_index(op.f("ix_member_penalties_expires_at"), "member_penalties", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_member_penalties_expires_at"), table_name="member_penalties")
    op.drop_index(op.f("ix_member_penalties_member_id"), table_name="member_penalties")
    op.drop_index(op.f("ix_member_penalties_guild_id"), table_name="member_penalties")
    op.drop_table("member_penalties")
