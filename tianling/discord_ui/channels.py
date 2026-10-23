"""Channel setup helper."""
from __future__ import annotations

import logging

import discord

from tianling.db.repositories.config import GuildConfigRepository

log = logging.getLogger(__name__)

DEFAULT_CATEGORY = "THE HEAVENLY REGISTRY"
DEFAULT_CHANNELS = [
    "profile-registry",
    "find-a-team",
    "matchmaking",
    "announcements",
]


class ChannelSetup:
    def __init__(self, guild: discord.Guild, session):
        self.guild = guild
        self.session = session
        self.config = GuildConfigRepository(session)

    async def ensure_setup(self) -> dict:
        missing_perms = []
        me = self.guild.me
        if me:
            if not me.guild_permissions.manage_channels:
                missing_perms.append("Manage Channels")
            if not me.guild_permissions.move_members:
                missing_perms.append("Move Members")
        category_name = DEFAULT_CATEGORY
        cat = discord.utils.get(self.guild.categories, name=category_name)
        if not cat:
            try:
                cat = await self.guild.create_category(category_name)
            except discord.Forbidden:
                missing_perms.append("Manage Channels")
        channels = {}
        for ch in DEFAULT_CHANNELS:
            existing = discord.utils.get(self.guild.text_channels, name=ch)
            if existing:
                channels[ch] = existing
                continue
            try:
                if cat:
                    c = await self.guild.create_text_channel(ch, category=cat)
                else:
                    c = await self.guild.create_text_channel(ch)
                channels[ch] = c
            except discord.Forbidden:
                missing_perms.append("Manage Channels")
        await self.session.commit()
        return {"category": category_name, "channels": list(channels.keys()), "missing_permissions": missing_perms}
