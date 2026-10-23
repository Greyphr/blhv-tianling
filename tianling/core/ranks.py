"""Pure rank comparison and validation helpers.

Ranks are only comparable inside one game and ranked pool.  The order of a
pool comes from its editable rank definitions; the unit used for a rank gap
comes from the pool's ``gap_unit`` setting.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from tianling.core.seed_loader import RanksFile

GapUnit = Literal["division", "tier"]


class RankError(ValueError):
    """Base class for rank errors."""


class RankPoolMismatchError(RankError):
    """Raised when ranks from different games or pools are compared."""


class RankValidationError(RankError):
    """Raised when a tier/division pair is not in the configured pool."""


class RankConfigError(RankError):
    """Raised when configured rank data is incomplete or inconsistent."""


def _value(item: Any, *names: str) -> Any:
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    raise RankConfigError(f"rank entry is missing required field: {'/'.join(names)}")


def _optional_value(item: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    return default


def _text(value: Any) -> str:
    return str(value).strip()


def _division_key(division: str | None) -> str | None:
    return None if division is None else _text(division).casefold()


@dataclass(frozen=True)
class Rank:
    """A configured rank in a single game/pool."""

    game: str
    queue_type: str
    tier: str
    division: str | None
    label: str
    rank_order_value: int
    index: int
    tier_index: int
    gap_unit: GapUnit = "division"
    is_unranked: bool = False
    is_leaderboard_status: bool = False
    has_division: bool = False

    @property
    def gap_index(self) -> int:
        """Return the configured index used for gap calculations."""
        return self.tier_index if self.gap_unit == "tier" else self.index

    @property
    def pool(self) -> tuple[str, str]:
        return (self.game, self.queue_type)


class RankPool:
    """An ordered, validated rank pool for one game and queue type."""

    def __init__(self, ranks: Sequence[Rank]) -> None:
        if not ranks:
            raise RankConfigError("rank pool must contain at least one rank")
        self.ranks = tuple(ranks)
        self.game = self.ranks[0].game
        self.queue_type = self.ranks[0].queue_type
        self.gap_unit = self.ranks[0].gap_unit
        self._by_key: dict[tuple[str, str | None], Rank] = {}
        for rank in self.ranks:
            if rank.game != self.game or rank.queue_type != self.queue_type:
                raise RankConfigError("rank pool contains mixed games or queue types")
            if rank.gap_unit != self.gap_unit:
                raise RankConfigError("rank pool contains mixed gap units")
            key = (rank.tier.casefold(), _division_key(rank.division))
            if key in self._by_key:
                raise RankConfigError(f"duplicate rank definition: {rank.tier}/{rank.division!r}")
            self._by_key[key] = rank

    @classmethod
    def from_entries(
        cls,
        entries: Sequence[Any],
        *,
        game: str | None = None,
        queue_type: str | None = None,
        gap_unit: GapUnit = "division",
    ) -> RankPool:
        """Build a pool from seed entries, dicts, or rank-definition models."""
        if not entries:
            raise RankConfigError("cannot build a rank pool from no definitions")

        prepared: list[tuple[int, Any]] = []
        for entry in entries:
            order = int(_value(entry, "rank_order_value"))
            prepared.append((order, entry))
        prepared.sort(key=lambda pair: pair[0])

        ranks: list[Rank] = []
        seen: set[tuple[str, str | None]] = set()
        tier_indexes: dict[str, int] = {}
        for index, (_, entry) in enumerate(prepared):
            entry_game = _text(_value(entry, "game")) if _has(entry, "game") else game
            entry_queue = (
                _text(_value(entry, "queue_type")) if _has(entry, "queue_type") else queue_type
            )
            if entry_game is None or entry_queue is None:
                raise RankConfigError("game and queue_type are required to build a rank pool")
            if game is not None and entry_game != game:
                raise RankConfigError(f"rank entry belongs to {entry_game}, not {game}")
            if queue_type is not None and entry_queue != queue_type:
                raise RankConfigError(f"rank entry belongs to {entry_queue}, not {queue_type}")

            tier = _text(_value(entry, "rank_tier", "tier"))
            division_value = _optional_value(entry, "division")
            division = None if division_value is None else _text(division_value)
            tier_key = tier.casefold()
            tier_index = tier_indexes.setdefault(tier_key, len(tier_indexes))

            key = (tier_key, _division_key(division))
            if key in seen:
                raise RankConfigError(f"duplicate rank definition: {tier}/{division!r}")
            seen.add(key)

            ranks.append(
                Rank(
                    game=entry_game,
                    queue_type=entry_queue,
                    tier=tier,
                    division=division,
                    label=_text(_value(entry, "label")),
                    rank_order_value=int(_value(entry, "rank_order_value")),
                    index=index,
                    tier_index=tier_index,
                    gap_unit=gap_unit,
                    is_unranked=bool(_optional_value(entry, "is_unranked", default=False)),
                    is_leaderboard_status=bool(
                        _optional_value(entry, "is_leaderboard_status", default=False)
                    ),
                    has_division=bool(
                        _optional_value(entry, "has_division", default=division is not None)
                    ),
                )
            )
        return cls(ranks)

    @classmethod
    def from_ranks_file(cls, ranks_file: RanksFile) -> RankPool:
        return cls.from_entries(
            ranks_file.ranks,
            game=ranks_file.game,
            queue_type=ranks_file.queue_type,
            gap_unit=ranks_file.gap_unit,
        )

    @classmethod
    def from_rank_definitions(
        cls, definitions: Sequence[Any], *, gap_unit: GapUnit = "division"
    ) -> RankPool:
        return cls.from_entries(definitions, gap_unit=gap_unit)

    def validate(self, tier: str, division: str | None) -> Rank:
        """Return the configured rank for a tier/division pair."""
        tier_key = _text(tier).casefold()
        key = (tier_key, _division_key(division))
        rank = self._by_key.get(key)
        if rank is not None:
            return rank

        tier_known = any(r.tier.casefold() == tier_key for r in self.ranks)
        if not tier_known:
            raise RankValidationError(
                f"unknown rank tier {tier!r} for {self.game}/{self.queue_type}"
            )
        if division is not None and any(
            r.tier.casefold() == tier_key and not r.has_division for r in self.ranks
        ):
            raise RankValidationError(f"rank {tier!r} has no normal division; omit the division")
        if division is None and any(
            r.tier.casefold() == tier_key and r.has_division for r in self.ranks
        ):
            raise RankValidationError(
                f"rank tier {tier!r} requires a division for {self.game}/{self.queue_type}"
            )
        raise RankValidationError(
            f"invalid division {division!r} for tier {tier!r} in {self.game}/{self.queue_type}"
        )

    def compare(self, a: Rank, b: Rank) -> int:
        """Compare two ranks, returning -1, 0, or 1."""
        self._ensure_compatible(a, b)
        if a.index == b.index:
            return 0
        return -1 if a.index < b.index else 1

    def rank_gap(self, a: Rank, b: Rank) -> int:
        """Return the absolute gap using this pool's configured gap unit."""
        self._ensure_compatible(a, b)
        if a.gap_unit != b.gap_unit:
            raise RankConfigError("cannot compare ranks with different gap units")
        return abs(a.gap_index - b.gap_index)

    def party_range(self, ranks: Sequence[Rank]) -> tuple[Rank, Rank]:
        """Return the lowest and highest rank, not an average."""
        if not ranks:
            raise RankValidationError("party rank range requires at least one member")
        for rank in ranks[1:]:
            self._ensure_compatible(ranks[0], rank)
        ordered = sorted(ranks, key=lambda r: r.index)
        return ordered[0], ordered[-1]

    def _ensure_compatible(self, a: Rank, b: Rank) -> None:
        if a.game != b.game or a.queue_type != b.queue_type:
            raise RankPoolMismatchError(
                "cannot compare ranks across games or ranked pools: "
                f"{a.game}/{a.queue_type} vs {b.game}/{b.queue_type}"
            )


def _has(item: Any, name: str) -> bool:
    return (isinstance(item, dict) and name in item) or hasattr(item, name)


def compare_ranks(a: Rank, b: Rank) -> int:
    """Compare two ranks from the same game and pool."""
    if a.game != b.game or a.queue_type != b.queue_type:
        raise RankPoolMismatchError(
            "cannot compare ranks across games or ranked pools: "
            f"{a.game}/{a.queue_type} vs {b.game}/{b.queue_type}"
        )
    if a.index == b.index:
        return 0
    return -1 if a.index < b.index else 1


def rank_gap(a: Rank, b: Rank) -> int:
    """Return the absolute rank gap in the pool's configured unit."""
    if a.game != b.game or a.queue_type != b.queue_type:
        raise RankPoolMismatchError(
            "cannot compare ranks across games or ranked pools: "
            f"{a.game}/{a.queue_type} vs {b.game}/{b.queue_type}"
        )
    if a.gap_unit != b.gap_unit:
        raise RankConfigError("cannot compare ranks with different gap units")
    return abs(a.gap_index - b.gap_index)


def party_rank_range(members: Sequence[Rank]) -> tuple[Rank, Rank]:
    """Return the lowest and highest rank in a party."""
    if not members:
        raise RankValidationError("party rank range requires at least one member")
    for rank in members[1:]:
        if rank.game != members[0].game or rank.queue_type != members[0].queue_type:
            raise RankPoolMismatchError(
                "cannot calculate a party range across games or ranked pools"
            )
    ordered = sorted(members, key=lambda r: r.index)
    return ordered[0], ordered[-1]


def validate_rank_pair(pool: RankPool, tier: str, division: str | None) -> Rank:
    """Validate a tier/division pair against a configured rank pool."""
    return pool.validate(tier, division)


__all__ = [
    "GapUnit",
    "Rank",
    "RankConfigError",
    "RankError",
    "RankPool",
    "RankPoolMismatchError",
    "RankValidationError",
    "compare_ranks",
    "party_rank_range",
    "rank_gap",
    "validate_rank_pair",
]
