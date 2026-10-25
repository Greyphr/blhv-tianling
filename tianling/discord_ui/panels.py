"""Persistent public panels. Buttons open ephemeral flows; they never edit the panel itself."""

from __future__ import annotations

import discord

from tianling.discord_ui.theme import THEME


class ProfileRegistryPanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Register/Edit My Profile",
        style=discord.ButtonStyle.primary,
        custom_id="tianling:panel:register_edit",
        emoji=THEME.emoji_profile,
    )
    async def register_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.registration import start_registration

        await start_registration(interaction)  # defers itself

    @discord.ui.button(
        label="Find My Team",
        style=discord.ButtonStyle.success,
        custom_id="tianling:panel:find_team",
        emoji=THEME.emoji_team,
    )
    async def find_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.lfg import start_lfg

        await start_lfg(interaction)

    @discord.ui.button(
        label="View My Profile",
        style=discord.ButtonStyle.secondary,
        custom_id="tianling:panel:view_profile",
        emoji=THEME.emoji_star,
    )
    async def view_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.views import view_my_profile

        await interaction.response.defer(ephemeral=True)
        await view_my_profile(interaction)

    @discord.ui.button(
        label="Matchmaking Preferences",
        style=discord.ButtonStyle.secondary,
        custom_id="tianling:panel:preferences",
        emoji=THEME.emoji_moon,
    )
    async def preferences(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.views import show_preferences

        await show_preferences(interaction)

    @discord.ui.button(
        label="Help",
        style=discord.ButtonStyle.secondary,
        custom_id="tianling:panel:help",
        emoji=THEME.emoji_registry,
    )
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.views import show_help

        await show_help(interaction)


class FindTeamPanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Find My Team",
        style=discord.ButtonStyle.success,
        custom_id="tianling:team:join",
        emoji=THEME.emoji_team,
    )
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.lfg import start_lfg

        await start_lfg(interaction)

    @discord.ui.button(
        label="Create Party",
        style=discord.ButtonStyle.primary,
        custom_id="tianling:team:party_create",
    )
    async def party_create(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.lfg import start_lfg

        await start_lfg(interaction, party_mode=True)

    @discord.ui.button(
        label="My Queue Status",
        style=discord.ButtonStyle.secondary,
        custom_id="tianling:team:status",
    )
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.lfg import show_status

        await show_status(interaction)

    @discord.ui.button(
        label="Leave Queue", style=discord.ButtonStyle.danger, custom_id="tianling:team:leave"
    )
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        from tianling.discord_ui.lfg import leave_everything

        await leave_everything(interaction)


def guide_embed() -> discord.Embed:
    from tianling.discord_ui.views import HELP_TEXT

    embed = discord.Embed(
        title=f"{THEME.emoji_sun} Welcome to Tianling",
        description=HELP_TEXT,
        color=THEME.color_primary,
    )
    embed.set_footer(text=THEME.footer)
    return embed


def registration_panel_embed() -> discord.Embed:
    return discord.Embed(
        title=f"{THEME.emoji_profile} Register your profile",
        description="Create or update your game profiles, view them, or manage "
        "matchmaking preferences. "
        "Ranks are self-reported.",
        color=THEME.color_primary,
    ).set_footer(text=THEME.footer)


def find_team_panel_embed() -> discord.Embed:
    return discord.Embed(
        title=f"{THEME.emoji_team} Find a team",
        description="Join the queue solo or with a party. We'll ping you when a group is ready.",
        color=THEME.color_success,
    ).set_footer(text=THEME.footer)
