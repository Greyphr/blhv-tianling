"""Discord registry service."""
from __future__ import annotations

import asyncio
from typing import Any

from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.notification_settings import NotificationSettingRepository
from tianling.db.repositories.registry import RegistryRepository
from tianling.db.repositories.users import UserRepository
from tianling.discord_ui.registry_adapter import DiscordAdapter, Embed
from tianling.discord_ui.theme import THEME


class RegistryService:
    def __init__(self, session, adapter: DiscordAdapter):
        self.session = session
        self.adapter = adapter
        self.users = UserRepository(session)
        self.profiles = GameProfileRepository(session)
        self.registry = RegistryRepository(session)
        self.notifs = NotificationSettingRepository(session)

    def _format_profile(self, profile, user) -> Embed:
        fields = []
        mention = f"<@{user.member_id}>" if user else ""
        fields.append(("Player", mention or str(getattr(user, 'display_name') or user.member_id if user else ''), True))
        fields.append(("Game", f"{profile.game} ({profile.current_queue_type})", True))
        if profile.region:
            fields.append(("Region", profile.region, True))
        cur = f"{profile.current_rank_tier or 'Unranked'} {profile.current_division or ''} (self-reported)".strip()
        peak = f"{profile.peak_rank_tier or '-'} {profile.peak_division or ''}".strip() if profile.peak_rank_tier else "-"
        fields.append(("Current Rank", cur, True))
        fields.append(("Peak Rank", peak, True))
        prefs = []
        if profile.preferred_roles:
            prefs.append(f"Pref: {profile.preferred_roles}")
        if profile.acceptable_roles:
            prefs.append(f"Acc: {profile.acceptable_roles}")
        if profile.playstyle:
            prefs.append(f"Style: {profile.playstyle}")
        if prefs:
            fields.append(("Preferences", "\n".join(prefs), False))
        # availability only if opted in
        embed = Embed(
            title=f"{THEME.emoji_registry} {profile.game} • {profile.ingame_name or user.display_name or user.member_id if user else ''}",
            description=None,
            color=THEME.color_primary,
            fields=fields,
            footer=THEME.footer,
        )
        return embed

    async def upsert_profile(self, guild_id: int, profile_id: int, channel_id: int):
        profile = await self.profiles.get_by_id(profile_id)
        if not profile:
            return
        user = await self.users.get_by_id(profile.user_id)
        embed = self._format_profile(profile, user)
        existing = await self.registry.get_by_profile(profile_id)
        channel = await self.adapter.get_channel(channel_id)
        if existing:
            await self._with_retry(self.adapter.edit_embed, channel, existing.message_id, embed)
            return
        msg_id = await self._with_retry(self.adapter.send_embed, channel, embed)
        from tianling.db.models import RegistryMessage
        rm = RegistryMessage(guild_id=guild_id, profile_id=profile_id, channel_id=channel_id, message_id=msg_id)
        await self.registry.create(rm)
        await self.session.flush()

    async def delete_profile(self, profile_id: int):
        existing = await self.registry.get_by_profile(profile_id)
        if not existing:
            return
        channel = await self.adapter.get_channel(existing.channel_id)
        try:
            await self._with_retry(self.adapter.delete_message, channel, existing.message_id)
        except Exception:
            pass
        await self.registry.delete_for_profile(profile_id)
        await self.session.flush()

    async def reconcile(self, guild_id: int, channel_id: int):
        profiles = await self.profiles.list_for_guild(guild_id)
        existing = await self.registry.list_for_channel(channel_id)
        existing_map = {e.profile_id: e for e in existing}
        # update existing, create missing
        for p in profiles:
            try:
                await self.upsert_profile(guild_id, p.id, channel_id)
            except Exception:
                continue
        # delete orphaned
        profile_ids = {p.id for p in profiles}
        for e in existing:
            if e.profile_id not in profile_ids:
                channel = await self.adapter.get_channel(channel_id)
                try:
                    await self._with_retry(self.adapter.delete_message, channel, e.message_id)
                except Exception:
                    pass
                await self.registry.delete(e.id)
        await self.session.flush()

    async def _with_retry(self, func, *args, retries: int = 3, delay: float = 0.5):
        for i in range(retries):
            try:
                return await func(*args)
            except Exception:
                if i == retries - 1:
                    raise
                await asyncio.sleep(delay * (2 ** i))
        return None
