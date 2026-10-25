"""Matchmaking settings, loaded per guild from ``guild_config`` (key ``matching``)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from tianling.db.repositories.config import GuildConfigRepository


@dataclass
class MatchSettings:
    """Tunable matchmaking behaviour. Defaults apply until an admin changes them."""

    queue_expiry_minutes: int = 30
    confirmation_timeout_seconds: int = 120
    cooldown_seconds: int = 300
    auto_confirm: bool = False
    allow_partial_fill: bool = False
    failed_roster_ttl_seconds: int = 300
    still_looking_lead_minutes: int = 5
    #: "requeue" keeps released players queued when no replacement is found;
    #: "cancel" removes them and tells them to rejoin.
    replacement_policy: str = "requeue"
    finalized_conflict_window_seconds: int = 4 * 3600
    invite_timeout_seconds: int = 120

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> MatchSettings:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


#: Backwards-compatible names used by earlier code and tests.
QueueConfig = MatchSettings
ConfirmationConfig = MatchSettings


async def load_match_settings(session, guild_id: int) -> MatchSettings:
    repo = GuildConfigRepository(session)
    data = dict(await repo.get_value(guild_id, "matching", {}) or {})
    legacy = await repo.get_value(guild_id, "allow_partial_fill", None)
    if legacy and "allow_partial_fill" not in data:
        data["allow_partial_fill"] = bool(legacy.get("enabled"))
    return MatchSettings.from_mapping(data)


# --------------------------------------------------------------------------- channels

#: Channels the bot posts into, per SPEC section 2. Stored by id in guild_config, never
#: looked up by name.
CHANNEL_KEYS = (
    "profile_registration",
    "player_registry",
    "find_a_team",
    "matchmaking_alerts",
    "matchmaking_preferences",
    "tianling_guide",
)


async def get_channel_id(session, guild_id: int, key: str) -> int | None:
    channels = await GuildConfigRepository(session).get_value(guild_id, "channels", {}) or {}
    value = channels.get(key)
    return int(value) if value else None


async def set_channel_ids(session, guild_id: int, **channels: int | None) -> dict:
    unknown = set(channels) - set(CHANNEL_KEYS)
    if unknown:
        raise ValueError(f"unknown channel key(s): {', '.join(sorted(unknown))}")
    return await GuildConfigRepository(session).update_value(guild_id, "channels", **channels)


@dataclass
class VoiceSettings:
    category_id: int | None = None
    grace_seconds: int = 300
    mod_role_ids: list[int] = field(default_factory=list)
    fallback_channel_id: int | None = None
    naming_prefix: str = "⚔️・"

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> VoiceSettings:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


async def load_voice_settings(session, guild_id: int) -> VoiceSettings:
    data = await GuildConfigRepository(session).get_value(guild_id, "voice", {})
    return VoiceSettings.from_mapping(data)


async def next_voice_number(session, guild_id: int, game: str) -> int:
    """Per-game counter used for readable channel names like ``marvel-rivals-01``."""
    repo = GuildConfigRepository(session)
    counters = dict(await repo.get_value(guild_id, "voice_counter", {}) or {})
    counters[game] = int(counters.get(game, 0)) + 1
    await repo.set(guild_id, "voice_counter", counters)
    return counters[game]
