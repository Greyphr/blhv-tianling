"""LFG slash commands."""
from __future__ import annotations

import discord
from discord import app_commands


def setup_lfg_commands(tree: app_commands.CommandTree, guild_id: int):
    guild = discord.Object(id=guild_id)

    @tree.command(name="lfg", description="LFG commands", guild=guild)
    @app_commands.describe(
        action="join|party|leave|status|preferences|cancel",
        party_size="Party size for join",
    )
    async def lfg_cmd(interaction: discord.Interaction, action: str, party_size: int | None = None):
        action = action.lower()
        if action == "join":
            await interaction.response.send_message("Use Find My Team panel or provide details.", ephemeral=True)
            return
        if action == "status":
            await interaction.response.send_message("Queue status view not fully implemented.", ephemeral=True)
            return
        await interaction.response.send_message("Use the panel UI for full flow.", ephemeral=True)
