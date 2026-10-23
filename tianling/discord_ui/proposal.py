"""Matchmaking alerts and proposal views."""
from __future__ import annotations

from datetime import UTC, datetime

import discord

from tianling.db.repositories.matches import MatchRepository
from tianling.db.repositories.parties import PartyRepository
from tianling.db.session import session_scope
from tianling.discord_ui.theme import THEME


class ProposalView(discord.ui.View):
    def __init__(self, group_uuid: str, deadline: datetime | None = None):
        super().__init__(timeout=None)
        self.group_uuid = group_uuid
        self.deadline = deadline

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="tianling:prop:accept", emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.services.confirmation_service import ConfirmationService
        async with session_scope() as session:
            cs = ConfirmationService(session)
            await cs.accept(interaction.guild_id, self.group_uuid, interaction.user.id)
            await session.commit()
            cs.pop_events()
        await interaction.response.send_message("Accepted.", ephemeral=True)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, custom_id="tianling:prop:decline", emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.services.confirmation_service import ConfirmationService
        async with session_scope() as session:
            cs = ConfirmationService(session)
            await cs.decline(interaction.guild_id, self.group_uuid, interaction.user.id)
            await session.commit()
            cs.pop_events()
        await interaction.response.send_message("Declined.", ephemeral=True)
