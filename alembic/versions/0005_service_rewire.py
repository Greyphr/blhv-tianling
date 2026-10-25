"""queue/confirmation rewire: proposal ids, message ids, guests, failed rosters

Revision ID: 0005_service_rewire
Revises: 0004_proposal_fields
Create Date: 2026-10-24 08:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_service_rewire"
down_revision = "0004_proposal_fields"
branch_labels = None
depends_on = None


def _bool_col(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())


def upgrade() -> None:
    with op.batch_alter_table("party_members") as batch:
        batch.add_column(_bool_col("is_test"))
        batch.add_column(sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("game", sa.String(length=32), nullable=True))

    with op.batch_alter_table("queue_entries") as batch:
        batch.add_column(_bool_col("is_test"))
        batch.add_column(_bool_col("still_looking_sent"))

    with op.batch_alter_table("notification_settings") as batch:
        batch.add_column(_bool_col("show_availability"))

    with op.batch_alter_table("match_groups") as batch:
        batch.add_column(sa.Column("proposal_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("roster_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("channel_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("message_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cancel_reason", sa.String(length=64), nullable=True))
        batch.create_index(batch.f("ix_match_groups_proposal_id"), ["proposal_id"], unique=False)

    with op.batch_alter_table("match_members") as batch:
        batch.add_column(sa.Column("entry_id", sa.Integer(), nullable=True))
        batch.add_column(_bool_col("is_guest"))
        batch.add_column(_bool_col("is_test"))
        batch.add_column(sa.Column("rank_snapshot", sa.JSON(), nullable=True))
        batch.create_index(batch.f("ix_match_members_entry_id"), ["entry_id"], unique=False)

    op.create_table(
        "failed_rosters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("game", sa.String(length=32), nullable=False),
        sa.Column("activity", sa.String(length=32), nullable=False),
        sa.Column("roster_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_failed_rosters_guild_id"), "failed_rosters", ["guild_id"])
    op.create_index(op.f("ix_failed_rosters_expires_at"), "failed_rosters", ["expires_at"])
    op.create_index(
        "ix_failed_rosters_lookup",
        "failed_rosters",
        ["guild_id", "game", "activity", "roster_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_failed_rosters_lookup", table_name="failed_rosters")
    op.drop_index(op.f("ix_failed_rosters_expires_at"), table_name="failed_rosters")
    op.drop_index(op.f("ix_failed_rosters_guild_id"), table_name="failed_rosters")
    op.drop_table("failed_rosters")

    with op.batch_alter_table("match_members") as batch:
        batch.drop_index(batch.f("ix_match_members_entry_id"))
        batch.drop_column("rank_snapshot")
        batch.drop_column("is_test")
        batch.drop_column("is_guest")
        batch.drop_column("entry_id")

    with op.batch_alter_table("match_groups") as batch:
        batch.drop_index(batch.f("ix_match_groups_proposal_id"))
        batch.drop_column("cancel_reason")
        batch.drop_column("finalized_at")
        batch.drop_column("message_id")
        batch.drop_column("channel_id")
        batch.drop_column("roster_hash")
        batch.drop_column("proposal_id")

    with op.batch_alter_table("notification_settings") as batch:
        batch.drop_column("show_availability")

    with op.batch_alter_table("queue_entries") as batch:
        batch.drop_column("still_looking_sent")
        batch.drop_column("is_test")

    with op.batch_alter_table("party_members") as batch:
        batch.drop_column("game")
        batch.drop_column("invite_expires_at")
        batch.drop_column("is_test")
