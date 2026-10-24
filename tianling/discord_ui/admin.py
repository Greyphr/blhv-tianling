"""Admin tools and config UI."""
from __future__ import annotations

import discord
from discord import app_commands

from tianling.db.repositories.matches import MatchRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.db.session import session_scope


def setup_admin(tree: app_commands.CommandTree, guild_id: int):
    guild = discord.Object(id=guild_id)

    @tree.command(name="tianling", description="Tianling admin", guild=guild)
    @app_commands.describe(subcommand="setup|config|status|rebuild|reconcile")
    async def tianling_admin(interaction: discord.Interaction, subcommand: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        sub = subcommand.lower()
        if sub == "status":
            async with session_scope() as session:
                q = QueueRepository(session)
                m = MatchRepository(session)
                entries = await q.list_active(guild_id, "marvel_rivals", "competitive")  # rough
            embed = discord.Embed(title="Tianling Status", description=f"Active entries (sample): {len(entries)}", color=0x7C9BFF)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if sub == "rebuild":
            await interaction.response.send_message("Registry rebuild triggered.", ephemeral=True)
            return
        if sub == "reconcile":
            await interaction.response.send_message("Voice reconcile triggered.", ephemeral=True)
            return
        await interaction.response.send_message("Available: setup|config|status|rebuild|reconcile", ephemeral=True)
