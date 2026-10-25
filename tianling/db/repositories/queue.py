"""Queue repository with atomic, status-guarded transitions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, or_, select, update

from tianling.db.models import MatchGroup, PartyMember, QueueEntry
from tianling.db.repositories.base import BaseRepository

ACTIVE_STATUSES = ("queued", "reserved", "proposed")


class ReservationConflict(ValueError):
    """Raised when a queue entry is no longer available to reserve."""


class QueueRepository(BaseRepository):
    async def join(self, entry: QueueEntry) -> QueueEntry:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get(self, entry_id: int) -> QueueEntry | None:
        res = await self.session.execute(select(QueueEntry).where(QueueEntry.id == entry_id))
        return res.scalar_one_or_none()

    async def get_many(self, entry_ids: list[int]) -> list[QueueEntry]:
        if not entry_ids:
            return []
        res = await self.session.execute(select(QueueEntry).where(QueueEntry.id.in_(entry_ids)))
        return list(res.scalars().all())

    async def leave(self, guild_id: int, member_id: int, game: str, activity: str) -> bool:
        """Cancel the member's *queued* entry for one game/activity."""
        stmt = (
            update(QueueEntry)
            .where(
                QueueEntry.guild_id == guild_id,
                QueueEntry.member_id == member_id,
                QueueEntry.game == game,
                QueueEntry.activity == activity,
                QueueEntry.status == "queued",
            )
            .values(status="cancelled")
        )
        res = await self.session.execute(stmt)
        return int(res.rowcount or 0) > 0

    async def list_active(self, guild_id: int, game: str, activity: str) -> list[QueueEntry]:
        stmt = select(QueueEntry).where(
            QueueEntry.guild_id == guild_id,
            QueueEntry.game == game,
            QueueEntry.activity == activity,
            QueueEntry.status.in_(ACTIVE_STATUSES),
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_queued(self, guild_id: int, game: str, activity: str) -> list[QueueEntry]:
        """Entries eligible for matching: only those still waiting in the queue."""
        stmt = (
            select(QueueEntry)
            .where(
                QueueEntry.guild_id == guild_id,
                QueueEntry.game == game,
                QueueEntry.activity == activity,
                QueueEntry.status == "queued",
            )
            .order_by(QueueEntry.queued_at.asc(), QueueEntry.id.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_by_proposal(self, proposal_id: str) -> list[QueueEntry]:
        res = await self.session.execute(
            select(QueueEntry).where(QueueEntry.proposal_id == proposal_id)
        )
        return list(res.scalars().all())

    async def list_for_user(
        self,
        guild_id: int,
        user_id: int,
        *,
        game: str | None = None,
        active_only: bool = False,
    ) -> list[QueueEntry]:
        conditions = [QueueEntry.guild_id == guild_id, QueueEntry.user_id == user_id]
        if game is not None:
            conditions.append(QueueEntry.game == game)
        if active_only:
            conditions.append(QueueEntry.status.in_(ACTIVE_STATUSES))
        res = await self.session.execute(select(QueueEntry).where(*conditions))
        return list(res.scalars().all())

    async def list_active_for_member(
        self, guild_id: int, member_id: int, *, game: str | None = None
    ) -> list[QueueEntry]:
        """Active entries the member is part of, as leader/solo OR as a party member."""
        party_ids = select(PartyMember.party_id).where(
            PartyMember.guild_id == guild_id, PartyMember.member_id == member_id
        )
        conditions = [
            QueueEntry.guild_id == guild_id,
            QueueEntry.status.in_(ACTIVE_STATUSES),
            or_(QueueEntry.member_id == member_id, QueueEntry.party_id.in_(party_ids)),
        ]
        if game is not None:
            conditions.append(QueueEntry.game == game)
        res = await self.session.execute(
            select(QueueEntry).where(*conditions).order_by(QueueEntry.queued_at.asc())
        )
        return list(res.scalars().all())

    async def party_ids_for_leader(self, guild_id: int, member_id: int) -> list[str]:
        stmt = select(QueueEntry.party_id).where(
            QueueEntry.guild_id == guild_id,
            QueueEntry.leader_member_id == member_id,
            QueueEntry.party_id.is_not(None),
        )
        res = await self.session.execute(stmt)
        return [party_id for party_id in res.scalars().all() if party_id]

    async def delete_for_user(self, guild_id: int, user_id: int, *, game: str | None = None) -> int:
        conditions = [QueueEntry.guild_id == guild_id, QueueEntry.user_id == user_id]
        if game is not None:
            conditions.append(QueueEntry.game == game)
        res = await self.session.execute(delete(QueueEntry).where(*conditions))
        return int(res.rowcount or 0)

    async def cancel_for_parties(self, guild_id: int, party_ids: list[str]) -> int:
        if not party_ids:
            return 0
        res = await self.session.execute(
            update(QueueEntry)
            .where(
                QueueEntry.guild_id == guild_id,
                QueueEntry.party_id.in_(party_ids),
                QueueEntry.status.in_(ACTIVE_STATUSES),
            )
            .values(status="cancelled")
        )
        return int(res.rowcount or 0)

    async def reserve_entries(
        self, guild_id: int, proposal_id: str, entry_ids: list[int]
    ) -> list[QueueEntry]:
        """Reserve entries for a proposal; all-or-nothing within the caller's SAVEPOINT.

        Only ``status``/``proposal_id`` are touched: party fields are never rewritten.
        """
        res = await self.session.execute(
            update(QueueEntry)
            .where(
                QueueEntry.guild_id == guild_id,
                QueueEntry.id.in_(entry_ids),
                QueueEntry.status == "queued",
            )
            .values(status="reserved", proposal_id=proposal_id)
        )
        if int(res.rowcount or 0) != len(set(entry_ids)):
            raise ReservationConflict("could not reserve all entries atomically")
        return await self.get_many(entry_ids)

    async def cancel_siblings(
        self, guild_id: int, either_group: str | None, keep_entry_ids: list[int]
    ) -> list[int]:
        """Cancel the other 'Either' entries once one of them has been reserved."""
        if not either_group:
            return []
        res = await self.session.execute(
            select(QueueEntry.id).where(
                QueueEntry.guild_id == guild_id,
                QueueEntry.either_group == either_group,
                QueueEntry.id.not_in(keep_entry_ids),
                QueueEntry.status.in_(("queued", "reserved")),
            )
        )
        ids = list(res.scalars().all())
        if ids:
            await self.session.execute(
                update(QueueEntry).where(QueueEntry.id.in_(ids)).values(status="cancelled")
            )
        return ids

    async def release(self, entry_ids: list[int]) -> int:
        """Put reserved entries back to queued, keeping their original ``queued_at``."""
        if not entry_ids:
            return 0
        res = await self.session.execute(
            update(QueueEntry)
            .where(QueueEntry.id.in_(entry_ids), QueueEntry.status == "reserved")
            .values(status="queued", proposal_id=None)
        )
        return int(res.rowcount or 0)

    async def set_status(self, entry_ids: list[int], status: str) -> int:
        if not entry_ids:
            return 0
        res = await self.session.execute(
            update(QueueEntry).where(QueueEntry.id.in_(entry_ids)).values(status=status)
        )
        return int(res.rowcount or 0)

    async def list_expired(self, now: datetime) -> list[QueueEntry]:
        res = await self.session.execute(
            select(QueueEntry).where(
                QueueEntry.status == "queued",
                QueueEntry.expires_at.is_not(None),
                QueueEntry.expires_at <= now,
            )
        )
        return list(res.scalars().all())

    async def list_needing_still_looking(self, now: datetime, lead: datetime) -> list[QueueEntry]:
        """Queued entries that will expire before ``lead`` and were not yet prompted."""
        res = await self.session.execute(
            select(QueueEntry).where(
                QueueEntry.status == "queued",
                QueueEntry.still_looking_sent.is_(False),
                QueueEntry.expires_at.is_not(None),
                QueueEntry.expires_at > now,
                QueueEntry.expires_at <= lead,
            )
        )
        return list(res.scalars().all())

    async def active_game_activities(self) -> list[tuple[int, str, str]]:
        """Distinct (guild, game, activity) triples that currently have queued entries."""
        res = await self.session.execute(
            select(QueueEntry.guild_id, QueueEntry.game, QueueEntry.activity)
            .where(QueueEntry.status == "queued")
            .group_by(QueueEntry.guild_id, QueueEntry.game, QueueEntry.activity)
        )
        return [(int(g), str(game), str(act)) for g, game, act in res.all()]

    async def counts_by_game(self, guild_id: int) -> dict[tuple[str, str, str], int]:
        res = await self.session.execute(
            select(QueueEntry.game, QueueEntry.activity, QueueEntry.status, func.count())
            .where(QueueEntry.guild_id == guild_id, QueueEntry.status.in_(ACTIVE_STATUSES))
            .group_by(QueueEntry.game, QueueEntry.activity, QueueEntry.status)
        )
        return {(g, a, s): int(c) for g, a, s, c in res.all()}

    async def release_orphaned_reservations(self, guild_id: int | None = None) -> int:
        """Release entries still ``reserved`` for a proposal that is cancelled or gone.

        Nobody should be stuck in 'reserved' once their group no longer exists. Runs
        after profile deletions / admin cancels and as a periodic self-heal.
        """
        live = select(MatchGroup.proposal_id).where(
            MatchGroup.status.in_(("proposed", "finalized")), MatchGroup.proposal_id.is_not(None)
        )
        stmt = (
            update(QueueEntry)
            .where(
                QueueEntry.status == "reserved",
                QueueEntry.proposal_id.not_in(live) | QueueEntry.proposal_id.is_(None),
            )
            .values(status="queued", proposal_id=None)
        )
        if guild_id is not None:
            stmt = stmt.where(QueueEntry.guild_id == guild_id)
        res = await self.session.execute(stmt)
        return int(res.rowcount or 0)
