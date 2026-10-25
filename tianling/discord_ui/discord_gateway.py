"""Production :class:`Gateway` backed by discord.py.

Every API call goes through ``with_retry`` (capped exponential backoff for 429 and
5xx).  Permission or validation errors (4xx) are surfaced as ``GatewayError`` so
callers can fall back and notify instead of crashing.
"""

from __future__ import annotations

import logging

import discord

from tianling.discord_ui.gateway import Card, GatewayError
from tianling.discord_ui.retry import with_retry

log = logging.getLogger(__name__)

_MENTIONS = discord.AllowedMentions(users=True, roles=True, everyone=False)
_VOICE_CATEGORY_CAP = 50


def to_embed(card: Card) -> discord.Embed:
    embed = discord.Embed(title=card.title, description=card.description or None, color=card.color)
    for name, value, inline in card.fields:
        embed.add_field(name=name, value=value[:1024] or "-", inline=inline)
    if card.footer:
        embed.set_footer(text=card.footer)
    return embed


def build_view(card: Card) -> discord.ui.View | None:
    """Attach the right persistent buttons for a card (disabled when the card is closed)."""
    from tianling.discord_ui.lfg import PartyInviteButton, StillLookingButton
    from tianling.discord_ui.proposal import ProposalButton

    view = discord.ui.View(timeout=None)
    if card.proposal_group:
        for action in ("accept", "decline"):
            view.add_item(
                ProposalButton(action, card.proposal_group, disabled=not card.buttons_enabled)
            )
    elif card.party_invite:
        party_id, member_id = card.party_invite
        for action in ("accept", "decline"):
            view.add_item(PartyInviteButton(action, party_id, member_id))
    elif card.still_looking:
        game, activity = card.still_looking
        for action in ("yes", "no"):
            view.add_item(StillLookingButton(action, game, activity))
    else:
        return None
    return view


class DiscordGateway:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot

    async def _channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await with_retry(lambda: self.bot.fetch_channel(channel_id))
            except discord.NotFound:
                return None
        return channel

    async def post_card(self, channel_id: int, card: Card) -> int | None:
        channel = await self._channel(channel_id)
        if channel is None or not hasattr(channel, "send"):
            raise GatewayError(f"channel {channel_id} not found")
        try:
            message = await with_retry(
                lambda: channel.send(
                    content=card.content or None,
                    embed=to_embed(card),
                    view=build_view(card) or discord.utils.MISSING,
                    allowed_mentions=_MENTIONS,
                )
            )
        except discord.HTTPException as exc:
            raise GatewayError(f"could not post message: {exc}") from exc
        return message.id

    async def edit_card(self, channel_id: int, message_id: int, card: Card) -> bool:
        channel = await self._channel(channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return False
        try:
            message = await with_retry(lambda: channel.fetch_message(message_id))
            await with_retry(
                lambda: message.edit(
                    content=card.content or None,
                    embed=to_embed(card),
                    view=build_view(card),
                    allowed_mentions=_MENTIONS,
                )
            )
        except discord.NotFound:
            return False
        except discord.HTTPException as exc:
            raise GatewayError(f"could not edit message: {exc}") from exc
        return True

    async def send_dm(self, member_id: int, card: Card) -> bool:
        try:
            user = self.bot.get_user(member_id) or await with_retry(
                lambda: self.bot.fetch_user(member_id)
            )
            await with_retry(
                lambda: user.send(
                    embed=to_embed(card), view=build_view(card) or discord.utils.MISSING
                )
            )
            return True
        except (discord.Forbidden, discord.NotFound):
            return False  # DMs closed: not an error

    async def create_voice(
        self,
        guild_id: int,
        name: str,
        category_id: int | None,
        member_ids: list[int],
        mod_role_ids: list[int],
    ) -> int:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise GatewayError("guild not available")
        category = guild.get_channel(category_id) if category_id else None
        if (
            isinstance(category, discord.CategoryChannel)
            and len(category.channels) >= _VOICE_CATEGORY_CAP
        ):
            raise GatewayError("voice category is full")
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False)
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True, move_members=True
            )
        for member_id in member_ids:  # guests/test players were already filtered out
            member = guild.get_member(member_id)
            if member is not None:
                overwrites[member] = discord.PermissionOverwrite(
                    view_channel=True, connect=True, speak=True
                )
        for role_id in mod_role_ids:
            role = guild.get_role(role_id)
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, connect=True, manage_channels=True
                )
        try:
            channel = await with_retry(
                lambda: guild.create_voice_channel(
                    name,
                    category=category if isinstance(category, discord.CategoryChannel) else None,
                    overwrites=overwrites,
                    reason="Tianling: confirmed group",
                )
            )
        except discord.HTTPException as exc:
            raise GatewayError(str(exc)) from exc
        return channel.id

    async def delete_channel(self, guild_id: int, channel_id: int) -> bool:
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if channel is None:
            return False
        try:
            await with_retry(lambda: channel.delete(reason="Tianling: empty grace period over"))
        except discord.NotFound:
            return True
        except discord.HTTPException as exc:
            raise GatewayError(str(exc)) from exc
        return True

    async def voice_member_count(self, guild_id: int, channel_id: int) -> int | None:
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if not isinstance(channel, discord.VoiceChannel):
            return None
        return sum(1 for m in channel.members if not m.bot)

    async def present_member_ids(self, guild_id: int, member_ids: list[int]) -> set[int]:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return set(member_ids)  # cannot tell; do not block the group on our own outage
        present: set[int] = set()
        for member_id in member_ids:
            if guild.get_member(member_id) is not None:
                present.add(member_id)
                continue
            try:
                await with_retry(lambda mid=member_id: guild.fetch_member(mid))
                present.add(member_id)
            except discord.NotFound:
                pass
        return present

    async def display_name(self, guild_id: int, member_id: int) -> str:
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(member_id) if guild else None
        return member.display_name if member else f"User {member_id}"
