"""Channel setup: create/find the six channels, store their ids, post the panels once."""

from __future__ import annotations

import logging

import discord

from tianling.db.repositories.config import GuildConfigRepository
from tianling.discord_ui.panels import (
    FindTeamPanel,
    ProfileRegistryPanel,
    find_team_panel_embed,
    guide_embed,
    registration_panel_embed,
)
from tianling.discord_ui.registry_filters import RegistryFilterView
from tianling.discord_ui.retry import with_retry
from tianling.discord_ui.theme import THEME
from tianling.services.settings import CHANNEL_KEYS, set_channel_ids

log = logging.getLogger(__name__)

DEFAULT_CATEGORY = "THE HEAVENLY REGISTRY"
#: config key -> default channel name
CHANNEL_NAMES = {
    "profile_registration": "profile-registration",
    "player_registry": "player-registry",
    "find_a_team": "find-a-team",
    "matchmaking_alerts": "matchmaking-alerts",
    "matchmaking_preferences": "matchmaking-preferences",
    "tianling_guide": "tianling-guide",
}
assert set(CHANNEL_NAMES) == set(CHANNEL_KEYS)

#: Names used by earlier versions of the bot; adopted instead of creating duplicates.
LEGACY_ALIASES = {
    "profile_registration": ("profile-registry", "profile-registration"),
    "find_a_team": ("find-a-team", "find-team"),
    "matchmaking_alerts": ("matchmaking", "matchmaking-alerts"),
    "tianling_guide": ("announcements", "tianling-guide"),
}


def normalize(name: str) -> str:
    """Lower-case and keep only letters/digits, so emoji, dashes and caps don't matter."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def find_by_name(candidates, wanted: str, aliases: tuple[str, ...] = ()):
    """First candidate whose normalized name equals the wanted name or a legacy alias."""
    targets = {normalize(wanted), *(normalize(a) for a in aliases)}
    for candidate in candidates:
        if normalize(candidate.name) in targets:
            return candidate
    return None


REQUIRED_PERMS = (
    "manage_channels",
    "manage_messages",
    "move_members",
    "send_messages",
    "embed_links",
)


def missing_permissions(guild: discord.Guild) -> list[str]:
    me = guild.me
    if me is None:
        return ["(bot member not cached)"]
    return [
        p.replace("_", " ").title() for p in REQUIRED_PERMS if not getattr(me.guild_permissions, p)
    ]


async def setup_guild(bot, guild: discord.Guild) -> dict:
    """Idempotent: reuse stored/existing channels, create what is missing, refresh panels."""
    result: dict = {
        "created": [],
        "reused": [],
        "missing_permissions": missing_permissions(guild),
        "errors": [],
    }
    async with bot.scope() as session:
        stored = dict(
            await GuildConfigRepository(session).get_value(guild.id, "channels", {}) or {}
        )
        panels = dict(await GuildConfigRepository(session).get_value(guild.id, "panels", {}) or {})

    def _existing(key: str, name: str):
        stored_id = stored.get(key)
        channel = guild.get_channel(int(stored_id)) if stored_id else None
        return channel or find_by_name(guild.text_channels, name, LEGACY_ALIASES.get(key, ()))

    # Only make a category if a channel is actually missing; a renamed category is left alone.
    category = None
    if any(_existing(key, name) is None for key, name in CHANNEL_NAMES.items()):
        category = find_by_name(guild.categories, DEFAULT_CATEGORY)
        if category is None:
            try:
                category = await with_retry(lambda: guild.create_category(DEFAULT_CATEGORY))
            except discord.HTTPException as exc:
                result["errors"].append(f"category: {exc}")

    me_overwrite = discord.PermissionOverwrite(
        send_messages=True, embed_links=True, view_channel=True
    )
    read_only = {
        guild.default_role: discord.PermissionOverwrite(send_messages=False, add_reactions=False)
    }
    if guild.me is not None:
        read_only[guild.me] = me_overwrite

    channels: dict[str, discord.TextChannel] = {}
    for key, name in CHANNEL_NAMES.items():
        channel = _existing(key, name)
        if channel is not None:
            result["reused"].append(name)
        else:
            try:
                channel = await with_retry(
                    lambda n=name: guild.create_text_channel(
                        n, category=category, overwrites=read_only
                    )
                )
                result["created"].append(name)
            except discord.HTTPException as exc:
                result["errors"].append(f"{name}: {exc}")
                continue
        channels[key] = channel

    async with bot.scope() as session:
        await set_channel_ids(session, guild.id, **{k: c.id for k, c in channels.items()})

    posts = {
        "profile_registration": (registration_panel_embed(), ProfileRegistryPanel()),
        "find_a_team": (find_team_panel_embed(), FindTeamPanel()),
        "tianling_guide": (guide_embed(), None),
        "player_registry": (
            discord.Embed(
                title=f"{THEME.emoji_registry} Player Registry",
                description="Every registered player appears below. "
                "Use the menu to browse by game.",
                color=THEME.color_primary,
            ).set_footer(text=THEME.footer),
            RegistryFilterView(),
        ),
    }
    for key, (embed, view) in posts.items():
        channel = channels.get(key)
        if channel is None:
            continue
        try:
            panels[key] = await _post_or_edit(channel, panels.get(key), embed, view)
        except discord.HTTPException as exc:
            result["errors"].append(f"panel {key}: {exc}")
    async with bot.scope() as session:
        await GuildConfigRepository(session).set(guild.id, "panels", panels)
    return result


async def _post_or_edit(
    channel: discord.TextChannel, message_id: int | None, embed: discord.Embed, view
) -> int:
    """Edit the stored panel message if it still exists, else post a new one (no duplicates)."""
    kwargs = {"embed": embed, "view": view} if view is not None else {"embed": embed}
    if message_id:
        try:
            message = await with_retry(lambda: channel.fetch_message(int(message_id)))
            await with_retry(lambda: message.edit(**kwargs))
            return message.id
        except discord.NotFound:
            pass
    message = await with_retry(lambda: channel.send(**kwargs))
    return message.id
