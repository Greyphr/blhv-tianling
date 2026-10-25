"""Admin operations: validated settings changes and status summaries (no Discord)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from tianling.db.models import MatchGroup, QueueEntry, TemporaryChannel
from tianling.db.repositories.config import GuildConfigRepository
from tianling.services.settings import MatchSettings, load_match_settings, load_voice_settings


class SettingError(ValueError):
    pass


def _int(minimum: int, maximum: int):
    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError as exc:
            raise SettingError(f"'{raw}' is not a whole number") from exc
        if not minimum <= value <= maximum:
            raise SettingError(f"must be between {minimum} and {maximum}")
        return value

    return parse


def _bool(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in {"true", "on", "yes", "1"}:
        return True
    if lowered in {"false", "off", "no", "0"}:
        return False
    raise SettingError("use on/off")


def _choice(*options: str):
    def parse(raw: str) -> str:
        if raw not in options:
            raise SettingError(f"choose one of: {', '.join(options)}")
        return raw

    return parse


def _snowflake(raw: str) -> int:
    return _int(1, 2**63 - 1)(raw)


#: key -> (config section, field, parser, description)
SETTINGS: dict[str, tuple[str, str, object, str]] = {
    "confirmation_timeout_seconds": (
        "matching",
        "confirmation_timeout_seconds",
        _int(15, 3600),
        "Time to accept a proposal",
    ),
    "cooldown_seconds": (
        "matching",
        "cooldown_seconds",
        _int(0, 86400),
        "Penalty after missing a confirmation",
    ),
    "queue_expiry_minutes": (
        "matching",
        "queue_expiry_minutes",
        _int(1, 1440),
        "How long a queue request lasts",
    ),
    "auto_confirm": ("matching", "auto_confirm", _bool, "Skip confirmations and finalize at once"),
    "allow_partial_fill": (
        "matching",
        "allow_partial_fill",
        _bool,
        "Allow partially filled proposals",
    ),
    "replacement_policy": (
        "matching",
        "replacement_policy",
        _choice("requeue", "cancel"),
        "When no replacement is found",
    ),
    "voice_category": ("voice", "category_id", _snowflake, "Category for temporary voice channels"),
    "voice_grace_seconds": ("voice", "grace_seconds", _int(10, 3600), "Empty-channel grace period"),
    "voice_fallback_channel": (
        "voice",
        "fallback_channel_id",
        _snowflake,
        "Shared channel if creation fails",
    ),
    "moderator_role": ("voice", "mod_role_ids", _snowflake, "Role with access to temp channels"),
}


async def set_setting(session, guild_id: int, key: str, raw: str):
    if key not in SETTINGS:
        raise SettingError(f"unknown setting '{key}'. Known: {', '.join(SETTINGS)}")
    section, field_name, parser, _ = SETTINGS[key]
    value = parser(raw)  # type: ignore[operator]
    repo = GuildConfigRepository(session)
    if key == "moderator_role":
        current = (await repo.get_value(guild_id, "voice", {}) or {}).get("mod_role_ids", [])
        value = sorted({*current, value})
    await repo.update_value(guild_id, section, **{field_name: value})
    return value


async def current_settings(session, guild_id: int) -> dict[str, object]:
    matching: MatchSettings = await load_match_settings(session, guild_id)
    voice = await load_voice_settings(session, guild_id)
    out: dict[str, object] = {}
    for key, (section, field_name, _, _) in SETTINGS.items():
        source = matching if section == "matching" else voice
        out[key] = getattr(source, field_name)
    return out


@dataclass
class StatusSummary:
    queued: dict[str, int]
    reserved: int
    proposals: int
    finalized_recent: int
    temp_channels: int


async def status_summary(session, guild_id: int) -> StatusSummary:
    rows = await session.execute(
        select(QueueEntry.game, QueueEntry.activity, QueueEntry.status, func.count())
        .where(QueueEntry.guild_id == guild_id, QueueEntry.status.in_(("queued", "reserved")))
        .group_by(QueueEntry.game, QueueEntry.activity, QueueEntry.status)
    )
    queued: dict[str, int] = {}
    reserved = 0
    for game, activity, status, count in rows.all():
        if status == "queued":
            queued[f"{game}/{activity}"] = int(count)
        else:
            reserved += int(count)

    async def count(model, *conditions) -> int:
        return int(
            await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
        )

    return StatusSummary(
        queued=queued,
        reserved=reserved,
        proposals=await count(
            MatchGroup, MatchGroup.guild_id == guild_id, MatchGroup.status == "proposed"
        ),
        finalized_recent=await count(
            MatchGroup, MatchGroup.guild_id == guild_id, MatchGroup.status == "finalized"
        ),
        temp_channels=await count(TemporaryChannel, TemporaryChannel.guild_id == guild_id),
    )
