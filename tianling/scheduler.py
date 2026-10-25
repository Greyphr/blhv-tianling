"""DB-backed scheduler.

Deadlines and delayed work live in ``scheduled_jobs`` (never in memory only), so a
restart picks up exactly where it left off.  Each tick:

1. runs due jobs, each in its own transaction, then dispatches the resulting
   events to Discord *after* the commit;
2. periodically sweeps for work that is derived from DB state: matching for every
   waiting game/activity, queue expiry and "still looking?" prompts, expired
   party invites.

Failures are logged with a traceback, never swallowed, and one failing job never
stops the others.  A lock prevents one tick overlapping the next.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from tianling.core.timeutil import utcnow
from tianling.db.models import ScheduledJob
from tianling.db.repositories.config import GuildConfigRepository
from tianling.db.repositories.failed_rosters import FailedRosterRepository
from tianling.db.repositories.jobs import JobRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.services.confirmation_service import ConfirmationService
from tianling.services.events import Event, StaleProfiles
from tianling.services.profile_service import ProfileService
from tianling.services.queue_service import QueueService
from tianling.services.settings import load_match_settings

log = logging.getLogger(__name__)

STALE_REMINDER_EVERY = timedelta(days=1)
STALE_REMIND_MEMBER_EVERY = timedelta(days=7)


class Scheduler:
    def __init__(
        self,
        scope: Callable[[], Any],
        *,
        dispatcher: Any | None = None,
        voice: Any | None = None,
        interval_seconds: float = 1.0,
        sweep_interval_seconds: float = 15.0,
    ) -> None:
        self.scope = scope
        self.dispatcher = dispatcher
        self.voice = voice
        self.interval_seconds = interval_seconds
        self.sweep_interval = timedelta(seconds=sweep_interval_seconds)
        self._task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._next_sweep = utcnow()
        self.last_error: str | None = None

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._running:
            return
        async with self.scope() as session:
            recovered = await JobRepository(session).requeue_running()
        if recovered:
            log.warning("re-queued %d job(s) that were running when the bot stopped", recovered)
        self._running = True
        self._task = asyncio.create_task(self._run(), name="tianling-scheduler")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.interval_seconds)

    # ------------------------------------------------------------------ tick

    async def tick(self, *, force_sweep: bool = False) -> int:
        """Run one pass. Returns how many jobs ran. Skips if a tick is already running."""
        if self._lock.locked():
            return 0
        async with self._lock:
            ran = await self._run_due_jobs()
            now = utcnow()
            if force_sweep or now >= self._next_sweep:
                self._next_sweep = now + self.sweep_interval
                await self._sweep()
            return ran

    async def _run_due_jobs(self) -> int:
        async with self.scope() as session:
            due = [
                (j.id, j.job_type, j.guild_id, dict(j.payload or {}))
                for j in await JobRepository(session).due(utcnow())
            ]
        ran = 0
        for job_id, job_type, guild_id, payload in due:
            async with self.scope() as session:
                claimed = await JobRepository(session).claim(job_id)
            if not claimed:
                continue
            try:
                events = await self._handle_job(job_type, guild_id, payload)
                async with self.scope() as session:
                    await JobRepository(session).complete(job_id)
                ran += 1
            except Exception as exc:
                self.last_error = f"{job_type}: {type(exc).__name__}: {exc}"
                log.exception("job %s (%s) failed", job_id, job_type)
                async with self.scope() as session:
                    await JobRepository(session).fail(job_id)
                continue
            await self._dispatch(events)
        return ran

    async def _dispatch(self, events: list[Event]) -> None:
        if events and self.dispatcher is not None:
            await self.dispatcher.dispatch(events)

    # ------------------------------------------------------------------ jobs

    async def _handle_job(
        self, job_type: str, guild_id: int | None, payload: dict[str, Any]
    ) -> list[Event]:
        if job_type == "confirmation_deadline":
            group_uuid = payload.get("group_uuid")
            if not group_uuid or guild_id is None:
                return []
            async with self.scope() as session:
                cs = ConfirmationService(session, await load_match_settings(session, guild_id))
                return await cs.handle_deadline(group_uuid)
        if job_type == "queue_expiry":
            return await self._expire_queues(guild_id)
        if job_type == "matching_tick":
            return await self._matching(guild_id, payload.get("game"), payload.get("activity"))
        if job_type in {"empty_channel_cleanup", "empty_channel"}:
            channel_id = payload.get("channel_id")
            if guild_id is None or not channel_id or self.voice is None:
                return []
            outcome = await self.voice.cleanup_if_empty(guild_id, int(channel_id))
            log.info("empty channel cleanup %s -> %s", channel_id, outcome)
            return []
        if job_type == "test_auto_accept":
            return await self._test_auto_accept(guild_id, payload)
        if job_type == "stale_profile_reminder":
            return await self._stale_profiles(guild_id)
        log.warning("unknown job type %r ignored", job_type)
        return []

    async def _matching(
        self, guild_id: int | None, game: str | None, activity: str | None
    ) -> list[Event]:
        if guild_id is None:
            return []
        async with self.scope() as session:
            qs = await QueueService.create(session, guild_id)
            if game and activity:
                await qs.run_matching(guild_id, game, activity)
            else:
                await qs.run_all_matching(guild_id)
            return qs.pop_events()

    async def _expire_queues(self, guild_id: int | None) -> list[Event]:
        events: list[Event] = []
        async with self.scope() as session:
            guilds = (
                [guild_id]
                if guild_id
                else sorted(
                    {g for g, _, _ in await QueueRepository(session).active_game_activities()}
                )
            )
        for gid in guilds:
            async with self.scope() as session:
                qs = await QueueService.create(session, gid)
                await qs.expire_entries()
                events.extend(qs.pop_events())
        return events

    async def _test_auto_accept(self, guild_id: int | None, payload: dict[str, Any]) -> list[Event]:
        from tianling.services.test_mode import AutoAcceptConfig, TestMode

        if guild_id is None or not payload.get("group_uuid"):
            return []
        async with self.scope() as session:
            tm = TestMode(session, guild_id, await load_match_settings(session, guild_id))
            return await tm.auto_accept(
                payload["group_uuid"],
                AutoAcceptConfig(decline_probability=float(payload.get("decline_probability", 0))),
            )

    async def _stale_profiles(self, guild_id: int | None) -> list[Event]:
        if guild_id is None:
            return []
        now = utcnow()
        events: list[Event] = []
        async with self.scope() as session:
            profiles = await ProfileService(session, guild_id).stale_profiles(now=now)
            cfg = GuildConfigRepository(session)
            reminded = dict(await cfg.get_value(guild_id, "stale_reminded", {}) or {})
            from tianling.db.repositories.users import UserRepository

            users = UserRepository(session)
            by_member: dict[int, list[str]] = defaultdict(list)
            for p in profiles:
                user = await users.get_by_id(p.user_id)
                if user is None:
                    continue
                last = reminded.get(str(user.member_id))
                if last and now - _parse(last) < STALE_REMIND_MEMBER_EVERY:
                    continue
                by_member[user.member_id].append(p.game)
            for member_id in by_member:
                reminded[str(member_id)] = now.isoformat()
            await cfg.set(guild_id, "stale_reminded", reminded)
            # schedule the next check (self-perpetuating, stored in the DB)
            await JobRepository(session).enqueue(
                ScheduledJob(
                    guild_id=guild_id,
                    job_type="stale_profile_reminder",
                    run_at=now + STALE_REMINDER_EVERY,
                    payload={},
                    status="pending",
                )
            )
        if by_member:
            events.append(StaleProfiles(guild_id=guild_id, members=dict(by_member)))
        return events

    async def ensure_recurring_jobs(self, guild_id: int) -> None:
        """Make sure the daily stale-profile job exists (idempotent)."""
        async with self.scope() as session:
            jobs = JobRepository(session)
            existing = [
                j
                for j in await jobs.pending_of_type("stale_profile_reminder")
                if j.guild_id == guild_id
            ]
            if not existing:
                await jobs.enqueue(
                    ScheduledJob(
                        guild_id=guild_id,
                        job_type="stale_profile_reminder",
                        run_at=utcnow() + timedelta(hours=1),
                        payload={},
                        status="pending",
                    )
                )

    # ----------------------------------------------------------------- sweep

    async def _sweep(self) -> None:
        """Periodic, idempotent work derived from DB state."""
        async with self.scope() as session:
            pairs = await QueueRepository(session).active_game_activities()
            await FailedRosterRepository(session).purge_expired(utcnow())
            healed = await QueueRepository(session).release_orphaned_reservations()
            if healed:
                log.warning("released %d queue entries reserved for vanished proposals", healed)
        guilds = sorted({g for g, _, _ in pairs})
        for gid in guilds:
            for name, step in (("matching", self._matching), ("expiry", self._expire_queues)):
                try:
                    events = await step(gid, None, None) if name == "matching" else await step(gid)
                    await self._dispatch(events)
                except Exception as exc:
                    self.last_error = f"sweep {name}: {type(exc).__name__}: {exc}"
                    log.exception("sweep %s failed for guild %s", name, gid)
        try:
            async with self.scope() as session:
                await QueueService(session).expire_party_invites()
        except Exception:
            log.exception("party invite expiry failed")


def _parse(value: str):
    from datetime import datetime

    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=utcnow().tzinfo)
