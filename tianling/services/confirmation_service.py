"""Confirmation service."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tianling.db.repositories.jobs import JobRepository
from tianling.db.repositories.matches import MatchRepository
from tianling.db.repositories.parties import PartyRepository
from tianling.db.repositories.penalties import PenaltyRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.services.events import GroupFinalized, MemberAccepted, MemberDeclined, MemberTimedOut, ReplacementFailed, ReplacementSucceeded


class ConfirmationConfig:
    def __init__(self, cooldown_seconds: int = 300, auto_confirm: bool = False):
        self.cooldown_seconds = cooldown_seconds
        self.auto_confirm = auto_confirm


class ConfirmationService:
    def __init__(self, session, config: ConfirmationConfig | None = None):
        self.session = session
        self.config = config or ConfirmationConfig()
        self.matches = MatchRepository(session)
        self.queue = QueueRepository(session)
        self.parties = PartyRepository(session)
        self.jobs = JobRepository(session)
        self.penalties = PenaltyRepository(session)
        self._events: list = []

    def pop_events(self) -> list:
        ev = list(self._events)
        self._events.clear()
        return ev

    async def accept(self, guild_id: int, group_uuid: str, member_id: int):
        group = await self.matches.get_group(group_uuid)
        if not group or group.guild_id != guild_id:
            return
        mm = await self.matches.set_member_response(group.id, member_id, "accepted")
        if not mm:
            return
        self._events.append(MemberAccepted(guild_id=guild_id, group_uuid=group_uuid, member_id=member_id, party_id=mm.party_id))
        await self._finalize_if_ready(group)

    async def decline(self, guild_id: int, group_uuid: str, member_id: int):
        group = await self.matches.get_group(group_uuid)
        if not group or group.guild_id != guild_id:
            return
        mm = await self.matches.set_member_response(group.id, member_id, "declined")
        if not mm:
            return
        # determine if party member
        is_party = False
        if mm.party_id:
            pm = await self.parties.list_for_party(guild_id, mm.party_id)
            if any(p.member_id == member_id for p in pm):
                is_party = True
        self._events.append(MemberDeclined(guild_id=guild_id, group_uuid=group_uuid, member_id=member_id, party_id=mm.party_id, is_party_member=is_party))
        if not is_party:
            await self.penalties.increment_and_set(guild_id, member_id, self.config.cooldown_seconds, datetime.now())
        await self._attempt_replacement(group, mm)
        await self.session.flush()

    async def timeout(self, guild_id: int, group_uuid: str, member_id: int):
        group = await self.matches.get_group(group_uuid)
        if not group or group.guild_id != guild_id:
            return
        mm = await self.matches.set_member_response(group.id, member_id, "timeout")
        if not mm:
            return
        is_party = False
        if mm.party_id:
            pm = await self.parties.list_for_party(guild_id, mm.party_id)
            if any(p.member_id == member_id for p in pm):
                is_party = True
        self._events.append(MemberTimedOut(guild_id=guild_id, group_uuid=group_uuid, member_id=member_id, party_id=mm.party_id, is_party_member=is_party))
        if not is_party:
            await self.penalties.increment_and_set(guild_id, member_id, self.config.cooldown_seconds, datetime.now())
        await self._attempt_replacement(group, mm)
        await self.session.flush()

    async def handle_deadline(self, group_uuid: str):
        group = await self.matches.get_group(group_uuid)
        if not group:
            return
        members = await self.matches.list_members(group.id)
        for m in members:
            if m.response not in ("accepted", "declined", "timeout"):
                await self.timeout(group.guild_id, group_uuid, m.member_id)
        await self.session.flush()

    async def _finalize_if_ready(self, group):
        members = await self.matches.list_members(group.id)
        if not members:
            return
        all_done = all(m.response == "accepted" for m in members)
        if all_done:
            await self.matches.set_status(group.group_uuid, "finalized")
            await self.matches.set_proposal_status(group.group_uuid, "finalized")
            self._events.append(GroupFinalized(guild_id=group.guild_id, group_uuid=group.group_uuid, status="finalized", voice_channel_id=group.voice_channel_id))
            try:
                from tianling.discord_ui.notifications import NotificationService
            except Exception:
                pass
            await self.session.commit()
            return
        await self.session.flush()

    async def _attempt_replacement(self, group, failed_mm):
        # simplified: remove failed member, cancel its queue entries if any; mark replacement failed and release
        await self.matches.remove_member(group.id, failed_mm.member_id)
        # try to find replacement by triggering matching again - caller should do; just record
        self._events.append(ReplacementFailed(guild_id=group.guild_id, group_uuid=group.group_uuid, failed_member_id=failed_mm.member_id, released=True))
        await self.matches.set_proposal_status(group.group_uuid, "cancelled")
        await self.matches.set_status(group.group_uuid, "cancelled")
        await self.session.commit()
