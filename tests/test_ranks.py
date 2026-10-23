"""Tests for pure rank comparison, validation, and party ranges."""

from __future__ import annotations

import pytest

from tianling.core.ranks import (
    RankConfigError,
    RankPool,
    RankPoolMismatchError,
    RankValidationError,
    compare_ranks,
    party_rank_range,
    rank_gap,
    validate_rank_pair,
)
from tianling.core.seed_loader import SeedLoader


@pytest.fixture
def loader() -> SeedLoader:
    return SeedLoader()


@pytest.fixture
def marvel(loader: SeedLoader) -> RankPool:
    return RankPool.from_ranks_file(loader.load_ranks("marvel_rivals"))


@pytest.fixture
def ow_role(loader: SeedLoader) -> RankPool:
    return RankPool.from_ranks_file(loader.load_ranks("overwatch_2", "role_queue"))


@pytest.fixture
def ow_open(loader: SeedLoader) -> RankPool:
    return RankPool.from_ranks_file(loader.load_ranks("overwatch_2", "open_queue"))


@pytest.fixture
def apex(loader: SeedLoader) -> RankPool:
    return RankPool.from_ranks_file(loader.load_ranks("apex_legends"))


def test_compare_within_pool(marvel: RankPool) -> None:
    bronze = marvel.validate("Bronze", "III")
    gold = marvel.validate("Gold", "II")
    assert compare_ranks(bronze, gold) == -1
    assert compare_ranks(gold, bronze) == 1
    assert compare_ranks(gold, gold) == 0


def test_cross_game_and_cross_pool_comparison_raises(
    marvel: RankPool, ow_role: RankPool, ow_open: RankPool
) -> None:
    with pytest.raises(RankPoolMismatchError):
        compare_ranks(marvel.validate("Gold", "II"), ow_role.validate("Gold", "2"))
    with pytest.raises(RankPoolMismatchError):
        compare_ranks(ow_role.validate("Gold", "2"), ow_open.validate("Gold", "2"))
    with pytest.raises(RankPoolMismatchError):
        rank_gap(marvel.validate("Gold", "II"), ow_role.validate("Gold", "2"))


def test_rank_gap_division_unit(marvel: RankPool) -> None:
    gold_iii = marvel.validate("Gold", "III")
    gold_ii = marvel.validate("Gold", "II")
    gold_i = marvel.validate("Gold", "I")
    bronze_i = marvel.validate("Bronze", "I")
    silver_iii = marvel.validate("Silver", "III")

    assert rank_gap(gold_iii, gold_ii) == 1
    assert rank_gap(gold_iii, gold_i) == 2
    # Adjacent configured ranks, including a tier boundary, are one step.
    assert rank_gap(bronze_i, silver_iii) == 1


def test_rank_gap_tier_unit(loader: SeedLoader) -> None:
    ranks_file = loader.load_ranks("marvel_rivals").model_copy(update={"gap_unit": "tier"})
    pool = RankPool.from_ranks_file(ranks_file)
    bronze = pool.validate("Bronze", "III")
    gold = pool.validate("Gold", "III")
    gold_i = pool.validate("Gold", "I")
    assert rank_gap(bronze, gold) == 2
    assert rank_gap(gold, gold_i) == 0


def test_party_rank_range_is_min_max_not_average(marvel: RankPool) -> None:
    bronze_i = marvel.validate("Bronze", "I")
    gold_ii = marvel.validate("Gold", "II")
    silver_iii = marvel.validate("Silver", "III")

    lowest, highest = party_rank_range([gold_ii, bronze_i, silver_iii])
    assert lowest.tier == "Bronze"
    assert lowest.division == "I"
    assert highest.tier == "Gold"
    assert highest.division == "II"
    assert party_rank_range([high := gold_ii]) == (high, high)


def test_party_rank_range_rejects_mixed_pools(marvel: RankPool, ow_role: RankPool) -> None:
    with pytest.raises(RankPoolMismatchError):
        party_rank_range([marvel.validate("Gold", "II"), ow_role.validate("Gold", "2")])


def test_validate_rank_pair_rejects_unknown_tier_and_division(
    marvel: RankPool,
) -> None:
    with pytest.raises(RankValidationError, match="unknown rank tier"):
        validate_rank_pair(marvel, "Mythic", "I")
    with pytest.raises(RankValidationError, match="invalid division"):
        validate_rank_pair(marvel, "Gold", "IV")
    with pytest.raises(RankValidationError, match="requires a division"):
        validate_rank_pair(marvel, "Gold", None)


def test_leaderboard_ranks_have_no_normal_division(
    marvel: RankPool, ow_role: RankPool, apex: RankPool
) -> None:
    eternity = validate_rank_pair(marvel, "Eternity", None)
    top500 = validate_rank_pair(ow_role, "Top 500", None)
    predator = validate_rank_pair(apex, "Apex Predator", None)

    assert eternity.is_leaderboard_status and not eternity.has_division
    assert top500.is_leaderboard_status and not top500.has_division
    assert predator.is_leaderboard_status and not predator.has_division

    with pytest.raises(RankValidationError, match="no normal division"):
        validate_rank_pair(marvel, "Eternity", "I")
    with pytest.raises(RankValidationError, match="no normal division"):
        validate_rank_pair(ow_role, "Top 500", "1")
    with pytest.raises(RankValidationError, match="no normal division"):
        validate_rank_pair(apex, "Apex Predator", "IV")


def test_leaderboard_tier_with_missing_division_is_still_valid(
    marvel: RankPool, ow_role: RankPool, apex: RankPool
) -> None:
    assert marvel.validate("Eternity", None).tier == "Eternity"
    assert ow_role.validate("Top 500", None).tier == "Top 500"
    assert apex.validate("Apex Predator", None).tier == "Apex Predator"


def test_pool_rejects_duplicate_definitions() -> None:
    entries = [
        {
            "game": "test",
            "queue_type": "default",
            "tier": "Gold",
            "division": "I",
            "label": "Gold I",
            "rank_order_value": 100,
        },
        {
            "game": "test",
            "queue_type": "default",
            "tier": "Gold",
            "division": "I",
            "label": "Gold I duplicate",
            "rank_order_value": 110,
        },
    ]
    with pytest.raises(RankConfigError, match="duplicate"):
        RankPool.from_entries(entries, game="test", queue_type="default")
