"""Notification service: opt-in aware DMs and pings with rate limiting.

One shared instance is created in ``setup_hook``.  Test players and guests are
never DM'd or pinged.
"""

from __future__ import annotations

import asyncio
import logging
import time

from tianling.db.repositories.notification_settings import NotificationSettingRepository
from tianling.db.repositories.users import UserRepository
from tianling.discord_ui.gateway import Card, Gateway
from tianling.services.roster import is_real_member

log = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, gateway: Gateway, scope, *, cooldown_seconds: float = 5.0) -> None:
        self.gateway = gateway
        self.scope = scope
        self.cooldown_seconds = cooldown_seconds
        self._last: dict[tuple, float] = {}
        self._lock = asyncio.Lock()

    async def _rate_ok(self, key: tuple) -> bool:
        async with self._lock:
            now = time.monotonic()
            last = self._last.get(key)
            if last is not None and now - last < self.cooldown_seconds:
                return False
            self._last[key] = now
            if len(self._last) > 5000:  # keep the table bounded
                cutoff = now - 3600
                self._last = {k: v for k, v in self._last.items() if v > cutoff}
            return True

    async def opted_in(self, guild_id: int, member_ids: list[int]) -> list[int]:
        """Subset of real members who have not opted out of notifications."""
        real = [m for m in member_ids if is_real_member(m)]
        if not real:
            return []
        allowed: list[int] = []
        async with self.scope() as session:
            users = UserRepository(session)
            settings = NotificationSettingRepository(session)
            for mid in real:
                user = await users.get(guild_id, mid)
                setting = await settings.get_for_user(user.id) if user else None
                if setting is None or setting.opt_in:
                    allowed.append(mid)
        return allowed

    async def mentions(self, guild_id: int, member_ids: list[int]) -> str:
        allowed = await self.opted_in(guild_id, member_ids)
        return " ".join(f"<@{m}>" for m in allowed)

    async def dm(
        self, guild_id: int, member_id: int, card: Card, *, kind: str, dedupe: str = ""
    ) -> bool:
        if not await self.opted_in(guild_id, [member_id]):
            return False
        if not await self._rate_ok((guild_id, member_id, kind, dedupe)):
            return False
        try:
            return await self.gateway.send_dm(member_id, card)
        except Exception:
            log.exception("could not DM member %s", member_id)
            return False
