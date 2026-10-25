"""Queue service: joining, parties, matching and proposal creation.

Transaction rules (see AGENTS.md): services only ``flush``.  The caller owns the
transaction (``session_scope`` in a UI handler or a scheduler tick) and commits
after the service returns.  Events collected in ``pop_events()`` must be
dispatched *after* that commit.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from tianling.core.matching import (
    MatchEntry,
    MatchingConfig,
    MatchMember,
    MatchProposal,
    find_matches,
)
from tianling.core.party_slots import open_slots as compute_open_slots
from tianling.core.party_slots import role_capacity, role_overflow
from tianling.core.ranks import Rank, RankError, RankPool
from tianling.core.seed_loader import CompositionEntry, RoleSlot, SeedLoader
from tianling.core.timeutil import utcnow
from tianling.db.models import (
    MatchGroup,
    PartyMember,
    QueueEntry,
    ScheduledJob,
)
from tianling.db.models import MatchMember as DBMatchMember
from tianling.db.repositories.compositions import CompositionRepository
from tianling.db.repositories.config import GuildConfigRepository
from tianling.db.repositories.failed_rosters import FailedRosterRepository
from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.jobs import JobRepository
from tianling.db.repositories.matches import MatchRepository
from tianling.db.repositories.parties import PartyRepository
from tianling.db.repositories.penalties import PenaltyRepository
from tianling.db.repositories.queue import QueueRepository, ReservationConflict
from tianling.db.repositories.ranks import RankRepository
from tianling.db.repositories.users import UserRepository
from tianling.services.events import (
    Event,
    PartyCreated,
    PartyInvite,
    PartyMemberLeft,
    PartyUpdated,
    ProposalCreated,
    QueueExpired,
    StillLooking,
)
from tianling.services.roster import is_real_member, roster_view
from tianling.services.settings import MatchSettings, QueueConfig, load_match_settings

log = logging.getLogger(__name__)

__all__ = [
    "AlreadyQueued",
    "CooldownActive",
    "InvalidQueueRequest",
    "LeaveResult",
    "PartyNotReady",
    "QueueConfig",
    "QueueError",
    "QueueService",
    "QueueStatus",
    "new_guest_id",
    "people_hash",
]


class QueueError(ValueError):
    """Base class for user-facing queue errors; ``str(exc)`` is safe to show."""


class CooldownActive(QueueError):
    def __init__(self, member_id: int, expires_at: datetime) -> None:
        self.member_id = member_id
        self.expires_at = expires_at
        super().__init__(
            f"<@{member_id}> is on a matchmaking cooldown until "
            f"<t:{int(expires_at.timestamp())}:R> after missing a confirmation."
        )


class AlreadyQueued(QueueError):
    pass


class PartyNotReady(QueueError):
    pass


class InvalidQueueRequest(QueueError):
    pass


def people_hash(proposal: MatchProposal) -> str:
    """Stable hash of *who plays which role*.

    The engine's own ``roster_hash`` includes queue-entry ids, so a player who
    declines and immediately rejoins would produce a "new" hash for the same
    group.  This one only depends on the people and their roles.
    """
    parts = sorted(f"{m.member_id}:{m.role or ''}" for m in proposal.members)
    payload = "|".join([proposal.game, proposal.activity, *parts])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def new_guest_id() -> int:
    """Synthetic negative id for a guest slot (guests have no Discord account)."""
    return -(secrets.randbits(62) + 1)


@dataclass
class LeaveResult:
    cancelled: list[int] = field(default_factory=list)
    in_proposal: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.cancelled)


@dataclass
class QueueStatus:
    entry: QueueEntry
    party_members: list[PartyMember]
    group_uuid: str | None = None


@dataclass
class _Loaded:
    row: QueueEntry
    party: list[PartyMember]
    match_entry: MatchEntry


class QueueService:
    def __init__(self, session, config: MatchSettings | None = None) -> None:
        self.session = session
        self.config = config or MatchSettings()
        self.users = UserRepository(session)
        self.profiles = GameProfileRepository(session)
        self.queue = QueueRepository(session)
        self.parties = PartyRepository(session)
        self.matches = MatchRepository(session)
        self.jobs = JobRepository(session)
        self.penalties = PenaltyRepository(session)
        self.guild_config = GuildConfigRepository(session)
        self.ranks = RankRepository(session)
        self.compositions = CompositionRepository(session)
        self.failed = FailedRosterRepository(session)
        self.loader = SeedLoader()
        self._events: list[Event] = []
        self._pool_cache: dict[tuple[int, str, str], RankPool | None] = {}

    @classmethod
    async def create(cls, session, guild_id: int) -> QueueService:
        """Build a service whose settings come from the guild's stored config."""
        return cls(session, await load_match_settings(session, guild_id))

    # ------------------------------------------------------------------ events

    def pop_events(self) -> list[Event]:
        events = list(self._events)
        self._events.clear()
        return events

    async def _emit(self, event: Event) -> None:
        self._events.append(event)

    # ----------------------------------------------------------------- parties

    async def create_party(
        self,
        guild_id: int,
        leader_member_id: int,
        member_ids: list[int],
        declared_roles: dict[int, str] | None = None,
        guest_flags: dict[int, bool] | None = None,
        rank_snapshots: dict[int, dict] | None = None,
        accepted_roles: dict[int, list] | None = None,
        *,
        pending_ids: set[int] | None = None,
        test_ids: set[int] | None = None,
        game: str | None = None,
    ) -> str:
        """Persist a party. Every member (guests included) gets a ``party_members`` row.

        Linked Discord members in ``pending_ids`` must accept an invite before the
        party can queue; everyone else (the leader, guests, test players) is
        confirmed immediately.
        """
        declared_roles = declared_roles or {}
        guest_flags = guest_flags or {}
        rank_snapshots = rank_snapshots or {}
        accepted_roles = accepted_roles or {}
        pending_ids = pending_ids or set()
        test_ids = test_ids or set()
        if leader_member_id not in member_ids:
            raise InvalidQueueRequest("the party leader must be a party member")
        if len(set(member_ids)) != len(member_ids):
            raise InvalidQueueRequest("a member appears twice in the party")
        party_id = str(uuid.uuid4())
        invite_deadline = utcnow() + timedelta(seconds=self.config.invite_timeout_seconds)
        for mid in member_ids:
            is_guest = bool(guest_flags.get(mid)) or mid < 0
            pending = mid in pending_ids and not is_guest and mid != leader_member_id
            await self.parties.add_member(
                PartyMember(
                    guild_id=guild_id,
                    party_id=party_id,
                    member_id=mid,
                    declared_role=declared_roles.get(mid),
                    accepted_roles=accepted_roles.get(mid),
                    rank_snapshot=rank_snapshots.get(mid),
                    is_guest=is_guest,
                    is_test=mid in test_ids,
                    game=game,
                    confirmed=not pending,
                    invite_expires_at=invite_deadline if pending else None,
                )
            )
        await self.session.flush()
        await self._emit(
            PartyCreated(
                guild_id=guild_id,
                party_id=party_id,
                leader_member_id=leader_member_id,
                member_ids=list(member_ids),
            )
        )
        for mid in pending_ids:
            if mid != leader_member_id and mid in member_ids and mid > 0:
                await self._emit(
                    PartyInvite(
                        guild_id=guild_id,
                        party_id=party_id,
                        leader_member_id=leader_member_id,
                        invited_member_id=mid,
                        expires_at=invite_deadline,
                    )
                )
        return party_id

    async def add_party_member(
        self,
        guild_id: int,
        party_id: str,
        member_id: int,
        *,
        declared_role: str | None = None,
        accepted_roles: list | None = None,
        rank_snapshot: dict | None = None,
        is_guest: bool = False,
        is_test: bool = False,
        max_size: int | None = None,
        leader_member_id: int | None = None,
        game: str | None = None,
    ) -> PartyMember:
        members = await self.parties.list_for_party(guild_id, party_id)
        if any(m.member_id == member_id for m in members):
            raise InvalidQueueRequest("that member is already in the party")
        if max_size is not None and len(members) >= max_size:
            raise InvalidQueueRequest(f"this party is already at the maximum size ({max_size})")
        pending = not is_guest and not is_test and member_id > 0
        deadline = utcnow() + timedelta(seconds=self.config.invite_timeout_seconds)
        pm = await self.parties.add_member(
            PartyMember(
                guild_id=guild_id,
                party_id=party_id,
                member_id=member_id,
                declared_role=declared_role,
                accepted_roles=accepted_roles,
                rank_snapshot=rank_snapshot,
                is_guest=is_guest,
                is_test=is_test,
                game=game or (members[0].game if members else None),
                confirmed=not pending,
                invite_expires_at=deadline if pending else None,
            )
        )
        await self._emit(
            PartyUpdated(
                guild_id=guild_id,
                party_id=party_id,
                member_ids=[m.member_id for m in members] + [member_id],
            )
        )
        if pending and leader_member_id is not None:
            await self._emit(
                PartyInvite(
                    guild_id=guild_id,
                    party_id=party_id,
                    leader_member_id=leader_member_id,
                    invited_member_id=member_id,
                    expires_at=deadline,
                )
            )
        return pm

    async def respond_party_invite(
        self, guild_id: int, party_id: str, member_id: int, accept: bool
    ) -> bool:
        """Linked member answers a party invite. Returns True if it changed anything."""
        members = await self.parties.list_for_party(guild_id, party_id)
        target = next((m for m in members if m.member_id == member_id), None)
        if target is None or target.confirmed:
            return False
        now = utcnow()
        if target.invite_expires_at is not None and target.invite_expires_at <= now:
            await self.parties.remove_member(guild_id, party_id, member_id)
            await self.session.flush()
            return False
        if accept:
            target.confirmed = True
            target.invite_expires_at = None
        else:
            await self.parties.remove_member(guild_id, party_id, member_id)
        await self.session.flush()
        remaining = [m.member_id for m in members if accept or m.member_id != member_id]
        await self._emit(PartyUpdated(guild_id=guild_id, party_id=party_id, member_ids=remaining))
        return True

    async def expire_party_invites(self, now: datetime | None = None) -> int:
        """Drop invited members who never answered before their deadline."""
        now = now or utcnow()
        from sqlalchemy import select

        res = await self.session.execute(
            select(PartyMember).where(
                PartyMember.confirmed.is_(False),
                PartyMember.invite_expires_at.is_not(None),
                PartyMember.invite_expires_at <= now,
            )
        )
        stale = list(res.scalars().all())
        for pm in stale:
            await self.parties.remove_member(pm.guild_id, pm.party_id, pm.member_id or 0)
            await self._emit(
                PartyMemberLeft(
                    guild_id=pm.guild_id, party_id=pm.party_id, member_id=pm.member_id or 0
                )
            )
        await self.session.flush()
        return len(stale)

    async def leave_party(self, guild_id: int, member_id: int) -> list[str]:
        """Remove a member from every party; their parties' queue entries are cancelled.

        If the party was already reserved in a proposal this behaves like the
        member declining it (handled by ``ConfirmationService``).
        """
        from tianling.services.confirmation_service import ConfirmationService

        party_ids = await self.parties.party_ids_for_member(guild_id, member_id)
        for pid in party_ids:
            for entry in await self._party_entries(guild_id, pid):
                if entry.status == "reserved":
                    group = await self._group_for_entry(entry)
                    if group is not None:
                        cs = ConfirmationService(self.session, self.config)
                        await cs.decline(guild_id, group.group_uuid, member_id)
                        self._events.extend(cs.pop_events())
            await self.parties.remove_member(guild_id, pid, member_id)
            await self.queue.cancel_for_parties(guild_id, [pid])
            await self._emit(PartyMemberLeft(guild_id=guild_id, party_id=pid, member_id=member_id))
        await self.session.flush()
        return party_ids

    async def _party_entries(self, guild_id: int, party_id: str) -> list[QueueEntry]:
        from sqlalchemy import select

        res = await self.session.execute(
            select(QueueEntry).where(
                QueueEntry.guild_id == guild_id,
                QueueEntry.party_id == party_id,
                QueueEntry.status.in_(("queued", "reserved")),
            )
        )
        return list(res.scalars().all())

    async def _group_for_entry(self, entry: QueueEntry) -> MatchGroup | None:
        if not entry.proposal_id:
            return None
        from sqlalchemy import select

        res = await self.session.execute(
            select(MatchGroup).where(MatchGroup.proposal_id == entry.proposal_id)
        )
        return res.scalar_one_or_none()

    # --------------------------------------------------------------- joining

    async def _composition_for(
        self, guild_id: int, game: str, activity: str
    ) -> CompositionEntry | None:
        for comp in await self._composition_entries(guild_id):
            if comp.game == game and comp.activity == activity:
                return comp
        return None

    async def _composition_entries(self, guild_id: int) -> list[CompositionEntry]:
        """Compositions come from the guild's DB rows (admin-editable), else the seed files."""
        rows = await self.compositions.list_for_guild(guild_id)
        if not rows:
            return list(self.loader.load_compositions().compositions)
        return [
            CompositionEntry(
                game=r.game,
                activity=r.activity,
                group_size=r.group_size,
                roles=[RoleSlot(**role) for role in (r.roles or [])],
                role_required=r.role_required,
                allowed_rank_gap=r.allowed_rank_gap,
                party_max_size=r.party_max_size,
                max_party_rank_gap=r.max_party_rank_gap,
                partial_fill_allowed=r.partial_fill_allowed,
                verify_in_game=r.verify_in_game,
            )
            for r in rows
        ]

    async def _check_cooldowns(self, guild_id: int, member_ids: list[int], now: datetime) -> None:
        for mid in member_ids:
            penalty = await self.penalties.get_active(guild_id, mid, now)
            if penalty is not None:
                raise CooldownActive(mid, penalty.expires_at)

    async def join_queue(
        self,
        guild_id: int,
        member_id: int,
        game: str,
        activity: str,
        queue_type: str = "default",
        region: str | None = None,
        role_preference: str | None = None,
        preferred_roles: list | None = None,
        acceptable_roles: list | None = None,
        rank_snapshot: dict | None = None,
        party_id: str | None = None,
        party_size: int = 1,
        leader_member_id: int | None = None,
        open_slots: dict | None = None,
        *,
        either_with: tuple[str, ...] | list[str] = (),
        is_test: bool = False,
    ) -> QueueEntry:
        """Queue a solo player, or an existing party as ONE entry.

        ``either_with`` lists additional activities of the same game to queue for
        (Deadlock "Either"); they become sibling entries and when one is reserved
        the others are cancelled in the same transaction.
        """
        now = utcnow()
        activities = list(dict.fromkeys([activity, *either_with]))
        party: list[PartyMember] = []
        if party_id:
            party = await self.parties.list_for_party(guild_id, party_id)
            if not party:
                raise InvalidQueueRequest("that party no longer exists")
            leader_member_id = leader_member_id or member_id
            if all(pm.member_id != leader_member_id for pm in party):
                raise InvalidQueueRequest("the party leader is not part of the party")
            waiting = [pm for pm in party if not pm.confirmed]
            if waiting:
                names = ", ".join(f"<@{pm.member_id}>" for pm in waiting)
                raise PartyNotReady(f"Waiting for {names} to accept the party invite.")
            member_id = leader_member_id
            party_size = len(party)
        elif party_size > 1:
            raise InvalidQueueRequest("create a party first, then queue it")

        # Everyone who will end up in a group: all party members, or just the solo player.
        if party:
            who = [(pm.member_id or 0, pm.is_guest, pm.is_test) for pm in party]
        else:
            who = [(member_id, False, is_test)]
        real_ids = [
            mid for mid, guest, test in who if is_real_member(mid, is_guest=guest, is_test=test)
        ]
        present_ids = [mid for mid, guest, _ in who if not guest]
        await self._check_cooldowns(guild_id, real_ids, now)

        for act in activities:
            comp = await self._composition_for(guild_id, game, act)
            if comp is None:
                raise InvalidQueueRequest(f"{game} has no activity named '{act}'")
            if party_size > comp.party_max_size:
                raise InvalidQueueRequest(
                    f"a party of {party_size} is too large for {act} (max {comp.party_max_size})"
                )
            if party:
                await self._check_party(guild_id, game, act, comp, party)
            for mid in present_ids:
                existing = [
                    e
                    for e in await self.queue.list_active_for_member(guild_id, mid, game=game)
                    if e.activity == act
                ]
                if existing:
                    who_text = "You are" if mid == member_id else f"<@{mid}> is"
                    raise AlreadyQueued(f"{who_text} already queued for {game} {act}.")
        busy = await self.matches.conflicting_member_ids(
            guild_id, present_ids, statuses=("proposed",)
        )
        if busy:
            names = ", ".join(f"<@{m}>" for m in sorted(busy) if m > 0)
            raise AlreadyQueued(f"{names or 'A player'} already has a pending match proposal.")

        # Queueing again means the player is done with their previous finalized group.
        await self.matches.release_finalized_membership(guild_id, real_ids)

        if party:
            comp0 = await self._composition_for(guild_id, game, activity)
            if comp0 is not None and comp0.roles:
                open_slots = compute_open_slots(comp0, [pm.declared_role for pm in party])
        user = await self.users.get_or_create(guild_id, member_id)
        either_group = str(uuid.uuid4()) if len(activities) > 1 else None
        created: list[QueueEntry] = []
        for act in activities:
            entry = QueueEntry(
                guild_id=guild_id,
                user_id=user.id,
                member_id=member_id,
                game=game,
                activity=act,
                queue_type=queue_type,
                region=region,
                role_preference=role_preference,
                preferred_roles=preferred_roles,
                acceptable_roles=acceptable_roles,
                rank_snapshot=rank_snapshot,
                party_id=party_id,
                party_size=party_size,
                leader_member_id=leader_member_id if party_id else None,
                open_slots=open_slots,
                status="queued",
                queued_at=now,
                expires_at=now + timedelta(minutes=self.config.queue_expiry_minutes),
                relaxation_level=0,
                either_group=either_group,
                is_test=is_test,
            )
            created.append(await self.queue.join(entry))
        if either_group:
            for entry in created:
                entry.sibling_entry_ids = [str(o.id) for o in created if o.id != entry.id]
        await self.session.flush()

        for act in activities:
            await self.run_matching(guild_id, game, act)
        await self.session.refresh(created[0])
        return created[0]

    async def _check_party(
        self,
        guild_id: int,
        game: str,
        activity: str,
        comp: CompositionEntry,
        party: list[PartyMember],
    ) -> None:
        """Reject parties the matcher could never place (size, role overflow, rank spread)."""
        if comp.roles and comp.role_required:
            capacity = role_capacity(comp)
            for pm in party:
                if pm.declared_role and pm.declared_role not in capacity:
                    raise InvalidQueueRequest(
                        f"'{pm.declared_role}' is not a role in {game} {activity}."
                    )
            overflow = role_overflow(comp, [pm.declared_role for pm in party])
            if overflow:
                role, excess = next(iter(overflow.items()))
                raise InvalidQueueRequest(
                    f"Your party has {excess} too many {role.replace('_', ' ')} "
                    f"(a group has {capacity.get(role, 0)})."
                )
            missing = [pm for pm in party if not pm.declared_role]
            if missing:
                raise InvalidQueueRequest("Every party member needs a role before queueing.")
        ranks: list[Rank] = []
        for pm in party:
            rank = await self._rank_from_snapshot(guild_id, game, "default", pm.rank_snapshot)
            if rank is not None:
                ranks.append(rank)
        if len(ranks) >= 2:
            pool = await self._rank_pool(guild_id, game, ["default"])
            if pool is not None:
                try:
                    low, high = pool.party_range(ranks)
                    gap = pool.rank_gap(low, high)
                except RankError:
                    return
                if gap > comp.max_party_rank_gap:
                    raise InvalidQueueRequest(
                        f"Your party's ranks are too far apart (max gap {comp.max_party_rank_gap})."
                    )

    async def leave_queue(
        self,
        guild_id: int,
        member_id: int,
        game: str | None = None,
        activity: str | None = None,
    ) -> LeaveResult:
        """Cancel the member's queued entries (and 'Either' siblings).

        Entries already reserved in a proposal are *not* cancelled here: the
        member must Decline the proposal, so the group is handled properly.
        """
        result = LeaveResult()
        entries = await self.queue.list_active_for_member(guild_id, member_id, game=game)
        for entry in entries:
            if activity is not None and entry.activity != activity:
                continue
            if entry.party_id and entry.leader_member_id not in (None, member_id):
                continue  # only the party leader can withdraw the party's entry
            if entry.status == "reserved":
                group = await self._group_for_entry(entry)
                if group is not None:
                    result.in_proposal.append(group.group_uuid)
                continue
            ids = [entry.id]
            if entry.either_group:
                ids.extend(
                    e.id
                    for e in entries
                    if e.either_group == entry.either_group and e.status == "queued"
                )
            await self.queue.set_status(list(dict.fromkeys(ids)), "cancelled")
            result.cancelled.extend(ids)
        await self.session.flush()
        result.cancelled = list(dict.fromkeys(result.cancelled))
        return result

    async def cancel_queue(self, guild_id: int, member_id: int, game: str, activity: str) -> bool:
        """Back-compat wrapper around :meth:`leave_queue`."""
        return bool(await self.leave_queue(guild_id, member_id, game, activity))

    async def get_status(self, guild_id: int, member_id: int) -> list[QueueStatus]:
        statuses: list[QueueStatus] = []
        for entry in await self.queue.list_active_for_member(guild_id, member_id):
            party = (
                await self.parties.list_for_party(guild_id, entry.party_id)
                if entry.party_id
                else []
            )
            group = await self._group_for_entry(entry)
            statuses.append(QueueStatus(entry, party, group.group_uuid if group else None))
        return statuses

    # ------------------------------------------------------------- expiry

    async def expire_entries(self, now: datetime | None = None) -> list[QueueEntry]:
        """Expire queued entries past ``expires_at`` and notify; prompt near-expiry ones."""
        now = now or utcnow()
        lead = now + timedelta(minutes=self.config.still_looking_lead_minutes)
        for entry in await self.queue.list_needing_still_looking(now, lead):
            entry.still_looking_sent = True
            await self._emit(
                StillLooking(
                    guild_id=entry.guild_id,
                    member_id=entry.member_id,
                    game=entry.game,
                    activity=entry.activity,
                    expires_at=entry.expires_at,
                    member_ids=await self._human_ids(entry),
                )
            )
        expired = await self.queue.list_expired(now)
        for entry in expired:
            entry.status = "expired"
            await self._emit(
                QueueExpired(
                    guild_id=entry.guild_id,
                    member_id=entry.member_id,
                    game=entry.game,
                    activity=entry.activity,
                    member_ids=await self._human_ids(entry),
                )
            )
        await self.session.flush()
        return expired

    async def extend_entry(self, guild_id: int, member_id: int, game: str, activity: str) -> bool:
        """'Still looking?' -> yes: push the expiry out again."""
        now = utcnow()
        for entry in await self.queue.list_active_for_member(guild_id, member_id, game=game):
            if entry.activity == activity and entry.status == "queued":
                entry.expires_at = now + timedelta(minutes=self.config.queue_expiry_minutes)
                entry.still_looking_sent = False
                await self.session.flush()
                return True
        return False

    async def _human_ids(self, entry: QueueEntry) -> list[int]:
        if entry.party_id:
            party = await self.parties.list_for_party(entry.guild_id, entry.party_id)
            return [
                pm.member_id
                for pm in party
                if pm.member_id
                and is_real_member(pm.member_id, is_guest=pm.is_guest, is_test=pm.is_test)
            ]
        return [] if entry.is_test else [entry.member_id]

    # ------------------------------------------------------------- matching

    async def run_all_matching(self, guild_id: int | None = None) -> list[str]:
        """Matching tick: every game/activity pair that has someone waiting."""
        created: list[str] = []
        for gid, game, activity in await self.queue.active_game_activities():
            if guild_id is not None and gid != guild_id:
                continue
            created.extend(await self.run_matching(gid, game, activity))
        return created

    async def _rank_pool(self, guild_id: int, game: str, queue_types: list[str]) -> RankPool | None:
        for qt in dict.fromkeys([*queue_types, "default", "role_queue"]):
            key = (guild_id, game, qt)
            if key not in self._pool_cache:
                definitions = await self.ranks.list_for(guild_id, game, qt)
                if not definitions:
                    self._pool_cache[key] = None
                else:
                    try:
                        gap_unit = self.loader.load_ranks(game, qt).gap_unit
                    except (FileNotFoundError, ValueError):
                        gap_unit = "division"
                    self._pool_cache[key] = RankPool.from_rank_definitions(
                        definitions, gap_unit=gap_unit
                    )
            if self._pool_cache[key] is not None:
                return self._pool_cache[key]
        return None

    async def _rank_from_snapshot(
        self, guild_id: int, game: str, entry_qt: str, snapshot: dict | None
    ) -> Rank | None:
        if not snapshot:
            return None
        tier = snapshot.get("tier") or snapshot.get("rank_tier")
        if not tier:
            return None
        pool = await self._rank_pool(
            guild_id, game, [snapshot.get("queue_type") or entry_qt, entry_qt]
        )
        if pool is None:
            return None
        try:
            return pool.validate(str(tier), snapshot.get("division"))
        except ValueError:
            return None

    async def _load_entries(self, guild_id: int, rows: list[QueueEntry]) -> list[_Loaded]:
        party_ids = list({r.party_id for r in rows if r.party_id})
        parties = await self.parties.list_for_parties(guild_id, party_ids)
        loaded: list[_Loaded] = []
        for row in rows:
            party = parties.get(row.party_id, []) if row.party_id else []
            if row.party_id and not party:
                log.warning("queue entry %s references missing party %s", row.id, row.party_id)
                continue
            members: list[MatchMember] = []
            if party:
                for pm in party:
                    rank = await self._rank_from_snapshot(
                        guild_id, row.game, row.queue_type, pm.rank_snapshot
                    )
                    accepted = tuple(pm.accepted_roles or ())
                    members.append(
                        MatchMember(
                            member_id=int(pm.member_id or 0),
                            rank=rank,
                            preferred_roles=accepted,
                            acceptable_roles=accepted,
                            declared_role=pm.declared_role,
                            is_guest=bool(pm.is_guest),
                        )
                    )
            else:
                rank = await self._rank_from_snapshot(
                    guild_id, row.game, row.queue_type, row.rank_snapshot
                )
                preferred = list(row.preferred_roles or [])
                acceptable = list(row.acceptable_roles or [])
                declared = None
                if row.role_preference:
                    if acceptable:
                        preferred = [row.role_preference, *preferred]
                    else:
                        declared = row.role_preference
                members.append(
                    MatchMember(
                        member_id=row.member_id,
                        rank=rank,
                        preferred_roles=tuple(preferred),
                        acceptable_roles=tuple(acceptable),
                        declared_role=declared,
                    )
                )
            engine_qt = row.queue_type if row.queue_type == row.activity else "default"
            loaded.append(
                _Loaded(
                    row=row,
                    party=party,
                    match_entry=MatchEntry(
                        entry_id=str(row.id),
                        game=row.game,
                        activity=row.activity,
                        members=tuple(members),
                        queue_type=engine_qt,
                        region=row.region,
                        party_id=row.party_id,
                        leader_member_id=row.leader_member_id,
                        queued_at=row.queued_at,
                        expires_at=row.expires_at,
                        sibling_entry_ids=tuple(str(s) for s in (row.sibling_entry_ids or ())),
                        either_group=row.either_group,
                    ),
                )
            )
        return loaded

    def _matching_config(self) -> MatchingConfig:
        try:
            return MatchingConfig.from_mapping(self.loader.load_matching_config().model_dump())
        except (FileNotFoundError, ValueError):
            log.exception("could not load matching.yaml; relaxation disabled")
            return MatchingConfig()

    async def run_matching(
        self,
        guild_id: int,
        game: str,
        activity: str,
        *,
        carry_accepted: set[int] | None = None,
    ) -> list[str]:
        """Find rosters for one game/activity and create a proposal for every one found.

        Returns the new group uuids.  ``carry_accepted`` are members who already
        accepted a previous proposal that fell through: they stay accepted.
        """
        now = utcnow()
        rows = await self.queue.list_queued(guild_id, game, activity)
        if not rows:
            return []
        loaded = await self._load_entries(guild_id, rows)
        # A player already sitting in a pending proposal (for another activity) is not matchable.
        all_ids = [m for item in loaded for m in item.match_entry.member_ids if m > 0]
        busy = await self.matches.conflicting_member_ids(guild_id, all_ids, statuses=("proposed",))
        loaded = [item for item in loaded if not busy.intersection(item.match_entry.member_ids)]
        if not loaded:
            return []

        compositions = [
            c
            for c in await self._composition_entries(guild_id)
            if c.game == game and c.activity == activity
        ]
        failed_people = set(await self.failed.active_hashes(guild_id, game, activity, now))
        engine_blocked: set[str] = set()
        match_entries = [item.match_entry for item in loaded]
        matching_config = self._matching_config()
        proposals: list[MatchProposal] = []
        for _ in range(8):
            proposals = find_matches(
                match_entries,
                compositions,
                config=matching_config,
                now=now,
                failed_hashes=engine_blocked,
                allow_partial=self.config.allow_partial_fill,
            )
            blocked = [p for p in proposals if people_hash(p) in failed_people]
            if not blocked:
                break
            # Ask the engine for alternatives instead of the recently failed rosters.
            engine_blocked.update(p.roster_hash for p in blocked)
        else:
            proposals = [p for p in proposals if people_hash(p) not in failed_people]

        by_entry = {str(item.row.id): item for item in loaded}
        created: list[str] = []
        for proposal in proposals:
            try:
                async with self.session.begin_nested():
                    group = await self._create_group(
                        guild_id, proposal, by_entry, now, carry_accepted or set()
                    )
            except ReservationConflict as exc:
                log.info("skipping proposal %s: %s", proposal.roster_hash[:8], exc)
                continue
            created.append(group.group_uuid)
            await self._after_create(group)
        return created

    async def _create_group(
        self,
        guild_id: int,
        proposal: MatchProposal,
        by_entry: dict[str, _Loaded],
        now: datetime,
        carry_accepted: set[int],
    ) -> MatchGroup:
        """Reserve entries and create the group. Raises ReservationConflict to roll back."""
        entry_ids = [int(eid) for eid in proposal.entry_ids]
        proposal_id = str(uuid.uuid4())
        await self.queue.reserve_entries(guild_id, proposal_id, entry_ids)

        human_ids = [m.member_id for m in proposal.members if m.member_id > 0]
        busy = await self.matches.conflicting_member_ids(
            guild_id, human_ids, statuses=("proposed",)
        )
        if busy:
            raise ReservationConflict("a player is already in another pending proposal")

        # Either-queue: reserving one sibling cancels all the others in this transaction.
        for eid in entry_ids:
            item = by_entry[str(eid)]
            if item.row.either_group:
                await self.queue.cancel_siblings(guild_id, item.row.either_group, entry_ids)

        # Who needs to answer?  Party members were accepted when the party formed; guests
        # never answer; carried-over members already accepted a previous version of this roster.
        info: dict[int, dict[str, Any]] = {}
        for eid in entry_ids:
            item = by_entry[str(eid)]
            if item.party:
                for pm in item.party:
                    info[int(pm.member_id or 0)] = {
                        "accepted": bool(pm.confirmed),
                        "guest": bool(pm.is_guest),
                        "test": bool(pm.is_test),
                        "snapshot": pm.rank_snapshot,
                        "entry_id": eid,
                        "party_id": item.row.party_id,
                    }
            else:
                info[item.row.member_id] = {
                    "accepted": False,
                    "guest": False,
                    "test": bool(item.row.is_test),
                    "snapshot": item.row.rank_snapshot,
                    "entry_id": eid,
                    "party_id": None,
                }

        deadline = (
            None
            if self.config.auto_confirm
            else now + timedelta(seconds=self.config.confirmation_timeout_seconds)
        )
        group = MatchGroup(
            guild_id=guild_id,
            group_uuid=str(uuid.uuid4()),
            game=proposal.game,
            activity=proposal.activity,
            queue_type=proposal.queue_type,
            region=proposal.region,
            party_id=None,
            proposal_id=proposal_id,
            roster_hash=people_hash(proposal),
            status="proposed",
            proposal_status="pending",
            confirmation_deadline=deadline,
            roster={
                "entry_ids": entry_ids,
                "partial": proposal.partial,
                "direct": proposal.direct,
                "relaxation_level": proposal.relaxation_level,
            },
        )
        self.session.add(group)
        await self.session.flush()

        # One match_members row for EVERY player in the roster (not one per queue entry).
        pending: list[int] = []
        for rm in proposal.members:
            details = info.get(rm.member_id) or {}
            accepted = (
                self.config.auto_confirm
                or bool(details.get("guest"))
                or bool(details.get("accepted"))
                or rm.member_id in carry_accepted
            )
            snapshot = dict(details.get("snapshot") or {})
            if rm.rank is not None:
                snapshot.update(tier=rm.rank.tier, division=rm.rank.division, label=rm.rank.label)
            self.session.add(
                DBMatchMember(
                    guild_id=guild_id,
                    match_group_id=group.id,
                    party_id=rm.party_id,
                    member_id=rm.member_id,
                    user_id=None,
                    role_assigned=rm.role,
                    response="accepted" if accepted else None,
                    entry_id=details.get("entry_id"),
                    is_guest=bool(details.get("guest")),
                    is_test=bool(details.get("test")),
                    rank_snapshot=snapshot or None,
                )
            )
            if not accepted:
                pending.append(rm.member_id)
        await self.session.flush()

        if pending:
            self.session.add(
                ScheduledJob(
                    guild_id=guild_id,
                    job_type="confirmation_deadline",
                    run_at=deadline or now,
                    payload={"group_uuid": group.group_uuid},
                    status="pending",
                )
            )
        test_cfg = await self.guild_config.get_value(guild_id, "test_mode", {}) or {}
        if test_cfg.get("auto_accept") and any(
            d.get("test") and not d.get("accepted") for d in info.values()
        ):
            self.session.add(
                ScheduledJob(
                    guild_id=guild_id,
                    job_type="test_auto_accept",
                    run_at=now + timedelta(seconds=float(test_cfg.get("delay_seconds", 2))),
                    payload={
                        "group_uuid": group.group_uuid,
                        "decline_probability": float(test_cfg.get("decline_probability", 0)),
                    },
                    status="pending",
                )
            )
        members = await self.matches.list_members(group.id)
        await self._emit(
            ProposalCreated(
                guild_id=guild_id,
                group_uuid=group.group_uuid,
                game=proposal.game,
                activity=proposal.activity,
                queue_type=proposal.queue_type,
                region=proposal.region,
                party_ids=sorted({m.party_id for m in members if m.party_id}),
                member_ids=[m.member_id for m in members],
                confirmation_deadline=deadline,
                auto_confirm=self.config.auto_confirm,
                roster=roster_view(members),
                pending_member_ids=[
                    m
                    for m in pending
                    if is_real_member(
                        m,
                        is_guest=bool(info.get(m, {}).get("guest")),
                        is_test=bool(info.get(m, {}).get("test")),
                    )
                ],
                partial=proposal.partial,
            )
        )
        await self.session.flush()
        return group

    async def _after_create(self, group: MatchGroup) -> None:
        """If nobody has to answer (full pre-made party, auto-confirm), finalize right away."""
        members = await self.matches.list_members(group.id)
        if members and all(m.response == "accepted" for m in members):
            from tianling.services.confirmation_service import ConfirmationService

            cs = ConfirmationService(self.session, self.config)
            await cs.try_finalize(group.group_uuid)
            self._events.extend(cs.pop_events())
