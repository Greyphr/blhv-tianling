"""Match repositories."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update

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
        return res.scalar_one_or_none()

    async def get_by_id(self, group_id: int) -> MatchGroup | None:
        stmt = select(MatchGroup).where(MatchGroup.id == group_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def cancel_for_parties(self, guild_id: int, party_ids: list[str]) -> int:
        if not party_ids:
            return 0
        res = await self.session.execute(
            update(MatchGroup)
            .where(
                MatchGroup.guild_id == guild_id,
                MatchGroup.party_id.in_(party_ids),
                MatchGroup.status.not_in(["cancelled", "completed"]),
            )
            .values(status="cancelled", proposal_status="cancelled")
        )
        return int(res.rowcount or 0)

    async def set_proposal_status(
        self, group_uuid: str, status: str, *, confirmation_deadline: datetime | None = None
    ) -> MatchGroup | None:
        values = {"proposal_status": status}
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

    async def update_roster(self, group_uuid: str, roster: dict) -> MatchGroup | None:
        res = await self.session.execute(
            update(MatchGroup).where(MatchGroup.group_uuid == group_uuid).values(roster=roster)
        )
        if res.rowcount == 0:
            return None
        return await self.get_group(group_uuid)

    async def list_members(self, match_group_id: int) -> list[MatchMember]:
        res = await self.session.execute(
            select(MatchMember).where(MatchMember.match_group_id == match_group_id)
        )
        return list(res.scalars().all())

    async def set_member_response(self, match_group_id: int, member_id: int, response: str) -> MatchMember | None:
        res = await self.session.execute(
            update(MatchMember)
            .where(MatchMember.match_group_id == match_group_id, MatchMember.member_id == member_id)
            .values(response=response, responded_at=datetime.now())
        )
        if res.rowcount == 0:
            return None
        q = select(MatchMember).where(
            MatchMember.match_group_id == match_group_id, MatchMember.member_id == member_id
        )
        r2 = await self.session.execute(q)
        return r2.scalar_one_or_none()

    async def remove_member(self, match_group_id: int, member_id: int) -> int:
        from sqlalchemy import delete

        res = await self.session.execute(
            delete(MatchMember).where(
                MatchMember.match_group_id == match_group_id, MatchMember.member_id == member_id
            )
        )
        return int(res.rowcount or 0)
