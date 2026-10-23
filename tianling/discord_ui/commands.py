"""Slash commands."""
from __future__ import annotations

import discord
from discord import app_commands

from tianling.discord_ui.registration import start_registration
from tianling.discord_ui.views import view_my_profile


def setup_commands(tree: app_commands.CommandTree, guild_id: int):
    guild = discord.Object(id=guild_id)

    @tree.command(name="profile", description="Manage your profiles", guild=guild)
    @app_commands.describe(action="create|view|edit|delete|list")
    async def profile_cmd(interaction: discord.Interaction, action: str):
        action = action.lower()
        if action in ("create", "edit"):
            await interaction.response.defer(ephemeral=True)
            await start_registration(interaction)
            return
        if action in ("view", "list"):
            await interaction.response.defer(ephemeral=True)
            await view_my_profile(interaction)
            return
        if action == "delete":
            from tianling.db.repositories.game_profiles import GameProfileRepository
            from tianling.db.repositories.users import UserRepository
            from tianling.db.session import session_scope

            async with session_scope() as session:
                urepo = UserRepository(session)
                user = await urepo.get_or_create(interaction.guild_id, interaction.user.id)
                prep = GameProfileRepository(session)
                profiles = await prep.list_for_user(user.id)
                for p in profiles:
                    await session.delete(p)
                await session.commit()
            await interaction.response.send_message("Profiles deleted.", ephemeral=True)
            return
        await interaction.response.send_message("Usage: /profile create|view|edit|delete|list", ephemeral=True)

    @tree.command(name="tianling", description="Tianling help", guild=guild)
    async def tianling_cmd(interaction: discord.Interaction):
        embed = discord.Embed(title="Tianling", description="Use the Registry panel to register profiles.", color=0x7C9BFF)
        await interaction.response.send_message(embed=embed, ephemeral=True)
