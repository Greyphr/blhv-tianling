"""Generate a deterministic mock queue and print matchmaking proposals."""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta

from tianling.core.matching import (
    MatchEntry,
    MatchingConfig,
    MatchMember,
    find_matches,
)
from tianling.core.ranks import RankPool
from tianling.core.seed_loader import CompositionEntry, SeedLoader


def _entry(
    index: int,
    composition: CompositionEntry,
    pool: RankPool,
    rng: random.Random,
    now: datetime,
) -> MatchEntry:
    group_size = composition.group_size
    size = rng.randint(1, min(3, group_size))
    roles = [role.key for role in composition.roles]
    rank = pool.ranks[len(pool.ranks) // 2]
    members: list[MatchMember] = []
    for offset in range(size):
        member_id = index * 100 + offset
        preferred = (rng.choice(roles),) if roles else ()
        acceptable = tuple(roles) if roles else ()
        members.append(
            MatchMember(
                member_id=member_id,
                rank=rank,
                preferred_roles=preferred,
                acceptable_roles=acceptable,
                preferred_heroes=(),
            )
        )
    return MatchEntry(
        entry_id=f"sim-{index:03d}",
        game=composition.game,
        activity=composition.activity,
        members=tuple(members),
        region="NA",
        queued_at=now - timedelta(minutes=rng.choice([0, 3, 12, 25, 40])),
    )


def main() -> None:
    loader = SeedLoader()
    games = {game.key: game for game in loader.load_games().games}
    game_key = sys.argv[1] if len(sys.argv) > 1 else "marvel_rivals"
    if game_key not in games:
        raise SystemExit(f"unknown game {game_key!r}; choose from {', '.join(sorted(games))}")
    game = games[game_key]
    compositions = [
        composition
        for composition in loader.load_compositions().compositions
        if composition.game == game_key
    ]
    composition = max(compositions, key=lambda item: item.group_size)
    queue_type = composition.activity
    pool = RankPool.from_ranks_file(loader.load_ranks(game_key, queue_type))
    config = MatchingConfig.from_mapping(loader.load_matching_config())
    rng = random.Random(1337)
    now = datetime.now(UTC)
    entries = [_entry(index, composition, pool, rng, now) for index in range(18)]

    print(f"Queue: {game.name} / {composition.activity} ({len(entries)} entries)")
    for entry in entries:
        print(
            f"  {entry.entry_id}: party={entry.party_size} region={entry.region} "
            f"members={entry.member_ids}"
        )

    proposals = find_matches(
        entries,
        [composition],
        config=config,
        now=now,
        allow_partial=True,
    )
    print(f"\nProposals: {len(proposals)}")
    for proposal in proposals:
        kind = "direct" if proposal.direct else ("partial" if proposal.partial else "complete")
        print(
            f"\n[{kind}] {proposal.game}/{proposal.activity} "
            f"size={proposal.group_size}/{composition.group_size} "
            f"region={proposal.region} relaxation={proposal.relaxation_level} "
            f"hash={proposal.roster_hash}"
        )
        print(f"  entries: {', '.join(proposal.entry_ids)}")
        for member in proposal.members:
            rank = member.rank.label if member.rank else "unranked"
            role = member.role or "flexible"
            print(f"  {member.member_id}: {role} ({rank})")


if __name__ == "__main__":
    main()
