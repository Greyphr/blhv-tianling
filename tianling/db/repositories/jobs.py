"""Scheduled jobs repository."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update

from tianling.db.models import ScheduledJob
from tianling.db.repositories.base import BaseRepository


class JobRepository(BaseRepository):
    async def enqueue(self, job: ScheduledJob) -> ScheduledJob:
        self.session.add(job)
        await self.session.flush()
        return job

    async def due(self, now: datetime) -> list[ScheduledJob]:
        stmt = (
            select(ScheduledJob)
            .where(ScheduledJob.status == "pending", ScheduledJob.run_at <= now)
            .order_by(ScheduledJob.run_at.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def claim(self, job_id: int) -> bool:
        res = await self.session.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id, ScheduledJob.status == "pending")
            .values(status="running")
        )
        return int(res.rowcount or 0) == 1

    async def complete(self, job_id: int) -> None:
        res = await self.session.execute(
            update(ScheduledJob).where(ScheduledJob.id == job_id).values(status="completed")
        )
        if res.rowcount == 0:
            return

    async def fail(self, job_id: int) -> None:
        res = await self.session.execute(
            update(ScheduledJob).where(ScheduledJob.id == job_id).values(status="failed")
        )
        if res.rowcount == 0:
            return

    async def cancel(self, job_id: int) -> None:
        res = await self.session.execute(
            update(ScheduledJob).where(ScheduledJob.id == job_id).values(status="cancelled")
        )
        if res.rowcount == 0:
            return
