"""Discord entrypoint for Tianling: wires services, dispatcher, scheduler and the UI."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from tianling.config import get_settings
from tianling.db.migrate import ensure_schema
from tianling.db.seed import seed_guild_data
from tianling.db.session import session_scope
from tianling.discord_ui import lifecycle
from tianling.discord_ui.discord_gateway import DiscordGateway
from tianling.discord_ui.dispatcher import Dispatcher
from tianling.discord_ui.notifications import NotificationService
from tianling.discord_ui.registry import RegistrySyncer
from tianling.discord_ui.registry_discord import DiscordRegistryAdapter
from tianling.discord_ui.voice import VoiceManager
from tianling.logging_setup import setup_logging
from tianling.scheduler import Scheduler

log = logging.getLogger(__name__)


class TianlingBot(commands.Bot):
    def __init__(self, *, guild_id: int) -> None:
        intents = discord.Intents.default()
        intents.members = True  # privileged: needed for on_member_remove and presence checks
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.guild_id = guild_id
        self.scope = session_scope
        self.gateway = DiscordGateway(self)
        self.voice = VoiceManager(self.gateway, self.scope)
        self.notifier = NotificationService(self.gateway, self.scope)
        self.dispatcher = Dispatcher(self.gateway, self.scope, self.voice, self.notifier)
        self.registry_syncer = RegistrySyncer(self.scope, DiscordRegistryAdapter(self))
        self.scheduler = Scheduler(self.scope, dispatcher=self.dispatcher, voice=self.voice)
        self._ready_once = False

    async def setup_hook(self) -> None:
        # A bot with a missing/old schema must not start half-working: fail loudly.
        await ensure_schema()
        async with session_scope() as session:
            await seed_guild_data(session, self.guild_id)
        log.info("Database ready and seed data loaded for guild %s", self.guild_id)

        from tianling.discord_ui.commands import setup_commands
        from tianling.discord_ui.flow_common import EXPIRED_ITEMS
        from tianling.discord_ui.lfg import PartyInviteButton, StillLookingButton
        from tianling.discord_ui.panels import FindTeamPanel, ProfileRegistryPanel
        from tianling.discord_ui.proposal import ProposalButton
        from tianling.discord_ui.registry_filters import RegistryFilterView

        # Persistent panels and dynamic items: all revive correctly after a restart.
        self.add_view(ProfileRegistryPanel())
        self.add_view(FindTeamPanel())
        self.add_view(RegistryFilterView())
        self.add_dynamic_items(
            ProposalButton, PartyInviteButton, StillLookingButton, *EXPIRED_ITEMS
        )

        setup_commands(self)
        guild = discord.Object(id=self.guild_id)
        await self.tree.sync(guild=guild)
        log.info("Synced application commands to guild %s", self.guild_id)

    async def on_ready(self) -> None:
        log.info("Tianling ready as %s (guild %s)", self.user, self.guild_id)
        if self._ready_once:
            return  # on_ready fires again after reconnects
        self._ready_once = True
        report = await lifecycle.startup_reconcile(self)
        log.info("Startup reconcile: %s", report)
        await self.scheduler.start()

    async def on_voice_state_update(self, member, before, after) -> None:
        if member.guild.id != self.guild_id or before.channel == after.channel:
            return
        await lifecycle.handle_voice_update(
            self.voice,
            self.guild_id,
            [getattr(before.channel, "id", None), getattr(after.channel, "id", None)],
        )

    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != self.guild_id or member.bot:
            return
        try:
            summary = await lifecycle.handle_member_remove(
                self.scope,
                self.guild_id,
                member.id,
                dispatcher=self.dispatcher,
                registry_syncer=self.registry_syncer,
            )
            log.info("Purged data for departed member %s: %s", member.id, summary)
        except Exception:
            log.exception("could not purge data for departed member %s", member.id)

    async def close(self) -> None:
        await self.scheduler.stop()
        await super().close()


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    bot = TianlingBot(guild_id=settings.guild_id)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
