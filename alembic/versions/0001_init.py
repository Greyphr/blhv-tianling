"""init

Revision ID: 0001_init
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("guild_id", "member_id", name="uq_users_guild_member"),
    )
    op.create_index(op.f("ix_users_guild_id"), "users", ["guild_id"], unique=False)

    op.create_table(
        "game_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("game", sa.String(length=32), nullable=False),
        sa.Column("ingame_name", sa.String(length=128), nullable=True),
        sa.Column("region", sa.String(length=16), nullable=True),
        sa.Column("platform", sa.String(length=16), nullable=True),
        sa.Column("current_rank_tier", sa.String(length=32), nullable=True),
        sa.Column("current_division", sa.String(length=16), nullable=True),
        sa.Column("current_queue_type", sa.String(length=32), nullable=False),
        sa.Column("current_is_unranked", sa.Boolean(), nullable=False),
        sa.Column("current_is_leaderboard_status", sa.Boolean(), nullable=False),
        sa.Column("current_rank_order_value", sa.Integer(), nullable=True),
        sa.Column("peak_rank_tier", sa.String(length=32), nullable=True),
        sa.Column("peak_division", sa.String(length=16), nullable=True),
        sa.Column("peak_queue_type", sa.String(length=32), nullable=False),
        sa.Column("peak_is_unranked", sa.Boolean(), nullable=False),
        sa.Column("peak_is_leaderboard_status", sa.Boolean(), nullable=False),
        sa.Column("peak_rank_order_value", sa.Integer(), nullable=True),
        sa.Column("preferred_roles", sa.JSON(), nullable=True),
        sa.Column("acceptable_roles", sa.JSON(), nullable=True),
        sa.Column("preferred_heroes", sa.JSON(), nullable=True),
        sa.Column("playstyle", sa.String(length=64), nullable=True),
        sa.Column("activity", sa.String(length=32), nullable=True),
        sa.Column("rank_source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_game_profiles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_profiles")),
        sa.UniqueConstraint("guild_id", "user_id", "game", name="uq_game_profiles_guild_user_game"),
    )
    op.create_index(op.f("ix_game_profiles_guild_id"), "game_profiles", ["guild_id"], unique=False)

    op.create_table(
        "rank_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("game", sa.String(length=32), nullable=False),
        sa.Column("queue_type", sa.String(length=32), nullable=False),
        sa.Column("rank_tier", sa.String(length=32), nullable=False),
        sa.Column("division", sa.String(length=16), nullable=True),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("rank_order_value", sa.Integer(), nullable=False),
        sa.Column("is_unranked", sa.Boolean(), nullable=False),
        sa.Column("is_leaderboard_status", sa.Boolean(), nullable=False),
        sa.Column("has_division", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rank_definitions")),
        sa.UniqueConstraint(
            "guild_id",
            "game",
            "queue_type",
            "rank_tier",
            "division",
            name="uq_rank_defs_guild_game_qt_tier_div",
        ),
    )
    op.create_index(
        op.f("ix_rank_definitions_guild_id"), "rank_definitions", ["guild_id"], unique=False
    )

    op.create_table(
        "compositions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("game", sa.String(length=32), nullable=False),
        sa.Column("activity", sa.String(length=32), nullable=False),
        sa.Column("group_size", sa.Integer(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=True),
        sa.Column("role_required", sa.Boolean(), nullable=False),
        sa.Column("allowed_rank_gap", sa.Integer(), nullable=False),
        sa.Column("party_max_size", sa.Integer(), nullable=False),
        sa.Column("max_party_rank_gap", sa.Integer(), nullable=False),
        sa.Column("partial_fill_allowed", sa.Boolean(), nullable=False),
        sa.Column("verify_in_game", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compositions")),
        sa.UniqueConstraint(
            "guild_id", "game", "activity", name="uq_compositions_guild_game_activity"
        ),
    )
    op.create_index(op.f("ix_compositions_guild_id"), "compositions", ["guild_id"], unique=False)

    op.create_table(
        "game_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("game", sa.String(length=32), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_game_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_preferences")),
        sa.UniqueConstraint("guild_id", "user_id", "game", name="uq_game_prefs_guild_user_game"),
    )
    op.create_index(
        op.f("ix_game_preferences_guild_id"), "game_preferences", ["guild_id"], unique=False
    )

    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("opt_in", sa.Boolean(), nullable=False),
        sa.Column("allowed_pings", sa.JSON(), nullable=True),
        sa.Column("rate_limit_data", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_settings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_settings")),
        sa.UniqueConstraint("user_id", name=op.f("uq_notification_settings_user_id")),
    )
    op.create_index(
        op.f("ix_notification_settings_guild_id"),
        "notification_settings",
        ["guild_id"],
        unique=False,
    )

    op.create_table(
        "party_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("party_id", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=True),
        sa.Column("declared_role", sa.String(length=32), nullable=True),
        sa.Column("accepted_roles", sa.JSON(), nullable=True),
        sa.Column("rank_snapshot", sa.JSON(), nullable=True),
        sa.Column("is_guest", sa.Boolean(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_party_members")),
    )
    op.create_index(op.f("ix_party_members_guild_id"), "party_members", ["guild_id"], unique=False)
    op.create_index(op.f("ix_party_members_party_id"), "party_members", ["party_id"], unique=False)

    op.create_table(
        "queue_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("game", sa.String(length=32), nullable=False),
        sa.Column("activity", sa.String(length=32), nullable=False),
        sa.Column("queue_type", sa.String(length=32), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=True),
        sa.Column("role_preference", sa.String(length=32), nullable=True),
        sa.Column("preferred_roles", sa.JSON(), nullable=True),
        sa.Column("acceptable_roles", sa.JSON(), nullable=True),
        sa.Column("rank_snapshot", sa.JSON(), nullable=True),
        sa.Column("party_id", sa.String(length=64), nullable=True),
        sa.Column("party_size", sa.Integer(), nullable=False),
        sa.Column("leader_member_id", sa.BigInteger(), nullable=True),
        sa.Column("open_slots", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("relaxation_level", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_queue_entries_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_queue_entries")),
    )
    op.create_index(op.f("ix_queue_entries_guild_id"), "queue_entries", ["guild_id"], unique=False)
    op.create_index(op.f("ix_queue_entries_party_id"), "queue_entries", ["party_id"], unique=False)
    op.create_index(op.f("ix_queue_entries_status"), "queue_entries", ["status"], unique=False)
    op.execute(
        "CREATE UNIQUE INDEX ix_queue_entries_active_unique ON queue_entries (guild_id, member_id, game, activity) WHERE status IN ('queued','reserved','proposed')"
    )

    op.create_table(
        "match_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("group_uuid", sa.String(length=64), nullable=False),
        sa.Column("game", sa.String(length=32), nullable=False),
        sa.Column("activity", sa.String(length=32), nullable=False),
        sa.Column("queue_type", sa.String(length=32), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=True),
        sa.Column("party_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("proposal_status", sa.String(length=16), nullable=False),
        sa.Column("confirmation_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("roster", sa.JSON(), nullable=True),
        sa.Column("voice_channel_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_groups")),
        sa.UniqueConstraint("group_uuid", name=op.f("uq_match_groups_group_uuid")),
    )
    op.create_index(op.f("ix_match_groups_guild_id"), "match_groups", ["guild_id"], unique=False)

    op.create_table(
        "match_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("match_group_id", sa.Integer(), nullable=False),
        sa.Column("party_id", sa.String(length=64), nullable=True),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("role_assigned", sa.String(length=32), nullable=True),
        sa.Column("response", sa.String(length=16), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["match_group_id"],
            ["match_groups.id"],
            name=op.f("fk_match_members_match_group_id_match_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_match_members_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_members")),
        sa.UniqueConstraint("match_group_id", "member_id", name="uq_match_members_group_member"),
    )
    op.create_index(op.f("ix_match_members_guild_id"), "match_members", ["guild_id"], unique=False)

    op.create_table(
        "temporary_channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("group_id", sa.String(length=64), nullable=True),
        sa.Column("match_group_id", sa.Integer(), nullable=True),
        sa.Column("empty_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["match_group_id"],
            ["match_groups.id"],
            name=op.f("fk_temporary_channels_match_group_id_match_groups"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_temporary_channels")),
        sa.UniqueConstraint("channel_id", name=op.f("uq_temporary_channels_channel_id")),
    )
    op.create_index(
        op.f("ix_temporary_channels_guild_id"), "temporary_channels", ["guild_id"], unique=False
    )

    op.create_table(
        "guild_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guild_config")),
        sa.UniqueConstraint("guild_id", "key", name="uq_guild_config_guild_key"),
    )
    op.create_index(op.f("ix_guild_config_guild_id"), "guild_config", ["guild_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_member_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_guild_id"), "audit_logs", ["guild_id"], unique=False)

    op.create_table(
        "blocked_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("blocker_member_id", sa.BigInteger(), nullable=False),
        sa.Column("blocked_member_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blocked_users")),
        sa.UniqueConstraint(
            "guild_id", "blocker_member_id", "blocked_member_id", name="uq_blocked_users"
        ),
    )
    op.create_index(op.f("ix_blocked_users_guild_id"), "blocked_users", ["guild_id"], unique=False)

    op.create_table(
        "registry_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["game_profiles.id"],
            name=op.f("fk_registry_messages_profile_id_game_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registry_messages")),
        sa.UniqueConstraint("guild_id", "profile_id", name="uq_registry_messages_profile"),
    )
    op.create_index(
        op.f("ix_registry_messages_guild_id"), "registry_messages", ["guild_id"], unique=False
    )

    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_jobs")),
    )
    op.create_index(
        op.f("ix_scheduled_jobs_guild_id"), "scheduled_jobs", ["guild_id"], unique=False
    )
    op.create_index(op.f("ix_scheduled_jobs_run_at"), "scheduled_jobs", ["run_at"], unique=False)
    op.create_index(op.f("ix_scheduled_jobs_status"), "scheduled_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_scheduled_jobs_status"), table_name="scheduled_jobs")
    op.drop_index(op.f("ix_scheduled_jobs_run_at"), table_name="scheduled_jobs")
    op.drop_index(op.f("ix_scheduled_jobs_guild_id"), table_name="scheduled_jobs")
    op.drop_table("scheduled_jobs")
    op.drop_index(op.f("ix_registry_messages_guild_id"), table_name="registry_messages")
    op.drop_table("registry_messages")
    op.drop_index(op.f("ix_blocked_users_guild_id"), table_name="blocked_users")
    op.drop_table("blocked_users")
    op.drop_index(op.f("ix_audit_logs_guild_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_guild_config_guild_id"), table_name="guild_config")
    op.drop_table("guild_config")
    op.drop_index(op.f("ix_temporary_channels_guild_id"), table_name="temporary_channels")
    op.drop_table("temporary_channels")
    op.drop_index(op.f("ix_match_members_guild_id"), table_name="match_members")
    op.drop_table("match_members")
    op.drop_index(op.f("ix_match_groups_guild_id"), table_name="match_groups")
    op.drop_table("match_groups")
    op.execute("DROP INDEX IF EXISTS ix_queue_entries_active_unique")
    op.drop_index(op.f("ix_queue_entries_status"), table_name="queue_entries")
    op.drop_index(op.f("ix_queue_entries_party_id"), table_name="queue_entries")
    op.drop_index(op.f("ix_queue_entries_guild_id"), table_name="queue_entries")
    op.drop_table("queue_entries")
    op.drop_index(op.f("ix_party_members_party_id"), table_name="party_members")
    op.drop_index(op.f("ix_party_members_guild_id"), table_name="party_members")
    op.drop_table("party_members")
    op.drop_index(op.f("ix_notification_settings_guild_id"), table_name="notification_settings")
    op.drop_table("notification_settings")
    op.drop_index(op.f("ix_compositions_guild_id"), table_name="compositions")
    op.drop_table("compositions")
    op.drop_index(op.f("ix_game_preferences_guild_id"), table_name="game_preferences")
    op.drop_table("game_preferences")
    op.drop_index(op.f("ix_rank_definitions_guild_id"), table_name="rank_definitions")
    op.drop_table("rank_definitions")
    op.drop_index(op.f("ix_game_profiles_guild_id"), table_name="game_profiles")
    op.drop_table("game_profiles")
    op.drop_index(op.f("ix_users_guild_id"), table_name="users")
    op.drop_table("users")
