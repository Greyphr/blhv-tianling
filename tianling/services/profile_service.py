"""Profile CRUD, validation, queue refresh, and cleanup services.

The service is guild-scoped.  Public ``user_id`` arguments are Discord member
IDs, matching the IDs stored on ``users.member_id``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tianling.config import get_settings
from tianling.core.ranks import RankConfigError, RankPool
from tianling.core.seed_loader import GameEntry, SeedLoader
from tianling.db.models import GameProfile, QueueEntry
from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.matches import MatchRepository
from tianling.db.repositories.parties import PartyRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.db.repositories.ranks import RankRepository
from tianling.db.repositories.registry import RegistryRepository
from tianling.db.repositories.users import UserRepository


class ProfileError(ValueError):
    """Base class for profile service errors."""


class ProfileNotFoundError(ProfileError):
    """Raised when a requested profile does not exist."""


class ProfileAlreadyExistsError(ProfileError):
    """Raised when a user already has a profile for a game."""


class ProfileValidationError(ProfileError):
    """Raised when a profile field is not valid for the selected game."""


EligibilityHook = Callable[[GameProfile, QueueEntry], Awaitable[bool] | bool]


@dataclass
class DeletionSummary:
    profiles: int = 0
    registry_entries: int = 0
    queue_entries: int = 0
    parties_cancelled: int = 0


_INPUT_FIELDS = {
    "ingame_name",
    "region",
    "platform",
    "current_rank_tier",
    "current_division",
    "current_queue_type",
    "peak_rank_tier",
    "peak_division",
    "peak_queue_type",
    "preferred_roles",
    "acceptable_roles",
    "preferred_heroes",
    "playstyle",
    "activity",
    "rank_source",
}
_RANK_SOURCES = {"self_reported", "verified"}


def _canonical(value: str, options: list[str], field: str) -> str:
    wanted = value.strip().casefold()
    for option in options:
        if option.casefold() == wanted:
            return option
    raise ProfileValidationError(f"invalid {field} {value!r}; choose one of: {', '.join(options)}")


def _clean_text(value: Any, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileValidationError(f"{field} must be text")
    value = value.strip()
    if not value:
        return None
    if len(value) > max_length:
        raise ProfileValidationError(f"{field} must be {max_length} characters or fewer")
    return value


def _clean_list(value: Any, field: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple, set)):
        raise ProfileValidationError(f"{field} must be a list")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProfileValidationError(f"{field} must contain non-empty text values")
        if item.strip() not in cleaned:
            cleaned.append(item.strip())
    return cleaned


class ProfileService:
    """Guild-scoped profile application service."""

    def __init__(
        self,
        session: AsyncSession,
        guild_id: int,
        *,
        config_dir: str | None = None,
        stale_after_days: int | None = None,
        eligibility_hook: EligibilityHook | None = None,
    ) -> None:
        self.session = session
        self.guild_id = guild_id
        self.loader = SeedLoader(config_dir)
        self._games = {g.key.casefold(): g for g in self.loader.load_games().games}
        self._roles = self.loader.load_roles_and_pool()
        self._compositions = {
            (c.game, c.activity): c for c in self.loader.load_compositions().compositions
        }
        self._users = UserRepository(session)
        self._profiles = GameProfileRepository(session)
        self._ranks = RankRepository(session)
        self._queues = QueueRepository(session)
        self._registry = RegistryRepository(session)
        self._parties = PartyRepository(session)
        self._matches = MatchRepository(session)
        self.eligibility_hook = eligibility_hook
        self.stale_after_days = (
            stale_after_days if stale_after_days is not None else self._configured_stale_days()
        )

    @staticmethod
    def _configured_stale_days() -> int:
        try:
            return get_settings().profile_stale_days
        except Exception:
            return 30

    def _game(self, game: str) -> GameEntry:
        try:
            return self._games[game.strip().casefold()]
        except KeyError as exc:
            supported = ", ".join(g.key for g in self._games.values())
            raise ProfileValidationError(
                f"unsupported game {game!r}; choose one of: {supported}"
            ) from exc

    def _queue_options(self, game: GameEntry) -> list[str]:
        return list(dict.fromkeys(["default", *game.queues]))

    async def _rank_pool(self, game: str, queue_type: str | None) -> tuple[RankPool, str]:
        game_config = self._game(game)
        requested = _canonical(
            (queue_type or "default").strip() or "default",
            self._queue_options(game_config),
            "current_queue_type",
        )
        candidates = list(dict.fromkeys([requested, "default", *game_config.queues]))
        definitions: list[Any] = []
        resolved = requested
        for candidate in candidates:
            definitions = await self._ranks.list_for(self.guild_id, game, candidate)
            if definitions:
                resolved = candidate
                break
        if not definitions:
            raise RankConfigError(
                f"no rank definitions loaded for {game}/{requested}; run seed loading"
            )

        try:
            ranks_file = self.loader.load_ranks(game, resolved)
            gap_unit = ranks_file.gap_unit
        except (FileNotFoundError, ValueError):
            gap_unit = "division"
        return RankPool.from_rank_definitions(definitions, gap_unit=gap_unit), resolved

    async def _validated_state(self, game: GameEntry, values: dict[str, Any]) -> dict[str, Any]:
        current_tier = values.get("current_rank_tier")
        if not isinstance(current_tier, str) or not current_tier.strip():
            raise ProfileValidationError("current_rank_tier is required")

        current_pool, _ = await self._rank_pool(game.key, values.get("current_queue_type"))
        current_rank = current_pool.validate(current_tier, values.get("current_division"))

        peak_tier = values.get("peak_rank_tier")
        if peak_tier is None:
            if values.get("peak_division") is not None:
                raise ProfileValidationError("peak_rank_tier is required when peak_division is set")
            peak_rank = current_rank
            peak_pool = current_pool
        else:
            if not isinstance(peak_tier, str) or not peak_tier.strip():
                raise ProfileValidationError("peak_rank_tier must be text when provided")
            peak_queue = _canonical(
                str(values.get("peak_queue_type") or current_pool.queue_type),
                self._queue_options(game),
                "peak_queue_type",
            )
            peak_pool, _ = await self._rank_pool(game.key, peak_queue)
            peak_rank = peak_pool.validate(peak_tier, values.get("peak_division"))

        region = values.get("region")
        if region is not None:
            region = _canonical(str(region), game.regions, "region")

        platform = values.get("platform")
        if platform is not None:
            platform = _canonical(str(platform), game.platforms, "platform")

        activity = values.get("activity")
        if activity is not None:
            activity_options = list(dict.fromkeys([*game.activities, *game.queues]))
            activity = _canonical(str(activity), activity_options, "activity")

        rank_source = str(values.get("rank_source") or "self_reported").strip()
        if rank_source not in _RANK_SOURCES:
            raise ProfileValidationError(
                f"invalid rank_source {rank_source!r}; choose one of: "
                f"{', '.join(sorted(_RANK_SOURCES))}"
            )

        preferred_roles = self._validated_roles(
            game.key, values.get("preferred_roles"), "preferred_roles"
        )
        acceptable_roles = self._validated_roles(
            game.key, values.get("acceptable_roles"), "acceptable_roles"
        )
        preferred_heroes = self._validated_heroes(game.key, values.get("preferred_heroes"))

        return {
            "ingame_name": _clean_text(values.get("ingame_name"), "ingame_name", 128),
            "region": region,
            "platform": platform,
            "activity": activity,
            "playstyle": _clean_text(values.get("playstyle"), "playstyle", 64),
            "rank_source": rank_source,
            "preferred_roles": preferred_roles,
            "acceptable_roles": acceptable_roles,
            "preferred_heroes": preferred_heroes,
            "current_rank_tier": current_rank.tier,
            "current_division": current_rank.division,
            "current_queue_type": current_pool.queue_type,
            "current_is_unranked": current_rank.is_unranked,
            "current_is_leaderboard_status": current_rank.is_leaderboard_status,
            "current_rank_order_value": current_rank.rank_order_value,
            "peak_rank_tier": peak_rank.tier,
            "peak_division": peak_rank.division,
            "peak_queue_type": peak_pool.queue_type,
            "peak_is_unranked": peak_rank.is_unranked,
            "peak_is_leaderboard_status": peak_rank.is_leaderboard_status,
            "peak_rank_order_value": peak_rank.rank_order_value,
            "needs_update": False,
            "needs_update_reason": None,
        }

    def _validated_roles(self, game: str, roles: Any, field: str) -> list[str] | None:
        cleaned = _clean_list(roles, field)
        if cleaned is None:
            return None
        options = self._roles.roles.get(game, [])
        by_key = {str(role["key"]).casefold(): str(role["key"]) for role in options}
        result: list[str] = []
        for role in cleaned:
            canonical = by_key.get(role.casefold())
            if canonical is None:
                supported = ", ".join(by_key.values())
                raise ProfileValidationError(
                    f"invalid {field} value {role!r}; choose one of: {supported}"
                )
            if canonical not in result:
                result.append(canonical)
        return result

    def _validated_heroes(self, game: str, heroes: Any) -> list[str] | None:
        cleaned = _clean_list(heroes, "preferred_heroes")
        if cleaned is None:
            return None
        pool = self._roles.hero_pool.get(game) or {}
        items = [str(item) for item in pool.get("items", [])]
        by_name = {item.casefold(): item for item in items}
        result: list[str] = []
        for hero in cleaned:
            canonical = by_name.get(hero.casefold())
            if canonical is None:
                raise ProfileValidationError(f"invalid preferred_heroes value {hero!r} for {game}")
            if canonical not in result:
                result.append(canonical)
        return result

    def _profile_values(
        self, profile: GameProfile | None, changes: dict[str, Any]
    ) -> dict[str, Any]:
        unknown = set(changes) - _INPUT_FIELDS
        if unknown:
            raise ProfileValidationError(f"unknown profile field(s): {', '.join(sorted(unknown))}")
        values: dict[str, Any] = {}
        if profile is not None:
            for field in _INPUT_FIELDS:
                values[field] = getattr(profile, field)
        values.update(changes)
        return values

    async def create_profile(self, user_id: int, game: str, **fields: Any) -> GameProfile:
        game_config = self._game(game)
        values = self._profile_values(None, fields)
        state = await self._validated_state(game_config, values)

        user = await self._users.get_or_create(self.guild_id, user_id)
        existing = await self._profiles.get(self.guild_id, user.id, game_config.key)
        if existing is not None:
            raise ProfileAlreadyExistsError(
                f"profile already exists for user {user_id} in {game_config.key}"
            )

        profile = GameProfile(
            guild_id=self.guild_id,
            user_id=user.id,
            game=game_config.key,
            **state,
        )
        await self._profiles.create(profile)
        return profile

    async def update_profile(self, user_id: int, game: str, **changes: Any) -> GameProfile:
        game_config = self._game(game)
        user = await self._users.get(self.guild_id, user_id)
        if user is None:
            raise ProfileNotFoundError(f"user {user_id} has no profiles")
        profile = await self._profiles.get(self.guild_id, user.id, game_config.key)
        if profile is None:
            raise ProfileNotFoundError(f"no {game_config.key} profile for user {user_id}")

        values = self._profile_values(profile, changes)
        state = await self._validated_state(game_config, values)
        for field, value in state.items():
            setattr(profile, field, value)
        await self.session.flush()
        await self._refresh_queued_profile(profile)
        return profile

    async def get_profile(
        self, user_id: int, game: str, *, validate: bool = True
    ) -> GameProfile | None:
        game_config = self._game(game)
        user = await self._users.get(self.guild_id, user_id)
        if user is None:
            return None
        profile = await self._profiles.get(self.guild_id, user.id, game_config.key)
        if profile is not None and validate:
            await self._validate_stored_profile(profile)
        return profile

    async def list_profiles(self, user_id: int, *, validate: bool = True) -> list[GameProfile]:
        user = await self._users.get(self.guild_id, user_id)
        if user is None:
            return []
        profiles = await self._profiles.list_for_user(self.guild_id, user.id)
        if validate:
            for profile in profiles:
                await self._validate_stored_profile(profile)
        return profiles

    async def delete_profile(self, user_id: int, game: str) -> bool:
        game_config = self._game(game)
        user = await self._users.get(self.guild_id, user_id)
        if user is None:
            return False
        profile = await self._profiles.get(self.guild_id, user.id, game_config.key)
        if profile is None:
            return False

        active_entries = await self._queues.list_for_user(
            self.guild_id, user.id, game=game_config.key, active_only=True
        )
        party_ids = list(
            dict.fromkeys(entry.party_id for entry in active_entries if entry.party_id)
        )

        try:
            await self._registry.delete_for_profile(profile.id)
        except Exception:
            pass
        await self._queues.delete_for_user(self.guild_id, user.id, game=game_config.key)
        if party_ids:
            await self._queues.cancel_for_parties(self.guild_id, party_ids)
            await self._matches.cancel_for_parties(self.guild_id, party_ids)
            await self._parties.delete_for_parties(self.guild_id, party_ids)
        return await self._profiles.delete(self.guild_id, user.id, game_config.key)

    async def delete_all_user_data(self, user_id: int) -> DeletionSummary:
        user = await self._users.get(self.guild_id, user_id)
        if user is None:
            return DeletionSummary()

        profiles = await self._profiles.list_for_user(self.guild_id, user.id)
        profile_ids = [profile.id for profile in profiles]
        registry_deleted = 0
        try:
            for pid in profile_ids:
                registry_deleted += await self._registry.delete_for_profile(pid)
        except Exception:
            registry_deleted = 0

        party_ids = list(
            dict.fromkeys(
                [
                    *(await self._parties.party_ids_for_member(self.guild_id, user_id)),
                    *(await self._queues.party_ids_for_leader(self.guild_id, user_id)),
                ]
            )
        )
        queue_deleted = await self._queues.delete_for_user(self.guild_id, user.id)
        parties_cancelled = 0
        if party_ids:
            await self._queues.cancel_for_parties(self.guild_id, party_ids)
            parties_cancelled = await self._matches.cancel_for_parties(self.guild_id, party_ids)
            await self._parties.delete_for_parties(self.guild_id, party_ids)
        else:
            await self._parties.delete_for_member(self.guild_id, user_id)

        for profile in profiles:
            await self._profiles.delete(self.guild_id, user.id, profile.game)
        await self._users.delete(user.id)

        return DeletionSummary(
            profiles=len(profiles),
            registry_entries=registry_deleted,
            queue_entries=queue_deleted,
            parties_cancelled=parties_cancelled,
        )

    async def on_member_leave(self, user_id: int) -> DeletionSummary:
        """Clean up all guild data for a departed member."""
        return await self.delete_all_user_data(user_id)

    async def stale_profiles(
        self, days: int | None = None, *, now: datetime | None = None
    ) -> list[GameProfile]:
        """Return profiles that have not been updated within the configured days."""
        threshold_days = self.stale_after_days if days is None else days
        if threshold_days < 0:
            raise ProfileValidationError("stale profile days cannot be negative")
        reference = now or datetime.now(UTC)
        before = reference - timedelta(days=threshold_days)
        return await self._profiles.list_updated_before(self.guild_id, before)

    async def mark_invalid_profiles(self) -> int:
        """Revalidate every guild profile after rank/config edits."""
        marked = 0
        for profile in await self._profiles.list_for_guild(self.guild_id):
            if not await self._validate_stored_profile(profile):
                marked += 1
        await self.session.flush()
        return marked

    async def _validate_stored_profile(self, profile: GameProfile) -> bool:
        try:
            game = self._game(profile.game)
            values = {
                field: getattr(profile, field) for field in _INPUT_FIELDS if hasattr(profile, field)
            }
            state = await self._validated_state(game, values)
        except (ValueError, FileNotFoundError) as exc:
            profile.needs_update = True
            profile.needs_update_reason = str(exc)[:255]
            return False

        for field, value in state.items():
            setattr(profile, field, value)
        return True

    def _snapshot(self, profile: GameProfile) -> dict[str, Any]:
        return {
            "tier": profile.current_rank_tier,
            "division": profile.current_division,
            "queue_type": profile.current_queue_type,
            "rank_order_value": profile.current_rank_order_value,
            "is_unranked": profile.current_is_unranked,
            "is_leaderboard_status": profile.current_is_leaderboard_status,
        }

    def _basic_queue_eligible(self, profile: GameProfile, entry: QueueEntry) -> bool:
        if profile.needs_update:
            return False
        if entry.queue_type != profile.current_queue_type:
            return False
        if (entry.game, entry.activity) not in self._compositions:
            return False
        return True

    async def _refresh_queued_profile(self, profile: GameProfile) -> None:
        entries = await self._queues.list_for_user(
            self.guild_id, profile.user_id, game=profile.game, active_only=True
        )
        snapshot = self._snapshot(profile)
        for entry in entries:
            entry.rank_snapshot = snapshot
            if not self._basic_queue_eligible(profile, entry):
                entry.status = "cancelled"
                continue
            if self.eligibility_hook is None:
                continue
            allowed = self.eligibility_hook(profile, entry)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                entry.status = "cancelled"
        await self.session.flush()


__all__ = [
    "DeletionSummary",
    "EligibilityHook",
    "ProfileAlreadyExistsError",
    "ProfileError",
    "ProfileNotFoundError",
    "ProfileService",
    "ProfileValidationError",
]
