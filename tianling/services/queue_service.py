"""Queue service."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from tianling.core.matching import MatchEntry, MatchMember, find_matches
from tianling.core.ranks import RankPool
from tianling.core.seed_loader import SeedLoader
from tianling.db.models import (
    MatchGroup,
    MatchMember as DBMatchMember,
    PartyMember,
    QueueEntry,
    ScheduledJob,
)
from tianling.db.repositories.config import GuildConfigRepository
from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.jobs import JobRepository
from tianling.db.repositories.matches import MatchRepository
from tianling.db.repositories.parties import PartyRepository
from tianling.db.repositories.penalties import PenaltyRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.db.repositories.ranks import RankRepository
from tianling.db.repositories.users import UserRepository
from tianling.services.events import ProposalCreated


class QueueConfig:
    def __init__(self, queue_expiry_minutes: int = 30, confirmation_timeout_seconds: int = 60,
                 cooldown_seconds: int = 300, auto_confirm: bool = False):
        self.queue_expiry_minutes = queue_expiry_minutes
        self.confirmation_timeout_seconds = confirmation_timeout_seconds
        self.cooldown_seconds = cooldown_seconds
        self.auto_confirm = auto_confirm


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
        self.penalties = PenaltyRepository(session)
        self.guild_config = GuildConfigRepository(session)
        self.ranks = RankRepository(session)
        self.loader = SeedLoader()
        self._events: list = []

    def pop_events(self) -> list:
        ev = list(self._events)
        self._events.clear()
        return ev

    async def _emit(self, ev):
        self._events.append(ev)

    async def create_party(self, guild_id: int, leader_member_id: int, member_ids: list[int],
                          declared_roles: dict[int, str] | None = None,
                          guest_flags: dict[int, bool] | None = None,
                          rank_snapshots: dict[int, dict] | None = None,
                          accepted_roles: dict[int, list] | None = None):
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
        self._events.append(
            PartyCreated(guild_id=guild_id, party_id=party_id,
                         leader_member_id=leader_member_id, member_ids=member_ids)
        )
        return party_id

    async def join_queue(self, guild_id: int, member_id: int, game: str, activity: str,
                         queue_type: str = "default", region: str | None = None,
                         role_preference: str | None = None, preferred_roles: list | None = None,
                         acceptable_roles: list | None = None, rank_snapshot: dict | None = None,
                         party_id: str | None = None, party_size: int = 1,
                         leader_member_id: int | None = None, open_slots: dict | None = None):
        now = _utc_now()
        active_penalty = await self.penalties.get_active(guild_id, member_id, now)
        if active_penalty:
            raise ValueError("member on matchmaking cooldown")
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
            queued_at=now,
            expires_at=now + timedelta(minutes=self.config.queue_expiry_minutes),
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
        for pid in party_ids:
            await self.queue.cancel_for_parties(guild_id, [pid])
        await self.session.flush()
        from tianling.services.events import PartyMemberLeft
        for pid in party_ids:
            self._events.append(PartyMemberLeft(guild_id=guild_id, party_id=pid, member_id=member_id))
        return party_ids

    async def _build_rank_pool(self, guild_id: int, game: str, queue_type: str):
        definitions = await self.ranks.list_for(guild_id, game, queue_type)
        if not definitions:
            definitions = await self.ranks.list_for(guild_id, game, "default")
        try:
            ranks_file = self.loader.load_ranks(game, queue_type) if definitions else None
            gap_unit = ranks_file.gap_unit if ranks_file else "division"
        except Exception:
            gap_unit = "division"
        return RankPool.from_rank_definitions(definitions, gap_unit=gap_unit)

    def _snapshot_to_rank(self, snapshot: dict | None, pool: RankPool):
        if not snapshot:
            return None
        try:
            tier = snapshot.get("tier") or snapshot.get("rank_tier")
            division = snapshot.get("division")
            return pool.validate(str(tier), division)
        except Exception:
            return None

    async def _run_matching(self, guild_id: int, game: str, activity: str):
        now = _utc_now()
        entries = await self.queue.list_active(guild_id, game, activity)
        if not entries:
            return
        compositions = self.loader.load_compositions().compositions
        try:
            matching_raw = self.loader.load_raw("matching.yaml")
        except Exception:
            matching_raw = {}
        from tianling.core.matching import MatchingConfig
        matching_config = MatchingConfig.from_mapping(matching_raw)
        allow_partial = False
        try:
            gc = await self.guild_config.get(guild_id, "allow_partial_fill")
            if gc and gc.value and gc.value.get("enabled"):
                allow_partial = True
        except Exception:
            allow_partial = False
        failed_hashes = []

        m_entries: list[MatchEntry] = []
        for e in entries:
            members_list = []
            party_members = []
            if e.party_id:
                party_members = await self.parties.list_for_party(guild_id, e.party_id)
            if party_members:
                pool = await self._build_rank_pool(guild_id, e.game, e.queue_type)
                for pm in party_members:
                    snap = pm.rank_snapshot or {}
                    rank = self._snapshot_to_rank(snap, pool)
                    members_list.append(
                        MatchMember(
                            member_id=pm.member_id if pm.member_id is not None else e.member_id,
                            rank=rank,
                            preferred_roles=tuple(pm.accepted_roles or []),
                            acceptable_roles=tuple(pm.accepted_roles or []),
                            declared_role=pm.declared_role,
                            is_guest=bool(pm.is_guest),
                        )
                    )
            else:
                pool = await self._build_rank_pool(guild_id, e.game, e.queue_type)
                rank = self._snapshot_to_rank(e.rank_snapshot, pool)
                members_list.append(
                    MatchMember(
                        member_id=e.member_id,
                        rank=rank,
                        preferred_roles=tuple(e.preferred_roles or []),
                        acceptable_roles=tuple(e.acceptable_roles or []),
                        declared_role=e.role_preference,
                        is_guest=False,
                    )
                )
            m_entries.append(
                MatchEntry(
                    entry_id=str(e.id),
                    game=e.game,
                    activity=e.activity,
                    members=tuple(members_list),
                    queue_type=e.queue_type,
                    region=e.region,
                    party_id=e.party_id,
                    leader_member_id=e.leader_member_id,
                    queued_at=e.queued_at,
                    expires_at=e.expires_at,
                )
            )

        proposals = find_matches(
            m_entries,
            compositions,
            config=matching_config,
            now=now,
            failed_hashes=failed_hashes,
            allow_partial=allow_partial,
        )

        for p in proposals:
            entry_map = {me.entry_id: me for me in m_entries}
            entry_ids = [me.entry_id for me in p.entries] if hasattr(p, "entries") else list(p.entry_ids)
            # find corresponding entries
            selected_entries = []
            for eid in entry_ids:
                if eid in entry_map:
                    selected_entries.append(entry_map[eid])
                else:
                    for me in m_entries:
                        if str(me.entry_id) == str(eid):
                            selected_entries.append(me)
                            break
            if not selected_entries:
                continue
            leader_mid = None
            for se in selected_entries:
                if se.leader_member_id:
                    leader_mid = se.leader_member_id
                    break
            if leader_mid is None and selected_entries:
                leader_mid = selected_entries[0].member_ids[0]
            proposal_id = str(uuid.uuid4())
            # use savepoint per proposal
            async with self.session.begin_nested():
                try:
                    reserved = []
                    for se in selected_entries:
                        entry_id_int = int(str(se.entry_id))
                        qe = await self.session.get(QueueEntry, entry_id_int)
                        if not qe or qe.status != "queued":
                            raise ValueError("not available")
                        qe.status = "reserved"
                        qe.proposal_id = proposal_id
                        reserved.append(qe)
                    await self.session.flush()
                except Exception:
                    raise

            group_uuid = str(uuid.uuid4())
            mg = MatchGroup(
                guild_id=guild_id,
                group_uuid=group_uuid,
                game=p.game,
                activity=p.activity,
                queue_type=p.queue_type,
                region=p.region,
                party_id=proposal_id,
                status="proposed",
                proposal_status="pending",
                confirmation_deadline=(
                    now + timedelta(seconds=self.config.confirmation_timeout_seconds)
                    if not self.config.auto_confirm
                    else None
                ),
                roster={
                    "entries": entry_ids,
                    "roles": getattr(p, "role_assignments", None),
                },
            )
            self.session.add(mg)
            await self.session.flush()

            # create match members for EVERY player in roster
            for se in selected_entries:
                party_members = []
                if se.party_id:
                    party_members = await self.parties.list_for_party(guild_id, se.party_id)
                if party_members:
                    for pm in party_members:
                        response = "accepted" if pm.confirmed else None
                        mm = DBMatchMember(
                            guild_id=guild_id,
                            match_group_id=mg.id,
                            party_id=se.party_id,
                            member_id=pm.member_id if pm.member_id is not None else se.member_ids[0],
                            user_id=None,
                            role_assigned=(
                                getattr(p, "role_assignments", {}).get(str(pm.member_id))
                                if getattr(p, "role_assignments", None)
                                else pm.declared_role
                            ),
                            response=response,
                        )
                        self.session.add(mm)
                else:
                    for mid in se.member_ids:
                        mm = DBMatchMember(
                            guild_id=guild_id,
                            match_group_id=mg.id,
                            party_id=se.party_id,
                            member_id=mid,
                            user_id=None,
                            role_assigned=(
                                getattr(p, "role_assignments", {}).get(str(mid))
                                if getattr(p, "role_assignments", None)
                                else None
                            ),
                            response="accepted",
                        )
                        self.session.add(mm)
            await self.session.flush()

            self.session.add(
                ScheduledJob(
                    guild_id=guild_id,
                    job_type="confirmation_deadline",
                    run_at=mg.confirmation_deadline or now,
                    payload={"group_uuid": group_uuid},
                    status="pending",
                )
            )
            self.session.add(
                ScheduledJob(
                    guild_id=guild_id,
                    job_type="matching_tick",
                    run_at=now + timedelta(seconds=1),
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
                    party_ids=[se.party_id for se in selected_entries if se.party_id],
                    member_ids=[mid for se in selected_entries for mid in se.member_ids],
                    confirmation_deadline=mg.confirmation_deadline,
                    auto_confirm=self.config.auto_confirm,
                    roster=mg.roster,
                )
            )
            await self.session.flush()
        # loop continues over all proposals
