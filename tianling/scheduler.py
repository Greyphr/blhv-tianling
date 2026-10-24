"""Scheduler implementation (DB-backed)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from tianling.db.repositories.channels import ChannelRepository
from tianling.db.repositories.jobs import JobRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.services.confirmation_service import ConfirmationService
from tianling.services.queue_service import QueueService


class Scheduler:
    def __init__(self, session_factory, interval_seconds: float = 1.0):
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def _run(self):
        while self._running:
            try:
                await self._tick()
            except Exception:
                pass
            await asyncio.sleep(self.interval_seconds)

    async def _tick(self):
        async with self.session_factory() as session:
            jobs_repo = JobRepository(session)
            now = datetime.now(UTC)
            due = await jobs_repo.due(now)
            for job in due:
                claimed = await jobs_repo.claim(job.id)
                if not claimed:
                    continue
                try:
                    if job.job_type == "confirmation_deadline":
                        payload = job.payload or {}
                        g = payload.get("group_uuid")
                        if g:
                            cs = ConfirmationService(session)
                            await cs.handle_deadline(g)
                            cs.pop_events()
                    elif job.job_type == "queue_expiry":
                        qr = QueueRepository(session)
                        # simple: expire old queued entries
                        entries = await qr.list_active(job.guild_id or 0, payload_get(job, "game"), payload_get(job, "activity")) if job.guild_id else []
                        for e in entries:
                            if e.expires_at and e.expires_at <= now and e.status == "queued":
                                e.status = "cancelled"
                    elif job.job_type == "empty_channel":
                        ch = ChannelRepository(session)
                        # no-op without channel members info
                    elif job.job_type == "matching_tick":
                        payload = job.payload or {}
                        game = payload.get("game")
                        activity = payload.get("activity")
                        if job.guild_id and game and activity:
                            qs = QueueService(session)
                            await qs._run_matching(job.guild_id, game, activity)  # type: ignore
                            qs.pop_events()
                    elif job.job_type == "empty_channel_cleanup":
                        payload = job.payload or {}
                        cid = payload.get("channel_id")
                        if job.guild_id and cid:
                            try:
                                pass
                                # no bot in this context; skip real delete
                            except Exception:
                                pass
                    await jobs_repo.complete(job.id)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    try:
                        await jobs_repo.fail(job.id)
                        await session.commit()
                    except Exception:
                        await session.rollback()


def payload_get(job, key):
    p = job.payload or {}
    return p.get(key)
