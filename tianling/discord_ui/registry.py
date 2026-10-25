"""Public player registry: one message per profile, edited in place.

``RegistryService`` is the session-bound implementation (used by reconcile).
``RegistrySyncer`` applies the registry operations that ``ProfileService`` collected,
*after* the caller committed, and never holds a transaction open during a Discord call.
"""

from __future__ import annotations

import asyncio
import logging

from tianling.core.seed_loader import SeedLoader
from tianling.db.models import RegistryMessage
from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.notification_settings import NotificationSettingRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.db.repositories.registry import RegistryRepository
from tianling.db.repositories.users import UserRepository
from tianling.discord_ui.registry_adapter import DiscordAdapter, Embed
from tianling.discord_ui.theme import THEME
from tianling.services.profile_service import RegistryOp
from tianling.services.settings import get_channel_id

log = logging.getLogger(__name__)


def _rank_text(tier: str | None, division: str | None) -> str:
    if not tier:
        return "Unranked"
    return f"{tier} {division}".strip() if division else tier


def _game_name(game: str) -> str:
    try:
        for entry in SeedLoader().load_games().games:
            if entry.key == game:
                return entry.name
    except Exception:  # config problems must not break registry rendering
        log.exception("could not load game names")
    return game.replace("_", " ").title()


def format_profile(profile, user, *, availability: str | None = None) -> Embed:
    """Render a profile as a registry embed. ``availability`` is only passed when the
    member has opted in to showing it."""
    mention = f"<@{user.member_id}>" if user else "-"
    name = profile.ingame_name or (user.display_name if user else None) or "Unknown"
    queue = (
        ""
        if profile.current_queue_type in (None, "default")
        else f" ({profile.current_queue_type.replace('_', ' ')})"
    )
    fields: list[tuple[str, str, bool]] = [
        ("Player", mention, True),
        ("In-game name", name, True),
    ]
    if profile.region:
        fields.append(("Region", profile.region, True))
    if profile.platform:
        fields.append(
            (
                "Platform",
                profile.platform.upper() if profile.platform == "pc" else profile.platform.title(),
                True,
            )
        )
    fields.append(
        (
            "Current rank",
            f"{_rank_text(profile.current_rank_tier, profile.current_division)}{queue}"
            " · self-reported",
            True,
        )
    )
    if profile.peak_rank_tier:
        fields.append(
            ("Peak rank", _rank_text(profile.peak_rank_tier, profile.peak_division), True)
        )
    if profile.preferred_roles:
        fields.append(
            ("Preferred role", ", ".join(r.title() for r in profile.preferred_roles), True)
        )
    if profile.acceptable_roles:
        fields.append(("Also plays", ", ".join(r.title() for r in profile.acceptable_roles), True))
    if profile.preferred_heroes:
        heroes = list(profile.preferred_heroes)
        shown = ", ".join(heroes[:10]) + (f" +{len(heroes) - 10} more" if len(heroes) > 10 else "")
        fields.append(("Heroes / Legends", shown, False))
    if profile.playstyle:
        fields.append(("Playstyle", profile.playstyle.title(), True))
    if profile.activity:
        fields.append(("Activity", profile.activity.replace("_", " ").title(), True))
    if availability:
        fields.append(("Availability", availability, True))
    return Embed(
        title=f"{THEME.emoji_registry} {_game_name(profile.game)} • {name}",
        description=None,
        color=THEME.color_primary,
        fields=fields,
        footer=THEME.footer,
    )


class RegistryService:
    """Session-bound registry operations (kept for reconcile and direct use)."""

    def __init__(self, session, adapter: DiscordAdapter) -> None:
        self.session = session
        self.adapter = adapter
        self.users = UserRepository(session)
        self.profiles = GameProfileRepository(session)
        self.registry = RegistryRepository(session)
        self.notifs = NotificationSettingRepository(session)
        self.queue = QueueRepository(session)

    async def _availability(self, profile, user) -> str | None:
        setting = await self.notifs.get_for_user(user.id) if user else None
        if setting is None or not setting.show_availability:
            return None
        active = await self.queue.list_for_user(
            profile.guild_id, profile.user_id, game=profile.game, active_only=True
        )
        return "🟢 Looking for a team" if active else "⚪ Not queued"

    def _format_profile(self, profile, user, availability: str | None = None) -> Embed:
        return format_profile(profile, user, availability=availability)

    async def upsert_profile(self, guild_id: int, profile_id: int, channel_id: int) -> None:
        profile = await self.profiles.get_by_id(profile_id)
        if not profile:
            return
        user = await self.users.get_by_id(profile.user_id)
        embed = self._format_profile(profile, user, await self._availability(profile, user))
        existing = await self.registry.get_by_profile(profile_id)
        channel = await self.adapter.get_channel(channel_id)
        if existing:
            try:
                await self._with_retry(self.adapter.edit_embed, channel, existing.message_id, embed)
                return
            except Exception:
                log.info("registry message for profile %s is gone; reposting", profile_id)
                message_id = await self._with_retry(self.adapter.send_embed, channel, embed)
                existing.message_id = message_id
                existing.channel_id = channel_id
                await self.session.flush()
                return
        message_id = await self._with_retry(self.adapter.send_embed, channel, embed)
        await self.registry.create(
            RegistryMessage(
                guild_id=guild_id,
                profile_id=profile_id,
                channel_id=channel_id,
                message_id=message_id,
            )
        )
        await self.session.flush()

    async def delete_profile(self, profile_id: int) -> None:
        existing = await self.registry.get_by_profile(profile_id)
        if not existing:
            return
        channel = await self.adapter.get_channel(existing.channel_id)
        try:
            await self._with_retry(self.adapter.delete_message, channel, existing.message_id)
        except Exception:
            log.warning("could not delete registry message %s", existing.message_id, exc_info=True)
        await self.registry.delete_for_profile(profile_id)
        await self.session.flush()

    async def reconcile(self, guild_id: int, channel_id: int) -> dict[str, int]:
        """Create missing entries, refresh existing ones, drop entries for deleted profiles."""
        profiles = await self.profiles.list_for_guild(guild_id)
        existing = await self.registry.list_for_channel(channel_id)
        stats = {"synced": 0, "failed": 0, "orphans_removed": 0}
        for p in profiles:
            try:
                await self.upsert_profile(guild_id, p.id, channel_id)
                stats["synced"] += 1
            except Exception:
                stats["failed"] += 1
                log.exception("could not sync registry entry for profile %s", p.id)
        profile_ids = {p.id for p in profiles}
        for entry in existing:
            if entry.profile_id not in profile_ids:
                channel = await self.adapter.get_channel(channel_id)
                try:
                    await self._with_retry(self.adapter.delete_message, channel, entry.message_id)
                except Exception:
                    log.warning("could not delete orphan registry message", exc_info=True)
                await self.registry.delete(entry.id)
                stats["orphans_removed"] += 1
        await self.session.flush()
        return stats

    async def _with_retry(self, func, *args, retries: int = 3, delay: float = 0.5):
        for i in range(retries):
            try:
                return await func(*args)
            except Exception:
                if i == retries - 1:
                    raise
                await asyncio.sleep(delay * (2**i))
        return None


class RegistrySyncer:
    """Applies registry operations after commit, with no DB lock held across Discord calls."""

    def __init__(self, scope, adapter: DiscordAdapter) -> None:
        self.scope = scope
        self.adapter = adapter

    async def channel_id(self, guild_id: int) -> int | None:
        async with self.scope() as session:
            return await get_channel_id(session, guild_id, "player_registry")

    async def apply(self, guild_id: int, ops: list[RegistryOp]) -> None:
        if not ops:
            return
        channel_id = await self.channel_id(guild_id)
        for op in ops:
            try:
                if op.kind == "upsert" and op.profile_id is not None:
                    if channel_id is None:
                        log.warning(
                            "no player_registry channel configured; skipping registry update"
                        )
                        continue
                    await self.upsert(guild_id, op.profile_id, channel_id)
                elif op.kind == "delete_message" and op.channel_id and op.message_id:
                    channel = await self.adapter.get_channel(op.channel_id)
                    await self.adapter.delete_message(channel, op.message_id)
            except Exception:
                log.exception("registry operation %s failed", op)

    async def upsert(self, guild_id: int, profile_id: int, channel_id: int) -> None:
        # 1) read everything needed and release the transaction
        async with self.scope() as session:
            svc = RegistryService(session, self.adapter)
            profile = await svc.profiles.get_by_id(profile_id)
            if profile is None:
                return
            user = await svc.users.get_by_id(profile.user_id)
            embed = svc._format_profile(profile, user, await svc._availability(profile, user))
            existing = await svc.registry.get_by_profile(profile_id)
            existing_ref = (existing.channel_id, existing.message_id) if existing else None
        # 2) Discord
        channel = await self.adapter.get_channel(channel_id)
        message_id: int | None = None
        if existing_ref is not None:
            try:
                old_channel = await self.adapter.get_channel(existing_ref[0])
                await self.adapter.edit_embed(old_channel, existing_ref[1], embed)
                return
            except Exception:
                log.info("registry message for profile %s is gone; reposting", profile_id)
        message_id = await self.adapter.send_embed(channel, embed)
        # 3) remember the (new) message
        async with self.scope() as session:
            repo = RegistryRepository(session)
            row = await repo.get_by_profile(profile_id)
            if row is None:
                await repo.create(
                    RegistryMessage(
                        guild_id=guild_id,
                        profile_id=profile_id,
                        channel_id=channel_id,
                        message_id=message_id,
                    )
                )
            else:
                row.channel_id, row.message_id = channel_id, message_id

    async def reconcile(self, guild_id: int) -> dict[str, int]:
        """Rebuild the whole registry from stored profiles (``/tianling rebuild``)."""
        channel_id = await self.channel_id(guild_id)
        if channel_id is None:
            return {"synced": 0, "failed": 0, "orphans_removed": 0, "no_channel": 1}
        async with self.scope() as session:
            profile_ids = [
                p.id for p in await GameProfileRepository(session).list_for_guild(guild_id)
            ]
            known = {
                r.profile_id: r
                for r in await RegistryRepository(session).list_for_channel(channel_id)
            }
            orphans = [
                (r.id, r.message_id) for pid, r in known.items() if pid not in set(profile_ids)
            ]
        stats = {"synced": 0, "failed": 0, "orphans_removed": 0}
        for pid in profile_ids:
            try:
                await self.upsert(guild_id, pid, channel_id)
                stats["synced"] += 1
            except Exception:
                stats["failed"] += 1
                log.exception("could not sync registry entry for profile %s", pid)
        channel = await self.adapter.get_channel(channel_id)
        for row_id, message_id in orphans:
            try:
                await self.adapter.delete_message(channel, message_id)
            except Exception:
                log.warning(
                    "could not delete orphan registry message %s", message_id, exc_info=True
                )
            async with self.scope() as session:
                await RegistryRepository(session).delete(row_id)
            stats["orphans_removed"] += 1
        return stats
