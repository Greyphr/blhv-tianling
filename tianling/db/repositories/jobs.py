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
            .order_by(ScheduledJob.run_at.asc(), ScheduledJob.id.asc())
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
        await self.session.execute(
            update(ScheduledJob).where(ScheduledJob.id == job_id).values(status="completed")
        )

    async def fail(self, job_id: int) -> None:
        await self.session.execute(
            update(ScheduledJob).where(ScheduledJob.id == job_id).values(status="failed")
        )

    async def cancel(self, job_id: int) -> None:
        await self.session.execute(
            update(ScheduledJob).where(ScheduledJob.id == job_id).values(status="cancelled")
        )

    async def pending_of_type(self, job_type: str) -> list[ScheduledJob]:
        res = await self.session.execute(
            select(ScheduledJob).where(
                ScheduledJob.job_type == job_type, ScheduledJob.status == "pending"
            )
        )
        return list(res.scalars().all())

    async def cancel_matching(self, job_type: str, key: str, value: object) -> int:
        """Cancel pending jobs of a type whose payload[key] == value."""
        cancelled = 0
        for job in await self.pending_of_type(job_type):
            if (job.payload or {}).get(key) == value:
                job.status = "cancelled"
                cancelled += 1
        if cancelled:
            await self.session.flush()
        return cancelled

    async def requeue_running(self) -> int:
        """After a crash, jobs stuck in 'running' were never completed: run them again."""
        res = await self.session.execute(
            update(ScheduledJob).where(ScheduledJob.status == "running").values(status="pending")
        )
        return int(res.rowcount or 0)
