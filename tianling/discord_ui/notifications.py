"""Match notification service."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import discord

from tianling.db.repositories.matches import MatchRepository
from tianling.db.repositories.notification_settings import NotificationSettingRepository
from tianling.db.repositories.users import UserRepository
from tianling.db.session import session_scope
from tianling.discord_ui.proposal import ProposalView
from tianling.discord_ui.theme import THEME


class NotificationService:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._last: dict[tuple, float] = {}
        self._lock = asyncio.Lock()

    async def _rate_limited(self, key: tuple, cooldown: float = 5.0) -> bool:
        async with self._lock:
            now = time.time()
            last = self._last.get(key)
            if last and now - last < cooldown:
                return False
            self._last[key] = now
            return True

    async def post_proposal(self, guild_id: int, group_uuid: str):
        async with session_scope() as session:
            mrepo = MatchRepository(session)
            group = await mrepo.get_group(group_uuid)
            if not group:
                return
            members = await mrepo.list_members(group.id)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = discord.utils.get(guild.text_channels, name="matchmaking-alerts") or discord.utils.get(guild.text_channels, name="matchmaking")
        if not channel:
            return
        embed = discord.Embed(title=f"{THEME.emoji_star} Matchmaking Proposal", description=f"Activity: {group.activity} | Region: {group.region or '-'}", color=THEME.color_primary)
        for m in members:
            status = "Pre-confirmed" if m.response == "accepted" else ("Pending" if m.response is None else m.response)
            embed.add_field(name=f"Member {m.member_id}", value=f"Role: {m.role_assigned or '-'} | {status}", inline=True)
        if group.confirmation_deadline:
            embed.add_field(name="Deadline", value=group.confirmation_deadline.isoformat(), inline=False)
        embed.set_footer(text=THEME.footer)
        view = ProposalView(group_uuid, group.confirmation_deadline)
        try:
            await channel.send(embed=embed, view=view)
        except Exception:
            pass

    async def update_proposal(self, guild_id: int, group_uuid: str):
        await self.post_proposal(guild_id, group_uuid)

    async def announce_final(self, guild_id: int, group_uuid: str, voice_channel_id: int | None = None):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = discord.utils.get(guild.text_channels, name="matchmaking-alerts") or discord.utils.get(guild.text_channels, name="matchmaking")
        if not channel:
            return
        desc = "A Heavenly Match has been found!"
        if voice_channel_id:
            vc = guild.get_channel(voice_channel_id)
            if vc:
                desc += f"\nVoice: {vc.mention}"
        embed = discord.Embed(title=f"{THEME.emoji_sun} Heavenly Match Found", description=desc, color=THEME.color_success)
        embed.set_footer(text=THEME.footer)
        try:
            await channel.send(embed=embed)
        except Exception:
            pass
        # try DMs with opt-in
        async with session_scope() as session:
            mrepo = MatchRepository(session)
            group = await mrepo.get_group(group_uuid)
            if not group:
                return
            members = await mrepo.list_members(group.id)
            nrepo = NotificationSettingRepository(session)
            urepo = UserRepository(session)
            for m in members:
                if not await self._rate_limited((guild_id, m.member_id, "announce")):
                    continue
                user = await urepo.get(guild_id, m.member_id)
                if not user:
                    continue
                ns = await nrepo.get_for_user(user.id)
                if ns and not ns.opt_in:
                    continue
                member = guild.get_member(m.member_id)
                if not member:
                    continue
                try:
                    await member.send(embed=embed)
                except Exception:
                    continue
