"""Test helpers for building queues and parties."""

from __future__ import annotations

from tianling.services.queue_service import QueueService

# Marvel Rivals divisions are roman numerals in the rank config.
GOLD_II = {"tier": "Gold", "division": "II"}
GOLD_I = {"tier": "Gold", "division": "I"}
GOLD_III = {"tier": "Gold", "division": "III"}

MARVEL_ROLES = ["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"]


async def join_marvel_solos(
    qs: QueueService,
    ids: list[int],
    roles: list[str] | None = None,
    *,
    guild: int = 1,
    region: str = "EMEA",
) -> None:
    for member_id, role in zip(ids, roles or MARVEL_ROLES, strict=False):
        await qs.join_queue(
            guild,
            member_id,
            "marvel_rivals",
            "competitive",
            region=region,
            role_preference=role,
            rank_snapshot=GOLD_II,
        )
