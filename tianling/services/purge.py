"""Purge on member leave: one code path, shared with ``/profile delete_all``."""

from __future__ import annotations

from tianling.services.profile_service import DeletionSummary, ProfileService


class DataPurgeService:
    """Deletes everything stored about a member: profiles, registry entries, queue
    entries and parties (cancelling any pending group they were part of)."""

    def __init__(self, session, guild_id: int | None = None) -> None:
        self.session = session
        self.guild_id = guild_id

    async def purge_member(self, guild_id: int, member_id: int) -> DeletionSummary:
        service = ProfileService(self.session, guild_id)
        summary = await service.delete_all_user_data(member_id)
        self.registry_ops = service.pop_registry_ops()
        return summary
