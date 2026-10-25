"""Test mode: fake players that flow through the real queue and confirmation services.

Test players use ids >= TEST_ID_BASE, are flagged ``is_test`` (never DM'd or
pinged, labelled in rosters) and answer proposals through the *real*
``ConfirmationService`` so every code path is exercised.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import delete, select

from tianling.core.timeutil import utcnow
from tianling.db.models import (
    FailedRoster,
    MatchGroup,
    MatchMember,
    PartyMember,
    QueueEntry,
)
from tianling.db.repositories.jobs import JobRepository
from tianling.services.confirmation_service import ConfirmationService
from tianling.services.events import Event
from tianling.services.queue_service import QueueService
from tianling.services.settings import MatchSettings

TEST_ID_BASE = 900_000_000_000


def is_test_id(member_id: int) -> bool:
    return member_id >= TEST_ID_BASE


@dataclass
class AutoAcceptConfig:
    """How fake players answer proposals."""

    delay_seconds: float = 2.0
    decline_probability: float = 0.0
    seed: int | None = None


class TestMode:
    __test__ = False  # not a pytest class

    def __init__(self, session, guild_id: int, settings: MatchSettings | None = None) -> None:
        self.session = session
        self.guild_id = guild_id
        self.settings = settings or MatchSettings()
        self._queue = QueueService(session, self.settings)
        self.jobs = JobRepository(session)
        self._events: list[Event] = []

    async def add_player(
        self,
        member_id: int,
        game: str,
        activity: str,
        rank_snapshot: dict,
        role_preference: str | None = None,
        region: str | None = None,
        *,
        match_now: bool = False,
    ) -> QueueEntry:
        """Queue one fake player. Ids below TEST_ID_BASE are still flagged as test players."""
        entry = (
            await self._queue.join_queue(
                self.guild_id,
                member_id,
                game,
                activity,
                queue_type=activity,
                region=region,
                role_preference=role_preference,
                rank_snapshot=rank_snapshot,
                is_test=True,
            )
            if match_now
            else await self._queue_without_matching(
                member_id, game, activity, rank_snapshot, role_preference, region
            )
        )
        return entry

    async def _queue_without_matching(
        self, member_id, game, activity, rank_snapshot, role_preference, region
    ) -> QueueEntry:
        from datetime import timedelta

        user = await self._queue.users.get_or_create(
            self.guild_id, member_id, display_name=f"TestPlayer-{member_id}"
        )
        now = utcnow()
        entry = QueueEntry(
            guild_id=self.guild_id,
            user_id=user.id,
            member_id=member_id,
            game=game,
            activity=activity,
            queue_type="default",
            region=region,
            role_preference=role_preference,
            rank_snapshot=rank_snapshot,
            status="queued",
            queued_at=now,
            expires_at=now + timedelta(minutes=self.settings.queue_expiry_minutes),
            party_size=1,
            relaxation_level=0,
            is_test=True,
        )
        return await self._queue.queue.join(entry)

    async def match(self) -> list[Event]:
        """Run matching for everything that is waiting; returns the resulting events."""
        await self._queue.run_all_matching(self.guild_id)
        return self._queue.pop_events()

    async def fill(
        self,
        game: str,
        activity: str,
        count: int,
        rank_snapshot: dict | None = None,
        roles: list[str] | None = None,
        region: str | None = "EMEA",
    ) -> list[Event]:
        """Queue ``count`` fake players (cycling through ``roles``) and run matching."""
        rank_snapshot = rank_snapshot or await self._default_rank(game)
        existing = await self.session.execute(
            select(QueueEntry.member_id).where(QueueEntry.member_id >= TEST_ID_BASE)
        )
        used = set(existing.scalars().all())
        next_id = TEST_ID_BASE
        for i in range(count):
            while next_id in used:
                next_id += 1
            used.add(next_id)
            role = roles[i % len(roles)] if roles else None
            await self.add_player(next_id, game, activity, rank_snapshot, role, region)
        return await self.match()

    async def _default_rank(self, game: str) -> dict:
        defaults = {
            "marvel_rivals": {"tier": "Gold", "division": "II"},
            "overwatch_2": {"tier": "Gold", "division": "3"},
            "apex_legends": {"tier": "Gold", "division": "III"},
            "deadlock": {"tier": "Initiate", "division": "I"},
        }
        return defaults.get(game, {})

    async def fill_queue(self, game: str, activity: str, count: int) -> None:
        await self.fill(game, activity, count)

    async def auto_accept(
        self, group_uuid: str, config: AutoAcceptConfig | None = None
    ) -> list[Event]:
        """Answer for every pending fake player in a group via the real ConfirmationService.

        Declines happen with ``config.decline_probability`` (deterministic with a seed).
        The caller schedules this after ``config.delay_seconds``.
        """
        config = config or AutoAcceptConfig()
        rng = random.Random(config.seed)
        cs = ConfirmationService(self.session, self.settings)
        group = await cs.matches.get_group(group_uuid)
        if group is None or group.status != "proposed":
            return []
        for member in await cs.matches.list_members(group.id):
            if not member.is_test or member.response is not None:
                continue
            if rng.random() < config.decline_probability:
                result = await cs.decline(self.guild_id, group_uuid, member.member_id)
            else:
                result = await cs.accept(self.guild_id, group_uuid, member.member_id)
            if result.status in {"cancelled", "declined", "finalized", "group_closed"}:
                break
        return cs.pop_events()

    async def pending_test_groups(self) -> list[str]:
        res = await self.session.execute(
            select(MatchGroup.group_uuid)
            .join(MatchMember, MatchMember.match_group_id == MatchGroup.id)
            .where(
                MatchGroup.guild_id == self.guild_id,
                MatchGroup.status == "proposed",
                MatchMember.is_test.is_(True),
                MatchMember.response.is_(None),
            )
            .distinct()
        )
        return list(res.scalars().all())

    async def clear_all(self) -> None:
        """Remove every test player and any group/queue row they touched."""
        gid = self.guild_id
        group_ids = list(
            (
                await self.session.execute(
                    select(MatchMember.match_group_id)
                    .where(MatchMember.guild_id == gid, MatchMember.is_test.is_(True))
                    .distinct()
                )
            ).scalars()
        )
        group_uuids = list(
            (
                await self.session.execute(
                    select(MatchGroup.group_uuid).where(MatchGroup.id.in_(group_ids))
                )
            ).scalars()
        )
        for job_type in ("confirmation_deadline", "test_auto_accept"):
            for group_uuid in group_uuids:
                await self.jobs.cancel_matching(job_type, "group_uuid", group_uuid)
        if group_ids:
            await self.session.execute(
                delete(MatchMember).where(MatchMember.match_group_id.in_(group_ids))
            )
            await self.session.execute(delete(MatchGroup).where(MatchGroup.id.in_(group_ids)))
        await self.session.execute(
            delete(PartyMember).where(PartyMember.guild_id == gid, PartyMember.is_test.is_(True))
        )
        await self.session.execute(
            delete(QueueEntry).where(QueueEntry.guild_id == gid, QueueEntry.is_test.is_(True))
        )
        await self.session.execute(delete(FailedRoster).where(FailedRoster.guild_id == gid))
        await self.session.flush()
