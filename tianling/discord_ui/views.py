"""View/profile helpers."""
from __future__ import annotations

import discord

from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.users import UserRepository
from tianling.db.session import session_scope
from tianling.discord_ui.theme import THEME


async def view_my_profile(interaction: discord.Interaction):
    async with session_scope() as session:
        urepo = UserRepository(session)
        user = await urepo.get_or_create(interaction.guild_id, interaction.user.id)
        prep = GameProfileRepository(session)
        profiles = await prep.list_for_user(user.id)
    if not profiles:
        embed = discord.Embed(title=f"{THEME.emoji_profile} My Profiles", description="No profiles yet. Use Register/Edit My Profile.", color=THEME.color_warning)
        embed.set_footer(text=THEME.footer)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    embed = discord.Embed(title=f"{THEME.emoji_profile} My Profiles", color=THEME.color_primary)
    for p in profiles:
        rank = f"{p.current_rank_tier or 'Unranked'} {p.current_division or ''} (self-reported)"
        embed.add_field(name=f"{p.game} ({p.current_queue_type})", value=rank, inline=False)
    embed.set_footer(text=THEME.footer)
    await interaction.followup.send(embed=embed, ephemeral=True)
