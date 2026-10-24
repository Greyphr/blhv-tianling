"""Voice channel manager."""
from __future__ import annotations

import discord

from tianling.db.models import ScheduledJob, TemporaryChannel
from tianling.db.repositories.channels import ChannelRepository
from tianling.db.repositories.jobs import JobRepository
from tianling.db.repositories.matches import MatchRepository
from tianling.db.session import session_scope


class VoiceConfig:
    def __init__(self, category_id: int | None = None, naming_prefix: str = "⚔️・", grace_seconds: int = 300, fallback_channel_id: int | None = None):
        self.category_id = category_id
        self.naming_prefix = naming_prefix
        self.grace_seconds = grace_seconds
        self.fallback_channel_id = fallback_channel_id


class VoiceManager:
    def __init__(self, bot: discord.Client, config: VoiceConfig):
        self.bot = bot
        self.config = config

    async def create_for_group(self, guild_id: int, group_uuid: str, member_ids: list[int], mod_role_ids: list[int] | None = None):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return None
        mod_role_ids = mod_role_ids or []
        category = guild.get_channel(self.config.category_id) if self.config.category_id else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        }
        for mid in member_ids:
            m = guild.get_member(mid)
            if m:
                overwrites[m] = discord.PermissionOverwrite(view_channel=True, connect=True)
        for rid in mod_role_ids:
            r = guild.get_role(rid)
            if r:
                overwrites[r] = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
        name = f"{self.config.naming_prefix}{group_uuid[:8]}"
        try:
            if len(guild.voice_channels) >= 500:  # soft cap
                raise discord.HTTPException(None, "cap")
            if category and isinstance(category, discord.CategoryChannel) and len(category.channels) >= 50:
                raise discord.HTTPException(None, "catcap")
            ch = await guild.create_voice_channel(name, category=category, overwrites=overwrites)
        except Exception:
            # fallback
            if self.config.fallback_channel_id:
                return {"fallback": True, "channel_id": self.config.fallback_channel_id}
            return None
        async with session_scope() as session:
            crepo = ChannelRepository(session)
            mrepo = MatchRepository(session)
            group = await mrepo.get_group(group_uuid)
            tc = TemporaryChannel(
                guild_id=guild_id,
                channel_id=ch.id,
                group_id=group_uuid,
                match_group_id=group.id if group else None,
                status="active",
            )
            await crepo.create(tc)
            await session.commit()
        return {"channel_id": ch.id, "category_id": category.id if category else None}

    async def handle_empty(self, guild_id: int, channel_id: int):
        async with session_scope() as session:
            crepo = ChannelRepository(session)
            tc = await crepo.get_by_channel_id(channel_id)
            if not tc or tc.status != "active":
                return
            from datetime import UTC, datetime

            tc.empty_since = datetime.now(UTC)
            tc.status = "empty"
            jrepo = JobRepository(session)
            await jrepo.enqueue(
                ScheduledJob(
                    guild_id=guild_id,
                    job_type="empty_channel_cleanup",
                    run_at=datetime.now(UTC).replace(microsecond=0),
                    payload={"channel_id": channel_id},
                    status="pending",
                )
            )
            await session.commit()

    async def cleanup_if_empty(self, guild_id: int, channel_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        ch = guild.get_channel(channel_id)
        if not ch:
            async with session_scope() as session:
                crepo = ChannelRepository(session)
                tc = await crepo.get_by_channel_id(channel_id)
                if tc:
                    await session.delete(tc)
                await session.commit()
            return
        try:
            if isinstance(ch, discord.VoiceChannel) and len(ch.members) == 0:
                async with session_scope() as session:
                    crepo = ChannelRepository(session)
                    tc = await crepo.get_by_channel_id(channel_id)
                    if not tc:
                        return  # only delete recorded
                    await ch.delete(reason="empty grace period")
                    await session.delete(tc)
                    await session.commit()
        except Exception:
            pass

    async def add_friend(self, guild_id: int, channel_id: int, member_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return False
        ch = guild.get_channel(channel_id)
        member = guild.get_member(member_id)
        if not isinstance(ch, discord.VoiceChannel) or not member:
            return False
        try:
            await ch.set_permissions(member, view_channel=True, connect=True)
            return True
        except Exception:
            return False

    async def reconcile(self, guild_id: int):
        async with session_scope() as session:
            from sqlalchemy import select

            from tianling.db.models import TemporaryChannel

            res = await session.execute(select(TemporaryChannel).where(TemporaryChannel.guild_id == guild_id))
            tcs = res.scalars().all()
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        for tc in tcs:
            ch = guild.get_channel(tc.channel_id)
            if not ch:
                async with session_scope() as session2:
                    await session2.delete(tc)
                    await session2.commit()
                continue
            if isinstance(ch, discord.VoiceChannel) and len(ch.members) == 0 and tc.status == "active":
                await self.handle_empty(guild_id, tc.channel_id)
