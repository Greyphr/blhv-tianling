"""Voice channel manager (gateway based, so it is testable without Discord).

Rules from SPEC section 10: readable names (``marvel-rivals-01``), access only
for roster members and moderators, a configurable grace period before deleting an
empty channel, rejoining cancels deletion, only channels recorded in
``temporary_channels`` are ever deleted, and creation failure falls back to a
configured shared channel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from tianling.core.timeutil import utcnow
from tianling.db.models import ScheduledJob, TemporaryChannel
from tianling.db.repositories.channels import ChannelRepository
from tianling.db.repositories.jobs import JobRepository
from tianling.db.repositories.matches import MatchRepository
from tianling.discord_ui.gateway import Gateway, GatewayError
from tianling.services.roster import is_real_member
from tianling.services.settings import load_voice_settings, next_voice_number

log = logging.getLogger(__name__)


@dataclass
class VoiceResult:
    channel_id: int | None = None
    fallback: bool = False
    error: str | None = None
    already_existed: bool = False
    mod_role_ids: list[int] = field(default_factory=list)


class VoiceManager:
    def __init__(self, gateway: Gateway, scope) -> None:
        self.gateway = gateway
        self.scope = scope

    async def create_for_group(self, guild_id: int, group_uuid: str) -> VoiceResult:
        # 1) read what we need and reserve a name number, then release the DB.
        async with self.scope() as session:
            matches = MatchRepository(session)
            group = await matches.get_group(group_uuid)
            if group is None or group.guild_id != guild_id:
                return VoiceResult(error="group not found")
            if group.voice_channel_id:
                return VoiceResult(channel_id=group.voice_channel_id, already_existed=True)
            members = await matches.list_members(group.id)
            # every roster member (all party members) gets access; guests/test players have
            # no Discord account
            member_ids = [
                m.member_id
                for m in members
                if is_real_member(m.member_id, is_guest=m.is_guest, is_test=m.is_test)
            ]
            cfg = await load_voice_settings(session, guild_id)
            number = await next_voice_number(session, guild_id, group.game)
            game = group.game
            group_id = group.id
        name = f"{cfg.naming_prefix}{game.replace('_', '-')}-{number:02d}"

        # 2) talk to Discord with no DB transaction held open.
        try:
            channel_id = await self.gateway.create_voice(
                guild_id, name, cfg.category_id, member_ids, cfg.mod_role_ids
            )
        except Exception as exc:
            log.exception("voice channel creation failed for group %s", group_uuid)
            error = str(exc) or type(exc).__name__
            if cfg.fallback_channel_id:
                async with self.scope() as session:
                    await MatchRepository(session).set_voice_channel(
                        group_uuid, cfg.fallback_channel_id
                    )
                return VoiceResult(
                    channel_id=cfg.fallback_channel_id,
                    fallback=True,
                    error=error,
                    mod_role_ids=cfg.mod_role_ids,
                )
            return VoiceResult(error=error, mod_role_ids=cfg.mod_role_ids)

        # 3) record it so cleanup may delete it later (and only it).
        async with self.scope() as session:
            await ChannelRepository(session).create(
                TemporaryChannel(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    group_id=group_uuid,
                    match_group_id=group_id,
                    status="active",
                )
            )
            await MatchRepository(session).set_voice_channel(group_uuid, channel_id)
            # Nobody is in it yet: start the grace clock so it is not leaked forever.
            await self._schedule_cleanup(session, guild_id, channel_id, cfg.grace_seconds)
        return VoiceResult(channel_id=channel_id)

    async def _schedule_cleanup(
        self, session, guild_id: int, channel_id: int, grace_seconds: int
    ) -> None:
        await JobRepository(session).enqueue(
            ScheduledJob(
                guild_id=guild_id,
                job_type="empty_channel_cleanup",
                run_at=utcnow() + timedelta(seconds=grace_seconds),
                payload={"channel_id": channel_id},
                status="pending",
            )
        )

    async def on_voice_update(self, guild_id: int, channel_id: int) -> str:
        """Called for both the channel someone left and the one they joined."""
        count = await self.gateway.voice_member_count(guild_id, channel_id)
        if count is None:
            return "unknown"
        async with self.scope() as session:
            channels = ChannelRepository(session)
            tc = await channels.get_by_channel_id(channel_id)
            if tc is None:
                return "untracked"  # not ours: never touch it
            jobs = JobRepository(session)
            if count == 0:
                if not await channels.mark_empty(channel_id, utcnow()):
                    return "already_empty"
                cfg = await load_voice_settings(session, guild_id)
                await self._schedule_cleanup(session, guild_id, channel_id, cfg.grace_seconds)
                return "grace_started"
            if await channels.mark_active(channel_id):
                await jobs.cancel_matching("empty_channel_cleanup", "channel_id", channel_id)
                return "deletion_cancelled"
            return "active"

    async def cleanup_if_empty(self, guild_id: int, channel_id: int) -> str:
        """Job: delete the channel only if it is ours, still empty, and the grace is over."""
        async with self.scope() as session:
            channels = ChannelRepository(session)
            tc = await channels.get_by_channel_id(channel_id)
            if tc is None:
                return "untracked"
            cfg = await load_voice_settings(session, guild_id)
            grace = timedelta(seconds=cfg.grace_seconds)
            empty_since = tc.empty_since
            group_uuid = tc.group_id
            status = tc.status
        count = await self.gateway.voice_member_count(guild_id, channel_id)
        if count is None:
            async with self.scope() as session:
                await ChannelRepository(session).delete_by_channel_id(channel_id)
            return "gone"
        if count > 0:
            async with self.scope() as session:
                if await ChannelRepository(session).mark_active(channel_id):
                    await JobRepository(session).cancel_matching(
                        "empty_channel_cleanup", "channel_id", channel_id
                    )
            return "occupied"
        # Never deleted the instant it's created: it must have been empty for the full grace.
        reference = empty_since if status == "empty" and empty_since else None
        if reference is not None and utcnow() < reference + grace - timedelta(seconds=1):
            return "not_yet"
        try:
            await self.gateway.delete_channel(guild_id, channel_id)
        except GatewayError:
            log.exception("could not delete empty channel %s", channel_id)
            return "delete_failed"
        async with self.scope() as session:
            await ChannelRepository(session).delete_by_channel_id(channel_id)
            if group_uuid:
                matches = MatchRepository(session)
                await matches.transition(group_uuid, "finalized", "completed")
        return "deleted"

    async def reconcile(self, guild_id: int) -> dict[str, int]:
        """Match stored channels against reality (manual deletions, restarts)."""
        stats = {"removed": 0, "grace_started": 0, "checked": 0}
        async with self.scope() as session:
            tracked = [
                (tc.channel_id, tc.status)
                for tc in await ChannelRepository(session).list_for_guild(guild_id)
            ]
        for channel_id, status in tracked:
            stats["checked"] += 1
            count = await self.gateway.voice_member_count(guild_id, channel_id)
            if count is None:
                async with self.scope() as session:
                    await ChannelRepository(session).delete_by_channel_id(channel_id)
                stats["removed"] += 1
            elif count == 0 and status == "active":
                if await self.on_voice_update(guild_id, channel_id) == "grace_started":
                    stats["grace_started"] += 1
            elif count > 0 and status == "empty":
                await self.on_voice_update(guild_id, channel_id)
        return stats
