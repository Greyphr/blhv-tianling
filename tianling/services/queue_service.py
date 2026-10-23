"""Queue service."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tianling.core.matching import MatchEntry, find_matches
from tianling.db.models import MatchGroup, MatchMember, PartyMember, QueueEntry, ScheduledJob
from tianling.db.repositories.channels import ChannelRepository
from tianling.db.repositories.config import GuildConfigRepository
from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.jobs import JobRepository
from tianling.db.repositories.matches import MatchRepository
from tianling.db.repositories.parties import PartyRepository
from tianling.db.repositories.penalties import PenaltyRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.db.repositories.users import UserRepository
from tianling.services.events import ProposalCreated


@dataclass(slots=True)
class QueueConfig:
    queue_expiry_minutes: int = 30
    confirmation_timeout_seconds: int = 60
    cooldown_seconds: int = 300
    auto_confirm: bool = False


class QueueService:
    def __init__(self, session, config: QueueConfig | None = None):
        self.session = session
        self.config = config or QueueConfig()
        self.users = UserRepository(session)
        self.profiles = GameProfileRepository(session)
        self.queue = QueueRepository(session)
        self.parties = PartyRepository(session)
        self.matches = MatchRepository(session)
        self.jobs = JobRepository(session)
        self.channels = ChannelRepository(session)
        self.penalties = PenaltyRepository(session)
        self.guild_config = GuildConfigRepository(session)
        self._events: list = []

    def pop_events(self) -> list:
        ev = list(self._events)
        self._events.clear()
        return ev

    async def _emit(self, ev):
        self._events.append(ev)

    async def create_party(self, guild_id: int, leader_member_id: int, member_ids: list[int], declared_roles: dict[int, str] | None = None, guest_flags: dict[int, bool] | None = None, rank_snapshots: dict[int, dict] | None = None, accepted_roles: dict[int, list] | None = None):
        party_id = str(uuid.uuid4())
        declared_roles = declared_roles or {}
        guest_flags = guest_flags or {}
        rank_snapshots = rank_snapshots or {}
        accepted_roles = accepted_roles or {}
        for mid in member_ids:
            pm = PartyMember(
                guild_id=guild_id,
                party_id=party_id,
                member_id=mid,
                declared_role=declared_roles.get(mid),
                accepted_roles=accepted_roles.get(mid),
                rank_snapshot=rank_snapshots.get(mid),
                is_guest=bool(guest_flags.get(mid)),
                confirmed=True,
            )
            await self.parties.add_member(pm)
        await self.session.flush()
        from tianling.services.events import PartyCreated

        self._events.append(PartyCreated(guild_id=guild_id, party_id=party_id, leader_member_id=leader_member_id, member_ids=member_ids))
        return party_id

    async def join_queue(self, guild_id: int, member_id: int, game: str, activity: str, queue_type: str = "default", region: str | None = None, role_preference: str | None = None, preferred_roles: list | None = None, acceptable_roles: list | None = None, rank_snapshot: dict | None = None, party_id: str | None = None, party_size: int = 1, leader_member_id: int | None = None, open_slots: dict | None = None) -> QueueEntry:
        user = await self.users.get_or_create(guild_id, member_id)
        entry = QueueEntry(
            guild_id=guild_id,
            user_id=user.id,
            member_id=member_id,
            game=game,
            activity=activity,
            queue_type=queue_type,
            region=region,
            role_preference=role_preference,
            preferred_roles=preferred_roles,
            acceptable_roles=acceptable_roles,
            rank_snapshot=rank_snapshot,
            party_id=party_id,
            party_size=party_size,
            leader_member_id=leader_member_id,
            open_slots=open_slots,
            status="queued",
            queued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=self.config.queue_expiry_minutes),
            relaxation_level=0,
        )
        entry = await self.queue.join(entry)
        await self.session.flush()
        await self._run_matching(guild_id, game, activity)
        return entry

    async def cancel_queue(self, guild_id: int, member_id: int, game: str, activity: str) -> bool:
        res = await self.queue.leave(guild_id, member_id, game, activity)
        if res:
            party_ids = await self.parties.party_ids_for_member(guild_id, member_id)
            if party_ids:
                await self.matches.cancel_for_parties(guild_id, party_ids)
                await self.queue.cancel_for_parties(guild_id, party_ids)
        await self.session.flush()
        return res

    async def leave_party(self, guild_id: int, member_id: int) -> list[str]:
        party_ids = await self.parties.party_ids_for_member(guild_id, member_id)
        for pid in party_ids:
            await self.parties.remove_member(guild_id, pid, member_id)
        # cancel queue entries for affected parties
        for pid in party_ids:
            await self.queue.cancel_for_parties(guild_id, [pid])
        await self.session.flush()
        from tianling.services.events import PartyMemberLeft

        for pid in party_ids:
            self._events.append(PartyMemberLeft(guild_id=guild_id, party_id=pid, member_id=member_id))
        return party_ids

    async def _run_matching(self, guild_id: int, game: str, activity: str):
        entries = await self.queue.list_active(guild_id, game, activity)
        if not entries:
            return
        m_entries: list[MatchEntry] = []
        for e in entries:
            m_entries.append(
                MatchEntry(
                    entry_id=e.id,
                    guild_id=e.guild_id,
                    game=e.game,
                    activity=e.activity,
                    queue_type=e.queue_type,
                    region=e.region,
                    member_id=e.member_id,
                    party_id=e.party_id,
                    party_size=e.party_size,
                    leader_member_id=e.leader_member_id,
                    rank_snapshot=e.rank_snapshot or {},
                    preferred_roles=e.preferred_roles,
                    acceptable_roles=e.acceptable_roles,
                    role_preference=e.role_preference,
                    open_slots=e.open_slots,
                    relaxation_level=e.relaxation_level,
                )
            )
        proposals = find_matches(m_entries)
        for p in proposals:
            entry_ids = [me.entry_id for me in p.entries]
            leader_mid = None
            party_ids = []
            for me in p.entries:
                for qe in entries:
                    if qe.id == me.entry_id:
                        if qe.leader_member_id and leader_mid is None:
                            leader_mid = qe.leader_member_id
                        if qe.party_id and qe.party_id not in party_ids:
                            party_ids.append(qe.party_id)
                        break
            if leader_mid is None and p.entries:
                leader_mid = p.entries[0].member_id
            party_key = p.entries[0].party_id or str(uuid.uuid4())
            try:
                await self.queue.reserve_entries(guild_id, party_key, entry_ids, leader_mid or 0)
            except Exception:
                await self.session.rollback()
                continue
            group_uuid = str(uuid.uuid4())
            mg = MatchGroup(
                guild_id=guild_id,
                group_uuid=group_uuid,
                game=p.game,
                activity=p.activity,
                queue_type=p.queue_type,
                region=p.region,
                party_id=party_key,
                status="proposed",
                proposal_status="pending",
                confirmation_deadline=datetime.now(UTC) + timedelta(seconds=self.config.confirmation_timeout_seconds) if not self.config.auto_confirm else None,
                roster={"entries": [me.entry_id for me in p.entries], "roles": p.role_assignments},
            )
            mg = await self.matches.create_group(mg)
            for me in p.entries:
                mm = MatchMember(
                    guild_id=guild_id,
                    match_group_id=mg.id,
                    party_id=me.party_id,
                    member_id=me.member_id,
                    user_id=None,
                    role_assigned=p.role_assignments.get(str(me.member_id)) if p.role_assignments else None,
                )
                await self.matches.add_member(mm)
            await self.jobs.enqueue(
                ScheduledJob(
                    guild_id=guild_id,
                    job_type="confirmation_deadline",
                    run_at=mg.confirmation_deadline or (datetime.now(UTC) + timedelta(seconds=self.config.confirmation_timeout_seconds)),
                    payload={"group_uuid": group_uuid},
                    status="pending",
                )
            )
            await self.jobs.enqueue(
                ScheduledJob(
                    guild_id=guild_id,
                    job_type="matching_tick",
                    run_at=datetime.now(UTC) + timedelta(seconds=1),
                    payload={"game": game, "activity": activity},
                    status="pending",
                )
            )
            self._events.append(
                ProposalCreated(
                    guild_id=guild_id,
                    group_uuid=group_uuid,
                    game=p.game,
                    activity=p.activity,
                    queue_type=p.queue_type,
                    region=p.region,
                    party_ids=party_ids,
                    member_ids=[me.member_id for me in p.entries],
                    confirmation_deadline=mg.confirmation_deadline,
                    auto_confirm=self.config.auto_confirm,
                    roster=mg.roster,
                )
            )
            # attempt to post to Discord if available
            try:
                from tianling.discord_ui.notifications import NotificationService

                # bot not passed here; skip if no bot context
            except Exception:
                pass
            await self.session.commit()
            return
