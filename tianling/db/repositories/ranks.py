"""Rank definition repository."""

from __future__ import annotations

from sqlalchemy import select

from tianling.db.models import RankDefinition
from tianling.db.repositories.base import BaseRepository


class RankRepository(BaseRepository):
    async def upsert_from_seed(
        self, guild_id: int, game: str, queue_type: str, ranks: list[dict]
    ) -> None:
        for r in ranks:
            division = r.get("division")
            conditions = [
                RankDefinition.guild_id == guild_id,
                RankDefinition.game == game,
                RankDefinition.queue_type == queue_type,
                RankDefinition.rank_tier == r["tier"],
            ]
            if division is None:
                conditions.append(RankDefinition.division.is_(None))
            else:
                conditions.append(RankDefinition.division == division)

            res = await self.session.execute(select(RankDefinition).where(*conditions))
            existing = res.scalar_one_or_none()
            if existing is None:
                existing = RankDefinition(
                    guild_id=guild_id,
                    game=game,
                    queue_type=queue_type,
                    rank_tier=r["tier"],
                    division=division,
                )
                self.session.add(existing)
            existing.label = r["label"]
            existing.rank_order_value = r["rank_order_value"]
            existing.is_unranked = bool(r.get("is_unranked", False))
            existing.is_leaderboard_status = bool(r.get("is_leaderboard_status", False))
            existing.has_division = bool(r.get("has_division", division is not None))
        await self.session.flush()

    async def list_for(
        self, guild_id: int, game: str, queue_type: str = "default"
    ) -> list[RankDefinition]:
        stmt = (
            select(RankDefinition)
            .where(
                RankDefinition.guild_id == guild_id,
                RankDefinition.game == game,
                RankDefinition.queue_type == queue_type,
            )
            .order_by(RankDefinition.rank_order_value.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
