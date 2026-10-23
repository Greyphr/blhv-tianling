"""Persistent views and panels."""
from __future__ import annotations

import discord

from tianling.discord_ui.theme import THEME


class ProfileRegistryPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Register/Edit My Profile", style=discord.ButtonStyle.primary, custom_id="tianling:panel:register_edit", emoji=THEME.emoji_profile)
    async def register_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.registration import start_registration

        await interaction.response.defer(ephemeral=True)
        await start_registration(interaction)

    @discord.ui.button(label="Find My Team", style=discord.ButtonStyle.success, custom_id="tianling:panel:find_team", emoji=THEME.emoji_team)
    async def find_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.lfg import LFGStart

        await interaction.response.edit_message(content="Find My Team", view=LFGStart())

    @discord.ui.button(label="View My Profile", style=discord.ButtonStyle.secondary, custom_id="tianling:panel:view_profile", emoji=THEME.emoji_star)
    async def view_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.views import view_my_profile

        await interaction.response.defer(ephemeral=True)
        await view_my_profile(interaction)

    @discord.ui.button(label="Matchmaking Preferences", style=discord.ButtonStyle.secondary, custom_id="tianling:panel:preferences", emoji=THEME.emoji_moon)
    async def preferences(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Matchmaking Preferences (see Help/Addendum).", ephemeral=True)

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary, custom_id="tianling:panel:help", emoji=THEME.emoji_registry)
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title=f"{THEME.emoji_registry} Tianling Help", description="Use Register/Edit to create profiles. Ranks are self-reported. See Addendum for rules.", color=THEME.color_info)
        embed.set_footer(text=THEME.footer)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class FindTeamPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Join Queue", style=discord.ButtonStyle.success, custom_id="tianling:team:join", emoji=THEME.emoji_team)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Queue join UI coming soon.", ephemeral=True)

    @discord.ui.button(label="Leave Queue", style=discord.ButtonStyle.danger, custom_id="tianling:team:leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Queue leave UI coming soon.", ephemeral=True)

    @discord.ui.button(label="Create Party", style=discord.ButtonStyle.primary, custom_id="tianling:team:party_create")
    async def party_create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Party creation UI coming soon.", ephemeral=True)
