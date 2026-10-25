"""Match repositories."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update

from tianling.core.timeutil import utcnow
from tianling.db.models import MatchGroup, MatchMember
from tianling.db.repositories.base import BaseRepository


class MatchRepository(BaseRepository):
    async def create_group(self, group: MatchGroup) -> MatchGroup:
        self.session.add(group)
        await self.session.flush()
        return group

    async def add_member(self, member: MatchMember) -> MatchMember:
        self.session.add(member)
        await self.session.flush()
        return member

    async def get_group(self, group_uuid: str) -> MatchGroup | None:
        stmt = select(MatchGroup).where(MatchGroup.group_uuid == group_uuid)
        res = await self.session.execute(stmt)
        group = res.scalar_one_or_none()
        if group is not None:
            # Re-read: status-guarded UPDATEs bypass the identity map.
            await self.session.refresh(group)
        return group

    async def get_by_id(self, group_id: int) -> MatchGroup | None:
        stmt = select(MatchGroup).where(MatchGroup.id == group_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_message(self, channel_id: int, message_id: int) -> MatchGroup | None:
        res = await self.session.execute(
            select(MatchGroup).where(
                MatchGroup.channel_id == channel_id, MatchGroup.message_id == message_id
            )
        )
        return res.scalar_one_or_none()

    async def list_by_status(
        self, statuses: tuple[str, ...], guild_id: int | None = None
    ) -> list[MatchGroup]:
        stmt = select(MatchGroup).where(MatchGroup.status.in_(statuses))
        if guild_id is not None:
            stmt = stmt.where(MatchGroup.guild_id == guild_id)
        res = await self.session.execute(stmt.order_by(MatchGroup.id.asc()))
        return list(res.scalars().all())

    async def list_proposed(self, guild_id: int | None = None) -> list[MatchGroup]:
        return await self.list_by_status(("proposed",), guild_id)

    async def transition(
        self,
        group_uuid: str,
        from_status: str,
        to_status: str,
        *,
        reason: str | None = None,
        finalized: bool = False,
    ) -> bool:
        """Status-guarded group transition. True only for the caller that wins the race."""
        values: dict = {"status": to_status, "proposal_status": to_status}
        if reason is not None:
            values["cancel_reason"] = reason
        if finalized:
            values["finalized_at"] = utcnow()
        res = await self.session.execute(
            update(MatchGroup)
            .where(MatchGroup.group_uuid == group_uuid, MatchGroup.status == from_status)
            .values(**values)
        )
        return int(res.rowcount or 0) == 1

    async def cancel_for_parties(self, guild_id: int, party_ids: list[str]) -> int:
        """Cancel unfinished groups that contain any of the given parties.

        A group is matched through its members' ``party_id`` (the current model) and,
        for older rows, through the group's own ``party_id`` column.
        """
        if not party_ids:
            return 0
        via_members = select(MatchMember.match_group_id).where(
            MatchMember.guild_id == guild_id, MatchMember.party_id.in_(party_ids)
        )
        res = await self.session.execute(
            update(MatchGroup)
            .where(
                MatchGroup.guild_id == guild_id,
                MatchGroup.status == "proposed",
                (MatchGroup.id.in_(via_members)) | (MatchGroup.party_id.in_(party_ids)),
            )
            .values(status="cancelled", proposal_status="cancelled", cancel_reason="party_removed")
        )
        return int(res.rowcount or 0)

    async def set_proposal_status(
        self, group_uuid: str, status: str, *, confirmation_deadline: datetime | None = None
    ) -> MatchGroup | None:
        values: dict = {"proposal_status": status}
        if confirmation_deadline is not None:
            values["confirmation_deadline"] = confirmation_deadline
        res = await self.session.execute(
            update(MatchGroup).where(MatchGroup.group_uuid == group_uuid).values(**values)
        )
        if res.rowcount == 0:
            return None
        return await self.get_group(group_uuid)

    async def set_status(self, group_uuid: str, status: str) -> MatchGroup | None:
        res = await self.session.execute(
            update(MatchGroup).where(MatchGroup.group_uuid == group_uuid).values(status=status)
        )
        if res.rowcount == 0:
            return None
        return await self.get_group(group_uuid)

    async def set_message(self, group_uuid: str, channel_id: int, message_id: int) -> None:
        await self.session.execute(
            update(MatchGroup)
            .where(MatchGroup.group_uuid == group_uuid)
            .values(channel_id=channel_id, message_id=message_id)
        )

    async def set_voice_channel(self, group_uuid: str, voice_channel_id: int | None) -> None:
        await self.session.execute(
            update(MatchGroup)
            .where(MatchGroup.group_uuid == group_uuid)
            .values(voice_channel_id=voice_channel_id)
        )

    async def update_roster(self, group_uuid: str, roster: dict) -> MatchGroup | None:
        res = await self.session.execute(
            update(MatchGroup).where(MatchGroup.group_uuid == group_uuid).values(roster=roster)
        )
        if res.rowcount == 0:
            return None
        return await self.get_group(group_uuid)

    async def list_members(self, match_group_id: int) -> list[MatchMember]:
        res = await self.session.execute(
            select(MatchMember)
            .where(MatchMember.match_group_id == match_group_id)
            .order_by(MatchMember.id.asc())
        )
        members = list(res.scalars().all())
        for member in members:
            await self.session.refresh(member)
        return members

    async def get_member(self, match_group_id: int, member_id: int) -> MatchMember | None:
        res = await self.session.execute(
            select(MatchMember).where(
                MatchMember.match_group_id == match_group_id, MatchMember.member_id == member_id
            )
        )
        member = res.scalar_one_or_none()
        if member is not None:
            await self.session.refresh(member)
        return member

    async def respond_if_pending(self, match_group_id: int, member_id: int, response: str) -> bool:
        """Record a response only if the member has not answered yet (idempotent)."""
        res = await self.session.execute(
            update(MatchMember)
            .where(
                MatchMember.match_group_id == match_group_id,
                MatchMember.member_id == member_id,
                MatchMember.response.is_(None),
            )
            .values(response=response, responded_at=utcnow())
        )
        return int(res.rowcount or 0) == 1

    async def set_member_response(
        self, match_group_id: int, member_id: int, response: str
    ) -> MatchMember | None:
        res = await self.session.execute(
            update(MatchMember)
            .where(MatchMember.match_group_id == match_group_id, MatchMember.member_id == member_id)
            .values(response=response, responded_at=utcnow())
        )
        if res.rowcount == 0:
            return None
        return await self.get_member(match_group_id, member_id)

    async def remove_member(self, match_group_id: int, member_id: int) -> int:
        res = await self.session.execute(
            delete(MatchMember).where(
                MatchMember.match_group_id == match_group_id, MatchMember.member_id == member_id
            )
        )
        return int(res.rowcount or 0)

    async def groups_containing(
        self,
        guild_id: int,
        member_ids: list[int],
        *,
        statuses: tuple[str, ...],
        game: str | None = None,
        activity: str | None = None,
        exclude_group_id: int | None = None,
        finalized_since: datetime | None = None,
    ) -> list[MatchGroup]:
        """Groups (in the given statuses) that already contain any of the members."""
        if not member_ids:
            return []
        stmt = (
            select(MatchGroup)
            .join(MatchMember, MatchMember.match_group_id == MatchGroup.id)
            .where(
                MatchGroup.guild_id == guild_id,
                MatchGroup.status.in_(statuses),
                MatchMember.member_id.in_(member_ids),
            )
            .distinct()
        )
        if game is not None:
            stmt = stmt.where(MatchGroup.game == game)
        if activity is not None:
            stmt = stmt.where(MatchGroup.activity == activity)
        if exclude_group_id is not None:
            stmt = stmt.where(MatchGroup.id != exclude_group_id)
        if finalized_since is not None:
            stmt = stmt.where(
                (MatchGroup.status != "finalized") | (MatchGroup.finalized_at >= finalized_since)
            )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def group_ids_for_member(
        self, guild_id: int, member_id: int, statuses: tuple[str, ...]
    ) -> list[MatchGroup]:
        return await self.groups_containing(guild_id, [member_id], statuses=statuses)

    async def conflicting_member_ids(
        self,
        guild_id: int,
        member_ids: list[int],
        *,
        statuses: tuple[str, ...],
        game: str | None = None,
        activity: str | None = None,
        exclude_group_id: int | None = None,
        finalized_since: datetime | None = None,
    ) -> set[int]:
        """Which of the given members already sit in another group with these statuses."""
        if not member_ids:
            return set()
        stmt = (
            select(MatchMember.member_id)
            .join(MatchGroup, MatchMember.match_group_id == MatchGroup.id)
            .where(
                MatchGroup.guild_id == guild_id,
                MatchGroup.status.in_(statuses),
                MatchMember.member_id.in_(member_ids),
                MatchMember.response.is_distinct_from("declined"),
                MatchMember.response.is_distinct_from("timeout"),
                MatchMember.response.is_distinct_from("released"),
            )
        )
        if game is not None:
            stmt = stmt.where(MatchGroup.game == game)
        if activity is not None:
            stmt = stmt.where(MatchGroup.activity == activity)
        if exclude_group_id is not None:
            stmt = stmt.where(MatchGroup.id != exclude_group_id)
        if finalized_since is not None:
            stmt = stmt.where(
                (MatchGroup.status != "finalized") | (MatchGroup.finalized_at >= finalized_since)
            )
        res = await self.session.execute(stmt)
        return {int(mid) for mid in res.scalars().all()}

    async def release_finalized_membership(self, guild_id: int, member_ids: list[int]) -> int:
        """A player who queues again has left their previous finalized group."""
        if not member_ids:
            return 0
        finalized = select(MatchGroup.id).where(
            MatchGroup.guild_id == guild_id, MatchGroup.status == "finalized"
        )
        res = await self.session.execute(
            update(MatchMember)
            .where(
                MatchMember.guild_id == guild_id,
                MatchMember.member_id.in_(member_ids),
                MatchMember.match_group_id.in_(finalized),
                MatchMember.response == "accepted",
            )
            .values(response="released")
        )
        return int(res.rowcount or 0)
