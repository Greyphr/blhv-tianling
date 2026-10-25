"""Startup and membership lifecycle logic (free of discord.py types so it can be tested)."""

from __future__ import annotations

import logging

from tianling.services.confirmation_service import ConfirmationService
from tianling.services.profile_service import ProfileService
from tianling.services.settings import load_match_settings

log = logging.getLogger(__name__)


async def startup_reconcile(bot) -> dict[str, object]:
    """Run once the bot is ready: rebuild pending proposals, voice state, recurring jobs.

    Every step is isolated so one failure cannot stop the scheduler from starting.
    """
    report: dict[str, object] = {}
    guild_id = bot.guild_id
    steps = (
        ("proposals", lambda: bot.dispatcher.reconcile_proposals(guild_id)),
        ("voice", lambda: bot.voice.reconcile(guild_id)),
        ("recurring_jobs", lambda: bot.scheduler.ensure_recurring_jobs(guild_id)),
    )
    for name, step in steps:
        try:
            report[name] = await step()
        except Exception:
            log.exception("startup reconcile step %s failed", name)
            report[name] = "failed"
    return report


async def handle_member_remove(
    scope, guild_id: int, member_id: int, *, dispatcher=None, registry_syncer=None
):
    """A member left: cancel their pending groups (releasing the others), then purge their data.

    The registry messages are deleted on Discord after the database commit.
    """
    async with scope() as session:
        cs = ConfirmationService(session, await load_match_settings(session, guild_id))
        await cs.member_left(guild_id, member_id)
        events = cs.pop_events()
        svc = ProfileService(session, guild_id)
        summary = await svc.delete_all_user_data(member_id)
        ops = svc.pop_registry_ops()
    if dispatcher is not None:
        await dispatcher.dispatch(events)
    if registry_syncer is not None:
        await registry_syncer.apply(guild_id, ops)
    return summary


async def handle_voice_update(voice, guild_id: int, channel_ids: list[int | None]) -> list[str]:
    """Voice-state change: re-evaluate both the channel left and the channel joined."""
    results: list[str] = []
    for channel_id in dict.fromkeys(c for c in channel_ids if c):
        try:
            results.append(await voice.on_voice_update(guild_id, channel_id))
        except Exception:
            log.exception("voice update for channel %s failed", channel_id)
    return results
