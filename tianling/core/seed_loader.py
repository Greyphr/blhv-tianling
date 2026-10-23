"""Validate and load editable game data seeds from config/ using Pydantic."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


class RankEntry(BaseModel):
    tier: str
    division: str | None = None
    label: str
    rank_order_value: int
    is_unranked: bool = False
    is_leaderboard_status: bool = False
    has_division: bool = True


class RanksFile(BaseModel):
    game: str
    queue_type: str = "default"
    gap_unit: Literal["division", "tier"] = "division"
    ranks: list[RankEntry]

    @model_validator(mode="after")
    def ranks_sorted(self) -> RanksFile:
        values = [r.rank_order_value for r in self.ranks]
        if values != sorted(values):
            raise ValueError("ranks must be sorted strictly by rank_order_value")
        if len(set(values)) != len(values):
            raise ValueError("rank_order_value must be unique")
        return self


class RoleSlot(BaseModel):
    key: str
    name: str
    count: int = Field(ge=1)


class CompositionEntry(BaseModel):
    game: str
    activity: str
    group_size: int = Field(ge=1)
    roles: list[RoleSlot] = Field(default_factory=list)
    role_required: bool = False
    allowed_rank_gap: int = 0
    party_max_size: int = Field(ge=1)
    max_party_rank_gap: int = 0
    partial_fill_allowed: bool = False
    verify_in_game: bool = Field(default=True)

    @model_validator(mode="after")
    def role_counts_sum(self) -> CompositionEntry:
        if self.roles:
            total = sum(r.count for r in self.roles)
            if total != self.group_size:
                raise ValueError(
                    f"role counts must sum to group_size (got {total}, expected {self.group_size})"
                )
        return self


class CompositionsFile(BaseModel):
    compositions: list[CompositionEntry]


class RelaxationStepEntry(BaseModel):
    after_minutes: int = Field(ge=0)
    rank_gap_bonus: int = Field(default=0, ge=0)
    allow_adjacent_regions: bool = False
    allow_cross_region: bool = False


class MatchingConfigFile(BaseModel):
    relaxation: list[RelaxationStepEntry] = Field(default_factory=list)
    region_adjacency: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def relaxation_sorted(self) -> MatchingConfigFile:
        minutes = [step.after_minutes for step in self.relaxation]
        if minutes != sorted(minutes):
            raise ValueError("relaxation steps must be sorted by after_minutes")
        if len(set(minutes)) != len(minutes):
            raise ValueError("relaxation after_minutes values must be unique")
        return self


class GameEntry(BaseModel):
    key: str
    name: str
    activities: list[str]
    queues: list[str]
    regions: list[str]
    platforms: list[str] = Field(default_factory=list)
    role_system: str = "none"


class GamesFile(BaseModel):
    games: list[GameEntry]
    regions: list[str]
    platforms_global: list[str] = Field(default_factory=list)


class RolesAndPool(BaseModel):
    roles: dict[str, list[dict[str, Any]]]
    hero_pool: dict[str, Any]


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class SeedLoader:
    def __init__(self, config_dir: Path | str | None = None) -> None:
        if config_dir is None:
            config_dir = Path(__file__).resolve().parents[2] / "config"
        self.config_dir = Path(config_dir)
        self.ranks_dir = self.config_dir / "ranks"

    def load_games(self) -> GamesFile:
        data = _load_yaml(self.config_dir / "games.yaml")
        try:
            return GamesFile.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"Invalid games.yaml: {e}") from e

    def load_ranks(self, game: str, queue_type: str = "default") -> RanksFile:
        # Map known pools
        if game == "overwatch_2" and queue_type == "role_queue":
            path = self.ranks_dir / "overwatch_2_role_queue.yaml"
        elif game == "overwatch_2" and queue_type == "open_queue":
            path = self.ranks_dir / "overwatch_2_open_queue.yaml"
        elif game == "marvel_rivals":
            path = self.ranks_dir / "marvel_rivals.yaml"
        elif game == "apex_legends":
            path = self.ranks_dir / "apex_legends.yaml"
        elif game == "deadlock":
            path = self.ranks_dir / "deadlock.yaml"
        else:
            path = self.ranks_dir / f"{game}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Missing rank file for {game}/{queue_type}: {path}")
        data = _load_yaml(path)
        try:
            return RanksFile.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"Invalid ranks in {path.name}: {e}") from e

    def load_compositions(self) -> CompositionsFile:
        data = _load_yaml(self.config_dir / "compositions.yaml")
        try:
            return CompositionsFile.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"Invalid compositions.yaml: {e}") from e

    def load_roles_and_pool(self) -> RolesAndPool:
        data = _load_yaml(self.config_dir / "roles_and_pool.yaml")
        try:
            return RolesAndPool.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"Invalid roles_and_pool.yaml: {e}") from e

    def load_matching_config(self) -> MatchingConfigFile:
        path = self.config_dir / "matching.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Missing matching config: {path}")
        data = _load_yaml(path)
        try:
            return MatchingConfigFile.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"Invalid matching.yaml: {e}") from e
