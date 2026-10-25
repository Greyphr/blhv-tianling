"""discord.py implementation of the registry adapter."""

from __future__ import annotations

import discord

from tianling.discord_ui.registry_adapter import Embed
from tianling.discord_ui.retry import with_retry


def _to_discord(embed: Embed) -> discord.Embed:
    out = discord.Embed(title=embed.title, description=embed.description, color=embed.color)
    for name, value, inline in embed.fields or []:
        out.add_field(name=name, value=value[:1024] or "-", inline=inline)
    if embed.footer:
        out.set_footer(text=embed.footer)
    return out


class DiscordRegistryAdapter:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot

    async def get_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await with_retry(lambda: self.bot.fetch_channel(channel_id))
        return channel

    async def send_embed(self, channel, embed: Embed) -> int:
        message = await with_retry(
            lambda: channel.send(
                embed=_to_discord(embed), allowed_mentions=discord.AllowedMentions.none()
            )
        )
        return message.id

    async def edit_embed(self, channel, message_id: int, embed: Embed) -> None:
        message = await with_retry(lambda: channel.fetch_message(message_id))
        await with_retry(
            lambda: message.edit(
                embed=_to_discord(embed), allowed_mentions=discord.AllowedMentions.none()
            )
        )

    async def delete_message(self, channel, message_id: int) -> None:
        try:
            message = await with_retry(lambda: channel.fetch_message(message_id))
            await with_retry(message.delete)
        except discord.NotFound:
            return  # already gone
