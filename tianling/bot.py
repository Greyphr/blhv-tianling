"""Discord entrypoint for Tianling."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from tianling.config import get_settings
from tianling.db.seed import seed_guild_data
from tianling.db.session import session_scope
from tianling.logging_setup import setup_logging

log = logging.getLogger(__name__)


class TianlingBot(commands.Bot):
    """Minimal bot shell: guild-only command sync and a ready log."""

    def __init__(self, *, guild_id: int) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
        )
        self.guild_id = guild_id

    async def setup_hook(self) -> None:
        try:
            async with session_scope() as session:
                await seed_guild_data(session, self.guild_id)
            log.info("Seed data loaded for guild %s", self.guild_id)
        except Exception:
            log.exception("Could not load seed data; run 'alembic upgrade head' if the DB is empty")

        # Register persistent views
        from tianling.discord_ui.panels import FindTeamPanel, ProfileRegistryPanel
        from tianling.discord_ui.proposal import ProposalView

        self.add_view(ProfileRegistryPanel())
        self.add_view(FindTeamPanel())
        self.add_view(ProposalView("placeholder"))
        # Note: real proposal views rebuilt from DB on demand via NotificationService if needed

        # Setup commands
        try:
            from tianling.discord_ui.commands import setup_commands
            from tianling.discord_ui.lfg_commands import setup_lfg_commands
            from tianling.discord_ui.admin import setup_admin

            setup_commands(self.tree, self.guild_id)
            setup_lfg_commands(self.tree, self.guild_id)
            setup_admin(self.tree, self.guild_id)
        except Exception:
            log.exception("Could not setup commands")

        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Synced application commands to guild %s", self.guild_id)

    async def on_ready(self) -> None:
        log.info("Tianling ready as %s (guild %s)", self.user, self.guild_id)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    bot = TianlingBot(guild_id=settings.guild_id)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
