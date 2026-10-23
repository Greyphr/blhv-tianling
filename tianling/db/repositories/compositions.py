"""Composition repository."""

from __future__ import annotations

from sqlalchemy import select

from tianling.db.models import Composition
from tianling.db.repositories.base import BaseRepository


class CompositionRepository(BaseRepository):
    async def upsert_from_seed(self, guild_id: int, comps: list[dict]) -> None:
        for c in comps:
            stmt = select(Composition).where(
                Composition.guild_id == guild_id,
                Composition.game == c["game"],
                Composition.activity == c["activity"],
            )
            res = await self.session.execute(stmt)
            existing = res.scalar_one_or_none()
            if existing is None:
                existing = Composition(
                    guild_id=guild_id,
                    game=c["game"],
                    activity=c["activity"],
                )
                self.session.add(existing)
            existing.group_size = c["group_size"]
            existing.roles = c.get("roles") or []
            existing.role_required = bool(c.get("role_required", False))
            existing.allowed_rank_gap = int(c.get("allowed_rank_gap", 0))
            existing.party_max_size = int(c["party_max_size"])
            existing.max_party_rank_gap = int(c.get("max_party_rank_gap", 0))
            existing.partial_fill_allowed = bool(c.get("partial_fill_allowed", False))
            existing.verify_in_game = bool(c.get("verify_in_game", True))
        await self.session.flush()

    async def list_for(self, guild_id: int, game: str) -> list[Composition]:
        stmt = select(Composition).where(Composition.guild_id == guild_id, Composition.game == game)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
