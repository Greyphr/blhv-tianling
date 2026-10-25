"""Load seed rank/composition data into DB (idempotent upsert)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from tianling.core.seed_loader import SeedLoader
from tianling.db.repositories.compositions import CompositionRepository
from tianling.db.repositories.ranks import RankRepository


async def seed_guild_data(
    session: AsyncSession, guild_id: int, config_dir: Path | str | None = None
) -> None:
    """Upsert rank and composition seeds for one guild. Safe to run repeatedly."""
    loader = SeedLoader(config_dir)
    games = loader.load_games()
    rank_repo = RankRepository(session)
    for g in games.games:
        for queue_type in loader.rank_pools(g.key):
            ranks = loader.load_ranks(g.key, queue_type)
            await rank_repo.upsert_from_seed(
                guild_id, g.key, queue_type, [r.model_dump() for r in ranks.ranks]
            )

    comps = loader.load_compositions()
    comp_repo = CompositionRepository(session)
    await comp_repo.upsert_from_seed(guild_id, [c.model_dump() for c in comps.compositions])
    await session.flush()
