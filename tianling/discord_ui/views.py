"""Read-only profile views and the matchmaking preferences panel."""

from __future__ import annotations

import discord

from tianling.discord_ui.theme import THEME
from tianling.services.profile_service import ProfileService


async def view_my_profile(interaction: discord.Interaction) -> None:
    """Followup-only: the caller has already deferred."""
    bot = interaction.client
    async with bot.scope() as session:
        svc = ProfileService(session, interaction.guild_id)
        profiles = await svc.list_profiles(interaction.user.id, validate=False)
        prefs = await svc.get_preferences(interaction.user.id)
    if not profiles:
        embed = discord.Embed(
            title=f"{THEME.emoji_profile} My Profiles",
            description="No profiles yet. Use **Register/Edit My Profile**.",
            color=THEME.color_warning,
        )
        embed.set_footer(text=THEME.footer)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    embed = discord.Embed(title=f"{THEME.emoji_profile} My Profiles", color=THEME.color_primary)
    for p in profiles:
        rank = " ".join(x for x in (p.current_rank_tier or "Unranked", p.current_division) if x)
        peak = " ".join(x for x in (p.peak_rank_tier, p.peak_division) if x) or "-"
        queue = (
            ""
            if p.current_queue_type in (None, "default")
            else f" ({p.current_queue_type.replace('_', ' ')})"
        )
        stale = (
            "\n⚠️ Needs an update: please re-confirm your rank."
            if getattr(p, "needs_update", False)
            else ""
        )
        embed.add_field(
            name=f"{p.game.replace('_', ' ').title()} • {p.ingame_name or '-'}",
            value=(
                f"Region: {p.region or '-'}\nCurrent: {rank}{queue} (self-reported)\n"
                f"Peak: {peak}{stale}"
            ),
            inline=False,
        )
    embed.add_field(
        name="Preferences",
        value=f"Notifications: {'on' if prefs['notifications'] else 'off'} · "
        f"Availability in registry: {'shown' if prefs['show_availability'] else 'hidden'}",
        inline=False,
    )
    embed.set_footer(text=THEME.footer)
    await interaction.followup.send(embed=embed, ephemeral=True)


class PreferencesView(discord.ui.View):
    """Two toggles. Availability is hidden unless the member opts in."""

    def __init__(self, bot, guild_id: int, member_id: int, prefs: dict[str, bool]) -> None:
        super().__init__(timeout=300)
        self.bot, self.guild_id, self.member_id, self.prefs = bot, guild_id, member_id, dict(prefs)
        self._build()

    def _build(self) -> None:
        self.clear_items()
        notif = discord.ui.Button(
            label=f"Notifications: {'On' if self.prefs['notifications'] else 'Off'}",
            style=discord.ButtonStyle.success
            if self.prefs["notifications"]
            else discord.ButtonStyle.secondary,
            custom_id="tianling:prefs:notif",
        )
        avail = discord.ui.Button(
            label=f"Show availability: {'On' if self.prefs['show_availability'] else 'Off'}",
            style=discord.ButtonStyle.success
            if self.prefs["show_availability"]
            else discord.ButtonStyle.secondary,
            custom_id="tianling:prefs:avail",
        )
        notif.callback, avail.callback = self._toggle_notif, self._toggle_avail  # type: ignore[method-assign]
        self.add_item(notif)
        self.add_item(avail)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.member_id

    def embed(self) -> discord.Embed:
        return discord.Embed(
            title=f"{THEME.emoji_moon} Matchmaking Preferences",
            description="**Notifications** — DMs and pings when your group forms or changes.\n"
            "**Show availability** — adds a 'Looking for a team' indicator to your registry "
            "entry. Off by default.",
            color=THEME.color_info,
        ).set_footer(text=THEME.footer)

    async def _apply(self, interaction: discord.Interaction, key: str) -> None:
        await interaction.response.defer()
        self.prefs[key] = not self.prefs[key]
        async with self.bot.scope() as session:
            svc = ProfileService(session, self.guild_id)
            if key == "notifications":
                await svc.set_notifications(self.member_id, self.prefs[key])
            else:
                await svc.set_availability_display(self.member_id, self.prefs[key])
            ops = svc.pop_registry_ops()
        await self.bot.registry_syncer.apply(self.guild_id, ops)
        self._build()
        await interaction.edit_original_response(embed=self.embed(), view=self)

    async def _toggle_notif(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, "notifications")

    async def _toggle_avail(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, "show_availability")


async def show_preferences(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    bot = interaction.client
    async with bot.scope() as session:
        prefs = await ProfileService(session, interaction.guild_id).get_preferences(
            interaction.user.id
        )
    view = PreferencesView(bot, interaction.guild_id, interaction.user.id, prefs)
    await interaction.followup.send(embed=view.embed(), view=view, ephemeral=True)


HELP_TEXT = (
    "**Register** your game profiles with *Register/Edit My Profile*. Ranks are **self-reported**: "
    "Tianling does not verify them.\n"
    "**Find My Team** puts you (or your party) in a queue. When a balanced group forms, everyone "
    "must **Accept** before the deadline; missing it means a short cooldown.\n"
    "Confirmed groups get a private voice channel that disappears when empty.\n"
    "If you leave the server, your profiles and queue requests are deleted automatically. "
    "You can also delete a profile yourself at any time."
)


async def show_help(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title=f"{THEME.emoji_registry} Tianling Help", description=HELP_TEXT, color=THEME.color_info
    )
    embed.set_footer(text=THEME.footer)
    await interaction.response.send_message(embed=embed, ephemeral=True)
