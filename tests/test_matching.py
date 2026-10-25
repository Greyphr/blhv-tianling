"""Tests for the pure deterministic matchmaking engine."""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from tianling.core.matching import (
    MatchEntry,
    MatchingConfig,
    MatchingConfigError,
    MatchingValidationError,
    MatchMember,
    RelaxationStep,
    find_matches,
)
from tianling.core.ranks import Rank, RankPool
from tianling.core.seed_loader import CompositionEntry, SeedLoader

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def loader() -> SeedLoader:
    return SeedLoader()


@pytest.fixture
def compositions(loader: SeedLoader) -> dict[tuple[str, str], CompositionEntry]:
    return {(item.game, item.activity): item for item in loader.load_compositions().compositions}


def _member(
    member_id: int,
    rank: Rank | None = None,
    *,
    preferred: tuple[str, ...] = (),
    acceptable: tuple[str, ...] = (),
    declared: str | None = None,
    heroes: tuple[str, ...] = (),
) -> MatchMember:
    return MatchMember(
        member_id=member_id,
        rank=rank,
        preferred_roles=preferred,
        acceptable_roles=acceptable,
        declared_role=declared,
        preferred_heroes=heroes,
    )


def _entry(
    entry_id: str,
    game: str,
    activity: str,
    members: tuple[MatchMember, ...],
    *,
    region: str | None = "NA",
    queue_type: str = "default",
    age_minutes: float = 0,
    **kwargs: object,
) -> MatchEntry:
    return MatchEntry(
        entry_id=entry_id,
        game=game,
        activity=activity,
        members=members,
        region=region,
        queue_type=queue_type,
        queued_at=NOW - timedelta(minutes=age_minutes),
        **kwargs,
    )


def _role_members(rank: Rank, roles: list[str], *, start: int = 1) -> tuple[MatchMember, ...]:
    return tuple(
        _member(start + index, rank, preferred=(role,), acceptable=(role,))
        for index, role in enumerate(roles)
    )


def _solos(
    game: str,
    activity: str,
    members: tuple[MatchMember, ...],
    *,
    prefix: str = "e",
    region: str = "NA",
    age_minutes: float = 0,
) -> list[MatchEntry]:
    return [
        _entry(
            f"{prefix}{index}",
            game,
            activity,
            (member,),
            region=region,
            age_minutes=age_minutes,
        )
        for index, member in enumerate(members, start=1)
    ]


def test_complete_role_roster_assigns_exact_counts(
    loader: SeedLoader, compositions: dict[tuple[str, str], CompositionEntry]
) -> None:
    pool = RankPool.from_ranks_file(loader.load_ranks("marvel_rivals"))
    rank = pool.validate("Gold", "II")
    members = _role_members(
        rank,
        ["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"],
    )
    entries = _solos("marvel_rivals", "competitive", members)

    proposals = find_matches(entries, [compositions[("marvel_rivals", "competitive")]], now=NOW)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.group_size == 6
    assert not proposal.partial
    assert Counter(member.role for member in proposal.members) == Counter(
        {"vanguard": 2, "duelist": 2, "strategist": 2}
    )


@pytest.mark.parametrize(
    ("game", "activity", "queue_type", "roles"),
    [
        (
            "marvel_rivals",
            "competitive",
            "default",
            ["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"],
        ),
        (
            "overwatch_2",
            "role_queue",
            "role_queue",
            ["tank", "damage", "damage", "support", "support"],
        ),
        ("apex_legends", "ranked", "default", []),
        ("deadlock", "ranked", "default", []),
    ],
)
def test_all_four_games_make_complete_rosters(
    loader: SeedLoader,
    compositions: dict[tuple[str, str], CompositionEntry],
    game: str,
    activity: str,
    queue_type: str,
    roles: list[str],
) -> None:
    composition = compositions[(game, activity)]
    pool = RankPool.from_ranks_file(loader.load_ranks(game, queue_type))
    rank = pool.ranks[min(2, len(pool.ranks) - 1)]
    members = (
        _role_members(rank, roles)
        if roles
        else tuple(_member(index, rank) for index in range(1, composition.group_size + 1))
    )
    entries = _solos(game, activity, members)

    proposals = find_matches(entries, [composition], now=NOW)

    assert len(proposals) == 1
    assert proposals[0].group_size == composition.group_size
    assert len({member.member_id for member in proposals[0].members}) == composition.group_size


def test_flexible_member_fills_scarce_role(
    loader: SeedLoader, compositions: dict[tuple[str, str], CompositionEntry]
) -> None:
    pool = RankPool.from_ranks_file(loader.load_ranks("marvel_rivals"))
    rank = pool.validate("Gold", "II")
    members = (
        *_role_members(rank, ["vanguard", "vanguard", "duelist", "duelist", "strategist"]),
        _member(6, rank, preferred=("duelist",), acceptable=("strategist",)),
    )
    entries = _solos("marvel_rivals", "competitive", members)

    proposals = find_matches(entries, [compositions[("marvel_rivals", "competitive")]], now=NOW)

    assert len(proposals) == 1
    assigned = {member.member_id: member.role for member in proposals[0].members}
    assert assigned[6] == "strategist"
    assert Counter(assigned.values()) == Counter({"vanguard": 2, "duelist": 2, "strategist": 2})


def test_overfilled_party_roles_are_rejected(
    loader: SeedLoader, compositions: dict[tuple[str, str], CompositionEntry]
) -> None:
    pool = RankPool.from_ranks_file(loader.load_ranks("marvel_rivals"))
    rank = pool.validate("Gold", "II")
    party = _entry(
        "party",
        "marvel_rivals",
        "competitive",
        tuple(_member(index, rank, declared="vanguard") for index in range(1, 7)),
    )

    assert find_matches([party], [compositions[("marvel_rivals", "competitive")]], now=NOW) == []


def test_party_rank_spread_uses_lowest_to_highest(
    loader: SeedLoader, compositions: dict[tuple[str, str], CompositionEntry]
) -> None:
    pool = RankPool.from_ranks_file(loader.load_ranks("apex_legends"))
    low = pool.validate("Bronze", "IV")
    high = pool.validate("Gold", "IV")
    composition = compositions[("apex_legends", "ranked")].model_copy(
        update={"allowed_rank_gap": 100, "max_party_rank_gap": 1}
    )
    party = _entry(
        "party",
        "apex_legends",
        "ranked",
        (_member(1, low), _member(2, high), _member(3, high)),
    )

    assert find_matches([party], [composition], now=NOW) == []


def test_rank_gap_relaxes_with_queue_age(
    loader: SeedLoader, compositions: dict[tuple[str, str], CompositionEntry]
) -> None:
    pool = RankPool.from_ranks_file(loader.load_ranks("apex_legends"))
    low = pool.validate("Bronze", "IV")
    high = pool.validate("Gold", "IV")
    composition = compositions[("apex_legends", "ranked")]
    entries = [
        _entry("a", "apex_legends", "ranked", (_member(1, low),), age_minutes=10),
        _entry("b", "apex_legends", "ranked", (_member(2, low),), age_minutes=0),
        _entry("c", "apex_legends", "ranked", (_member(3, high),), age_minutes=0),
    ]
    relaxed = MatchingConfig(
        relaxation=(RelaxationStep(after_minutes=5, rank_gap_bonus=20),),
    )

    assert find_matches(entries, [composition], now=NOW) == []
    proposals = find_matches(entries, [composition], config=relaxed, now=NOW)

    assert len(proposals) == 1
    assert proposals[0].relaxation_level == 1


def test_region_relaxes_only_to_adjacent_regions(
    loader: SeedLoader, compositions: dict[tuple[str, str], CompositionEntry]
) -> None:
    pool = RankPool.from_ranks_file(loader.load_ranks("apex_legends"))
    rank = pool.validate("Gold", "IV")
    composition = compositions[("apex_legends", "ranked")]
    entries = [
        _entry("a", "apex_legends", "ranked", (_member(1, rank),), region="EMEA"),
        _entry("b", "apex_legends", "ranked", (_member(2, rank),), region="NA"),
        _entry("c", "apex_legends", "ranked", (_member(3, rank),), region="EMEA"),
    ]
    adjacent_only = MatchingConfig(
        relaxation=(RelaxationStep(after_minutes=0, allow_adjacent_regions=True),),
        region_adjacency={"EMEA": ("NA",), "NA": ("EMEA",)},
    )
    cross_region = MatchingConfig(
        relaxation=(RelaxationStep(after_minutes=0, allow_cross_region=True),),
    )
    far_entries = [
        entries[0],
        entries[2],
        _entry(
            "d",
            "apex_legends",
            "ranked",
            (_member(4, rank),),
            region="Asia",
        ),
    ]

    assert find_matches(entries, [composition], now=NOW) == []
    adjacent = find_matches(entries, [composition], config=adjacent_only, now=NOW)
    assert len(adjacent) == 1
    assert adjacent[0].relaxation_level == 1
    assert adjacent[0].region == "mixed"
    assert find_matches(far_entries, [composition], config=adjacent_only, now=NOW) == []
    assert len(find_matches(far_entries, [composition], config=cross_region, now=NOW)) == 1


def test_full_party_is_a_direct_proposal(
    loader: SeedLoader, compositions: dict[tuple[str, str], CompositionEntry]
) -> None:
    pool = RankPool.from_ranks_file(loader.load_ranks("marvel_rivals"))
    rank = pool.validate("Gold", "II")
    members = _role_members(
        rank,
        ["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"],
    )
    party = _entry("party", "marvel_rivals", "competitive", members)

    proposals = find_matches([party], [compositions[("marvel_rivals", "competitive")]], now=NOW)

    assert len(proposals) == 1
    assert proposals[0].direct
    assert proposals[0].entry_ids == ("party",)
    assert proposals[0].group_size == 6


def test_partial_fill_requires_both_composition_and_caller(
    loader: SeedLoader, compositions: dict[tuple[str, str], CompositionEntry]
) -> None:
    pool = RankPool.from_ranks_file(loader.load_ranks("marvel_rivals"))
    rank = pool.validate("Gold", "II")
    entries = _solos(
        "marvel_rivals",
        "casual",
        tuple(_member(index, rank) for index in range(1, 4)),
    )
    composition = compositions[("marvel_rivals", "casual")]

    assert composition.partial_fill_allowed
    assert find_matches(entries, [composition], now=NOW) == []
    proposals = find_matches(entries, [composition], now=NOW, allow_partial=True)

    assert len(proposals) == 1
    assert proposals[0].partial
    assert proposals[0].group_size == 3


def test_either_sibling_is_blocked_and_reported() -> None:
    composition = CompositionEntry(
        game="test",
        activity="queue",
        group_size=2,
        roles=[],
        role_required=False,
        allowed_rank_gap=10,
        party_max_size=2,
        max_party_rank_gap=10,
        partial_fill_allowed=False,
    )
    entries = [
        _entry("e1", "test", "queue", (_member(1),), sibling_entry_ids=("e1b",)),
        _entry("e1b", "test", "queue", (_member(1),), sibling_entry_ids=("e1",)),
        _entry("e2", "test", "queue", (_member(2),)),
    ]

    proposals = find_matches(entries, [composition], now=NOW)

    assert len(proposals) == 1
    assert proposals[0].entry_ids == ("e1", "e2")
    assert proposals[0].sibling_entry_ids == ("e1b",)


def test_failed_roster_hash_is_not_regenerated() -> None:
    composition = CompositionEntry(
        game="test",
        activity="queue",
        group_size=2,
        roles=[],
        role_required=False,
        allowed_rank_gap=10,
        party_max_size=2,
        max_party_rank_gap=10,
        partial_fill_allowed=False,
    )
    entries = [
        _entry("a", "test", "queue", (_member(1),)),
        _entry("b", "test", "queue", (_member(2),)),
    ]
    proposal = find_matches(entries, [composition], now=NOW)[0]

    assert find_matches(entries, [composition], now=NOW, failed_hashes={proposal.roster_hash}) == []


def test_party_larger_than_configured_max_is_rejected() -> None:
    composition = CompositionEntry(
        game="test",
        activity="queue",
        group_size=4,
        roles=[],
        role_required=False,
        allowed_rank_gap=10,
        party_max_size=2,
        max_party_rank_gap=10,
        partial_fill_allowed=False,
    )
    party = _entry("party", "test", "queue", (_member(1), _member(2), _member(3)))

    assert find_matches([party], [composition], now=NOW) == []


def test_oldest_entries_are_preferred_on_equal_rosters() -> None:
    composition = CompositionEntry(
        game="test",
        activity="queue",
        group_size=2,
        roles=[],
        role_required=False,
        allowed_rank_gap=10,
        party_max_size=2,
        max_party_rank_gap=10,
        partial_fill_allowed=False,
    )
    entries = [
        _entry("old1", "test", "queue", (_member(1),), age_minutes=30),
        _entry("old2", "test", "queue", (_member(2),), age_minutes=25),
        _entry("new1", "test", "queue", (_member(3),), age_minutes=1),
        _entry("new2", "test", "queue", (_member(4),), age_minutes=0),
    ]

    proposals = find_matches(entries, [composition], now=NOW)

    assert proposals[0].entry_ids == ("old1", "old2")


def test_duplicate_entry_ids_are_rejected() -> None:
    composition = CompositionEntry(
        game="test",
        activity="queue",
        group_size=2,
        roles=[],
        role_required=False,
        allowed_rank_gap=10,
        party_max_size=2,
        max_party_rank_gap=10,
        partial_fill_allowed=False,
    )
    entries = [
        _entry("same", "test", "queue", (_member(1),)),
        _entry("same", "test", "queue", (_member(2),)),
    ]

    with pytest.raises(MatchingValidationError, match="duplicate entry_id"):
        find_matches(entries, [composition], now=NOW)


def test_required_roles_without_definition_is_a_config_error() -> None:
    composition = CompositionEntry(
        game="test",
        activity="queue",
        group_size=2,
        roles=[],
        role_required=True,
        allowed_rank_gap=10,
        party_max_size=2,
        max_party_rank_gap=10,
        partial_fill_allowed=False,
    )
    entries = [
        _entry("a", "test", "queue", (_member(1),)),
        _entry("b", "test", "queue", (_member(2),)),
    ]

    with pytest.raises(MatchingConfigError, match="requires roles"):
        find_matches(entries, [composition], now=NOW)


def test_matching_config_loads_from_seed_file(loader: SeedLoader) -> None:
    config = MatchingConfig.from_mapping(loader.load_matching_config())

    assert config.relaxation
    assert config.relaxation[0].after_minutes == 10
    assert "emea" in config.region_adjacency
    assert "na" in config.region_adjacency["emea"]


def test_fuzz_proposals_are_disjoint_deterministic_and_valid(
    loader: SeedLoader,
) -> None:
    pool = RankPool.from_ranks_file(loader.load_ranks("apex_legends"))
    ranks = [rank for rank in pool.ranks if rank.has_division][:8]
    composition = CompositionEntry(
        game="test",
        activity="queue",
        group_size=4,
        roles=[],
        role_required=False,
        allowed_rank_gap=100,
        party_max_size=4,
        max_party_rank_gap=100,
        partial_fill_allowed=True,
    )
    rng = random.Random(1337)
    entries: list[MatchEntry] = []
    member_id = 1
    for entry_index in range(14):
        party_size = rng.randint(1, 3)
        members = tuple(
            _member(member_id + offset, rng.choice(ranks)) for offset in range(party_size)
        )
        member_id += party_size
        entries.append(
            _entry(
                f"fuzz-{entry_index:02d}",
                "test",
                "queue",
                members,
                age_minutes=rng.choice([0, 5, 15, 35]),
            )
        )

    first = find_matches(entries, [composition], now=NOW, allow_partial=True)
    second = find_matches(entries, [composition], now=NOW, allow_partial=True)

    assert first == second
    seen_members: set[int] = set()
    seen_entries: set[str] = set()
    for proposal in first:
        assert proposal.group_size <= composition.group_size
        assert proposal.partial or proposal.group_size == composition.group_size
        if proposal.partial:
            assert proposal.group_size >= 2
        proposal_members = set(proposal.member_ids)
        proposal_entries = set(proposal.entry_ids)
        assert len(proposal_members) == proposal.group_size
        assert not proposal_members & seen_members
        assert not proposal_entries & seen_entries
        seen_members.update(proposal_members)
        seen_entries.update(proposal_entries | set(proposal.sibling_entry_ids))
