"""Simple status view for LFG."""
from __future__ import annotations

import discord

from tianling.db.repositories.queue import QueueRepository
from tianling.db.repositories.users import UserRepository
from tianling.db.session import session_scope
from tianling.discord_ui.theme import THEME


class StatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, custom_id="tianling:lfg:status")
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with session_scope() as session:
            urepo = UserRepository(session)
            user = await urepo.get_or_create(interaction.guild_id, interaction.user.id)
            qrepo = QueueRepository(session)
            entries = await qrepo.list_for_user(interaction.guild_id, user.id, active_only=True)
        if not entries:
            embed = discord.Embed(title=f"{THEME.emoji_team} Queue Status", description="Not in queue.", color=THEME.color_warning)
            embed.set_footer(text=THEME.footer)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        e = entries[0]
        embed = discord.Embed(title=f"{THEME.emoji_team} Queue Status", description=f"{e.game} {e.activity}\nParty size: {e.party_size}\nStatus: {e.status}\nRegion: {e.region or '-'}", color=THEME.color_primary)
        embed.set_footer(text=THEME.footer)
        await interaction.response.send_message(embed=embed, ephemeral=True)
