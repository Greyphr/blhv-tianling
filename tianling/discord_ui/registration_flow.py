"""Registration wizard logic with no discord.py imports (so it can be unit tested).

Everything the wizard asks is derived from config: games, regions, rank pools and
divisions, roles, heroes, playstyles, platforms and activities.  Divisions are skipped
for tiers whose rank config says ``has_division: false`` (Eternity, One Above All,
Top 500, Apex Predator, Master (Apex), Rookie, Obscurus, ...).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from tianling.core.seed_loader import GameEntry, RolesAndPool

STEPS_ORDER = (
    "name",
    "region",
    "queue",
    "current_rank",
    "peak_rank",
    "roles",
    "heroes",
    "playstyle",
    "platform",
    "activity",
    "review",
)

STEP_TITLES = {
    "name": "In-game name",
    "region": "Region",
    "queue": "Queue type",
    "current_rank": "Current rank",
    "peak_rank": "Peak rank",
    "roles": "Roles",
    "heroes": "Heroes / Legends",
    "playstyle": "Playstyle",
    "platform": "Platform",
    "activity": "Activity preference",
    "review": "Review",
}

PAGE_SIZE = 25  # Discord select menus hold at most 25 options


def hero_label(game_entry: GameEntry) -> str:
    return "Legends" if game_entry.key == "apex_legends" else "Heroes"


def plan_steps(
    game: GameEntry, *, rank_pools: Sequence[str], roles_and_pool: RolesAndPool
) -> list[str]:
    """The ordered steps relevant to this game (and only those)."""
    heroes = (roles_and_pool.hero_pool.get(game.key) or {}).get("items") or []
    steps = ["name", "region"]
    if len(rank_pools) > 1:
        steps.append("queue")
    steps += ["current_rank", "peak_rank"]
    if game.role_system == "role_slots" and roles_and_pool.roles.get(game.key):
        steps.append("roles")
    if heroes:
        steps.append("heroes")
    if roles_and_pool.playstyles.get(game.key):
        steps.append("playstyle")
    if len(game.platforms) > 1:
        steps.append("platform")
    if len(game.activities) > 1:
        steps.append("activity")
    steps.append("review")
    return steps


def activity_options(game: GameEntry) -> list[str]:
    """Activities plus 'either' (e.g. Deadlock Ranked / Standard / Either)."""
    return [*game.activities, "either"] if len(game.activities) > 1 else list(game.activities)


@dataclass
class RankOptions:
    """Tiers and divisions of one rank ladder, from the guild's rank definitions."""

    tiers: list[str] = field(default_factory=list)
    divisions: dict[str, list[str]] = field(default_factory=dict)
    orders: dict[tuple[str, str | None], int] = field(default_factory=dict)
    labels: dict[tuple[str, str | None], str] = field(default_factory=dict)

    @classmethod
    def from_definitions(cls, definitions: Sequence[Any]) -> RankOptions:
        opts = cls()
        for d in sorted(definitions, key=lambda r: r.rank_order_value):
            if d.rank_tier not in opts.divisions:
                opts.tiers.append(d.rank_tier)
                opts.divisions[d.rank_tier] = []
            if d.division is not None:
                opts.divisions[d.rank_tier].append(d.division)
            opts.orders[(d.rank_tier, d.division)] = d.rank_order_value
            opts.labels[(d.rank_tier, d.division)] = d.label
        return opts

    def needs_division(self, tier: str) -> bool:
        return bool(self.divisions.get(tier))

    def order(self, tier: str | None, division: str | None) -> int | None:
        return self.orders.get((tier, division)) if tier else None

    def label(self, tier: str | None, division: str | None) -> str:
        if not tier:
            return "-"
        return self.labels.get((tier, division), f"{tier} {division}".strip())


@dataclass
class RegState:
    guild_id: int
    member_id: int
    game: str
    existing: bool = False
    ingame_name: str | None = None
    region: str | None = None
    queue_type: str = "default"
    cur_tier: str | None = None
    cur_div: str | None = None
    peak_tier: str | None = None
    peak_div: str | None = None
    preferred_role: str | None = None
    acceptable_roles: list[str] = field(default_factory=list)
    heroes: list[str] = field(default_factory=list)
    playstyle: str | None = None
    platform: str | None = None
    activity: str | None = None
    show_availability: bool = False

    def to_fields(self, steps: Sequence[str]) -> dict[str, Any]:
        """Keyword arguments for ``ProfileService.create_profile`` / ``update_profile``."""
        data: dict[str, Any] = {
            "ingame_name": self.ingame_name,
            "region": self.region,
            "current_queue_type": self.queue_type,
            "peak_queue_type": self.queue_type,
            "current_rank_tier": self.cur_tier,
            "current_division": self.cur_div,
            "peak_rank_tier": self.peak_tier,
            "peak_division": self.peak_div,
            "rank_source": "self_reported",
        }
        if "roles" in steps:
            data["preferred_roles"] = [self.preferred_role] if self.preferred_role else None
            others = [r for r in self.acceptable_roles if r != self.preferred_role]
            data["acceptable_roles"] = others or None
        if "heroes" in steps:
            data["preferred_heroes"] = list(self.heroes) or None
        if "playstyle" in steps:
            data["playstyle"] = self.playstyle
        if "platform" in steps:
            data["platform"] = self.platform
        if "activity" in steps:
            data["activity"] = self.activity
        return data

    @classmethod
    def from_profile(
        cls, profile, guild_id: int, member_id: int, show_availability: bool
    ) -> RegState:
        roles = list(profile.preferred_roles or [])
        return cls(
            guild_id=guild_id,
            member_id=member_id,
            game=profile.game,
            existing=True,
            ingame_name=profile.ingame_name,
            region=profile.region,
            queue_type=profile.current_queue_type or "default",
            cur_tier=profile.current_rank_tier,
            cur_div=profile.current_division,
            peak_tier=profile.peak_rank_tier,
            peak_div=profile.peak_division,
            preferred_role=roles[0] if roles else None,
            acceptable_roles=list(profile.acceptable_roles or []),
            heroes=list(profile.preferred_heroes or []),
            playstyle=profile.playstyle,
            platform=profile.platform,
            activity=profile.activity,
            show_availability=show_availability,
        )

    def peak_below_current(self, options: RankOptions) -> bool:
        cur = options.order(self.cur_tier, self.cur_div)
        peak = options.order(self.peak_tier, self.peak_div)
        return cur is not None and peak is not None and peak < cur


def review_lines(
    state: RegState, steps: Sequence[str], options: RankOptions
) -> list[tuple[str, str]]:
    """(label, value) rows for the review screen."""
    rows: list[tuple[str, str]] = [
        ("In-game name", state.ingame_name or "-"),
        ("Region", state.region or "-"),
    ]
    if "queue" in steps:
        rows.append(("Queue", state.queue_type.replace("_", " ").title()))
    rows.append(("Current rank", options.label(state.cur_tier, state.cur_div)))
    rows.append(("Peak rank", options.label(state.peak_tier, state.peak_div)))
    if "roles" in steps:
        rows.append(("Preferred role", (state.preferred_role or "-").title()))
        rows.append(
            (
                "Also OK with",
                ", ".join(r.title() for r in state.acceptable_roles if r != state.preferred_role)
                or "-",
            )
        )
    if "heroes" in steps:
        rows.append(("Heroes / Legends", ", ".join(state.heroes) or "-"))
    if "playstyle" in steps:
        rows.append(("Playstyle", (state.playstyle or "-").title()))
    if "platform" in steps:
        rows.append(
            (
                "Platform",
                (state.platform or "-").upper()
                if state.platform == "pc"
                else (state.platform or "-").title(),
            )
        )
    if "activity" in steps:
        rows.append(("Activity", (state.activity or "-").replace("_", " ").title()))
    rows.append(("Show availability", "Yes" if state.show_availability else "No (default)"))
    return rows


def hero_pages(items: Sequence[str]) -> list[list[str]]:
    """Split a hero/legend pool into select-menu sized pages."""
    return [list(items[i : i + PAGE_SIZE]) for i in range(0, len(items), PAGE_SIZE)] or [[]]
