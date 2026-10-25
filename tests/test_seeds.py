"""Tests for seed loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tianling.core.seed_loader import SeedLoader


def test_all_games_have_ranks_sorted_by_rank_order_value(tmp_path: Path) -> None:
    loader = SeedLoader()
    games = loader.load_games()
    for g in games.games:
        if g.key == "overwatch_2":
            for qt in ("role_queue", "open_queue"):
                ranks = loader.load_ranks(g.key, qt)
                vals = [r.rank_order_value for r in ranks.ranks]
                assert vals == sorted(vals)
        else:
            ranks = loader.load_ranks(g.key, "default")
            vals = [r.rank_order_value for r in ranks.ranks]
            assert vals == sorted(vals)


def test_deadlock_has_exactly_twelve_tiers() -> None:
    loader = SeedLoader()
    ranks = loader.load_ranks("deadlock", "default")
    tiers = [r.tier for r in ranks.ranks]
    # 12 tiers as specified: Obscurus, Initiate, Seeker, Acolyte, Sentinel, Mystic,
    # Ritualist, Emissary, Oracle, Phantom, Ascendant, Eternus
    assert tiers[0] == "Obscurus"
    assert tiers.count("Initiate") == 6
    assert tiers.count("Seeker") == 6
    assert tiers.count("Acolyte") == 6
    assert tiers.count("Sentinel") == 6
    assert tiers.count("Mystic") == 6
    assert tiers.count("Ritualist") == 6
    assert tiers.count("Emissary") == 6
    assert tiers.count("Oracle") == 6
    assert tiers.count("Phantom") == 6
    assert tiers.count("Ascendant") == 6
    assert tiers.count("Eternus") == 6
    # total distinct tier entries
    distinct = []
    for t in tiers:
        if not distinct or distinct[-1] != t:
            distinct.append(t)
    assert distinct == [
        "Obscurus",
        "Initiate",
        "Seeker",
        "Acolyte",
        "Sentinel",
        "Mystic",
        "Ritualist",
        "Emissary",
        "Oracle",
        "Phantom",
        "Ascendant",
        "Eternus",
    ]


def test_compositions_role_counts_sum_to_group_size_when_roles_exist() -> None:
    loader = SeedLoader()
    comps = loader.load_compositions()
    for c in comps.compositions:
        if c.roles:
            assert sum(r.count for r in c.roles) == c.group_size


def test_bad_files_raise_clear_errors(tmp_path: Path) -> None:
    loader = SeedLoader(tmp_path)
    # bad compositions: role sum != group_size
    bad_comps = tmp_path / "compositions.yaml"
    bad_comps.write_text(
        yaml.safe_dump(
            {
                "compositions": [
                    {
                        "game": "marvel_rivals",
                        "activity": "competitive",
                        "group_size": 6,
                        "roles": [
                            {"key": "vanguard", "name": "Vanguard", "count": 2},
                            {"key": "duelist", "name": "Duelist", "count": 2},
                        ],
                        "role_required": True,
                        "allowed_rank_gap": 1,
                        "party_max_size": 6,
                        "max_party_rank_gap": 1,
                        "partial_fill_allowed": False,
                        "verify_in_game": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        loader.load_compositions()

    # bad ranks: not sorted
    ranks_dir = tmp_path / "ranks"
    ranks_dir.mkdir()
    bad_ranks = ranks_dir / "marvel_rivals.yaml"
    bad_ranks.write_text(
        yaml.safe_dump(
            {
                "game": "marvel_rivals",
                "queue_type": "default",
                "ranks": [
                    {
                        "tier": "Silver",
                        "division": "I",
                        "label": "Silver I",
                        "rank_order_value": 220,
                    },
                    {
                        "tier": "Bronze",
                        "division": "I",
                        "label": "Bronze I",
                        "rank_order_value": 120,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    loader2 = SeedLoader(tmp_path)
    with pytest.raises(ValueError):
        loader2.load_ranks("marvel_rivals", "default")


def test_hero_pool_items_are_plain_strings() -> None:
    """Guards against YAML quirks such as an unquoted 'Soldier: 76' becoming a mapping."""
    from tianling.core.seed_loader import SeedLoader

    pools = SeedLoader().load_roles_and_pool().hero_pool
    for game, pool in pools.items():
        items = pool.get("items", [])
        assert all(isinstance(item, str) for item in items), game
        assert len(items) == len(set(items)), f"duplicate heroes in {game}"
