"""SQLAlchemy 2.x models for Tianling."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tianling.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class GuildScopedMixin:
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class User(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (UniqueConstraint("guild_id", "member_id", name="uq_users_guild_member"),)


class GameProfile(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "game_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    ingame_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True)

    current_rank_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_division: Mapped[str | None] = mapped_column(String(16), nullable=True)
    current_queue_type: Mapped[str] = mapped_column(String(32), default="default", nullable=False)
    current_is_unranked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_is_leaderboard_status: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    current_rank_order_value: Mapped[int | None] = mapped_column(Integer, nullable=True)

    peak_rank_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    peak_division: Mapped[str | None] = mapped_column(String(16), nullable=True)
    peak_queue_type: Mapped[str] = mapped_column(String(32), default="default", nullable=False)
    peak_is_unranked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    peak_is_leaderboard_status: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    peak_rank_order_value: Mapped[int | None] = mapped_column(Integer, nullable=True)

    preferred_roles: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    acceptable_roles: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    preferred_heroes: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    playstyle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    activity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rank_source: Mapped[str] = mapped_column(String(16), default="self_reported", nullable=False)
    needs_update: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_update_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", "game", name="uq_game_profiles_guild_user_game"),
    )


class RankDefinition(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "rank_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    queue_type: Mapped[str] = mapped_column(String(32), default="default", nullable=False)
    rank_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    division: Mapped[str | None] = mapped_column(String(16), nullable=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    rank_order_value: Mapped[int] = mapped_column(Integer, nullable=False)
    is_unranked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_leaderboard_status: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_division: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "guild_id",
            "game",
            "queue_type",
            "rank_tier",
            "division",
            name="uq_rank_defs_guild_game_qt_tier_div",
        ),
    )


class Composition(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "compositions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    activity: Mapped[str] = mapped_column(String(32), nullable=False)
    group_size: Mapped[int] = mapped_column(Integer, nullable=False)
    roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    role_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allowed_rank_gap: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    party_max_size: Mapped[int] = mapped_column(Integer, nullable=False)
    max_party_rank_gap: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    partial_fill_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verify_in_game: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "guild_id", "game", "activity", name="uq_compositions_guild_game_activity"
        ),
    )


class GamePreference(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "game_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", "game", name="uq_game_prefs_guild_user_game"),
    )


class NotificationSetting(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    opt_in: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allowed_pings: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    rate_limit_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PartyMember(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "party_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    declared_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    accepted_roles: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    rank_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class QueueEntry(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "queue_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    activity: Mapped[str] = mapped_column(String(32), nullable=False)
    queue_type: Mapped[str] = mapped_column(String(32), default="default", nullable=False)
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)
    role_preference: Mapped[str | None] = mapped_column(String(32), nullable=True)
    preferred_roles: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    acceptable_roles: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    rank_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    party_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    party_size: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    leader_member_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    open_slots: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False, index=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    relaxation_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    either_group: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sibling_entry_ids: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "ix_queue_entries_active_unique",
            "guild_id",
            "member_id",
            "game",
            "activity",
            unique=True,
            sqlite_where=text("status IN ('queued','reserved','proposed')"),
            postgresql_where=text("status IN ('queued','reserved','proposed')"),
        ),
    )


class MatchGroup(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "match_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    activity: Mapped[str] = mapped_column(String(32), nullable=False)
    queue_type: Mapped[str] = mapped_column(String(32), default="default", nullable=False)
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)
    party_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)
    proposal_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    confirmation_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    roster: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    voice_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class MatchMember(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "match_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_group_id: Mapped[int] = mapped_column(
        ForeignKey("match_groups.id", ondelete="CASCADE"), nullable=False
    )
    party_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    role_assigned: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response: Mapped[str | None] = mapped_column(String(16), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("match_group_id", "member_id", name="uq_match_members_group_member"),
    )


class TemporaryChannel(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "temporary_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    match_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_groups.id", ondelete="SET NULL"), nullable=True
    )
    empty_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)


class GuildConfig(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "guild_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (UniqueConstraint("guild_id", "key", name="uq_guild_config_guild_key"),)


class AuditLog(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_member_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class BlockedUser(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "blocked_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blocker_member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blocked_member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "guild_id", "blocker_member_id", "blocked_member_id", name="uq_blocked_users"
        ),
    )


class RegistryMessage(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "registry_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("game_profiles.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("guild_id", "profile_id", name="uq_registry_messages_profile"),
    )


class ScheduledJob(Base, TimestampMixin):
    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)


class MemberPenalty(Base, TimestampMixin, GuildScopedMixin):
    __tablename__ = "member_penalties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("guild_id", "member_id", name="uq_member_penalties_guild_member"),
    )
