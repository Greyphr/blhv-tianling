"""Slash commands: /profile, /lfg and the admin /tianling group.

Each handler defers (or opens a modal) inside the 3-second window, then does its work.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from tianling.core.seed_loader import SeedLoader
from tianling.discord_ui.channels import setup_guild
from tianling.services import admin as admin_service
from tianling.services.test_mode import TestMode

log = logging.getLogger(__name__)


def _game_choices() -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=g.name, value=g.key) for g in SeedLoader().load_games().games]


def setup_commands(bot) -> None:
    guild = discord.Object(id=bot.guild_id)

    # ---------------------------------------------------------------- /profile
    profile = app_commands.Group(
        name="profile", description="Manage your game profiles", guild_only=True
    )

    @profile.command(name="create", description="Create or edit a game profile")
    async def profile_create(interaction: discord.Interaction):
        from tianling.discord_ui.registration import start_registration

        await start_registration(interaction)

    @profile.command(name="edit", description="Edit a game profile")
    async def profile_edit(interaction: discord.Interaction):
        from tianling.discord_ui.registration import start_registration

        await start_registration(interaction)

    @profile.command(name="view", description="View your profiles")
    async def profile_view(interaction: discord.Interaction):
        from tianling.discord_ui.views import view_my_profile

        await interaction.response.defer(ephemeral=True)
        await view_my_profile(interaction)

    @profile.command(name="list", description="List your profiles")
    async def profile_list(interaction: discord.Interaction):
        from tianling.discord_ui.views import view_my_profile

        await interaction.response.defer(ephemeral=True)
        await view_my_profile(interaction)

    @profile.command(name="delete", description="Delete one of your game profiles")
    @app_commands.choices(game=_game_choices())
    async def profile_delete(interaction: discord.Interaction, game: app_commands.Choice[str]):
        from tianling.discord_ui.registration import ConfirmDeleteView
        from tianling.discord_ui.registration_flow import RegState

        await interaction.response.defer(ephemeral=True)
        state = RegState(interaction.guild_id, interaction.user.id, game.value)
        await interaction.followup.send(
            embed=discord.Embed(
                title=f"Delete your {game.name} profile?",
                description="This also removes your registry entry and cancels related "
                "queue requests.",
            ),
            view=ConfirmDeleteView(interaction.client, state),
            ephemeral=True,
        )

    # -------------------------------------------------------------------- /lfg
    lfg = app_commands.Group(name="lfg", description="Find a team", guild_only=True)

    @lfg.command(name="join", description="Join the queue")
    async def lfg_join(interaction: discord.Interaction):
        from tianling.discord_ui.lfg import start_lfg

        await start_lfg(interaction)

    @lfg.command(name="party", description="Build or manage your party")
    async def lfg_party(interaction: discord.Interaction):
        from tianling.discord_ui.lfg import show_party

        await show_party(interaction)

    @lfg.command(name="leave", description="Leave the queue")
    async def lfg_leave(interaction: discord.Interaction):
        from tianling.discord_ui.lfg import leave_everything

        await leave_everything(interaction)

    @lfg.command(name="cancel", description="Cancel your queue request")
    async def lfg_cancel(interaction: discord.Interaction):
        from tianling.discord_ui.lfg import leave_everything

        await leave_everything(interaction)

    @lfg.command(name="status", description="See where you are in the queue")
    async def lfg_status(interaction: discord.Interaction):
        from tianling.discord_ui.lfg import show_status

        await show_status(interaction)

    @lfg.command(name="preferences", description="Notification and availability preferences")
    async def lfg_prefs(interaction: discord.Interaction):
        from tianling.discord_ui.views import show_preferences

        await show_preferences(interaction)

    # ---------------------------------------------------------------- /tianling
    admin = app_commands.Group(
        name="tianling",
        description="Tianling administration",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    async def _is_admin(interaction: discord.Interaction) -> bool:
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms is None or not perms.administrator:
            await interaction.response.send_message("Administrators only.", ephemeral=True)
            return False
        return True

    @admin.command(name="setup", description="Create channels and post the panels")
    async def tianling_setup(interaction: discord.Interaction):
        if not await _is_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        result = await setup_guild(bot, interaction.guild)
        lines = [
            f"Created: {', '.join(result['created']) or 'nothing'}",
            f"Already existed: {', '.join(result['reused']) or 'nothing'}",
        ]
        if result["missing_permissions"]:
            lines.append("⚠️ Missing permissions: " + ", ".join(result["missing_permissions"]))
        lines += [f"❌ {e}" for e in result["errors"]]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @admin.command(name="config", description="Show or change a matchmaking/voice setting")
    @app_commands.describe(
        key="Setting name", value="New value (leave empty to just view settings)"
    )
    @app_commands.choices(
        key=[app_commands.Choice(name=k, value=k) for k in admin_service.SETTINGS]
    )
    async def tianling_config(
        interaction: discord.Interaction,
        key: app_commands.Choice[str] | None = None,
        value: str | None = None,
    ):
        if not await _is_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        async with bot.scope() as session:
            if key is not None and value is not None:
                try:
                    new = await admin_service.set_setting(
                        session, interaction.guild_id, key.value, value
                    )
                except admin_service.SettingError as exc:
                    await interaction.followup.send(f"❌ {exc}", ephemeral=True)
                    return
                await interaction.followup.send(f"✅ `{key.value}` is now `{new}`.", ephemeral=True)
                return
            current = await admin_service.current_settings(session, interaction.guild_id)
        lines = [f"`{k}` = `{v}` — {admin_service.SETTINGS[k][3]}" for k, v in current.items()]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @admin.command(name="status", description="Queue, proposal and voice overview")
    async def tianling_status(interaction: discord.Interaction):
        if not await _is_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        async with bot.scope() as session:
            s = await admin_service.status_summary(session, interaction.guild_id)
        queued = "\n".join(f"• {k}: {n}" for k, n in s.queued.items()) or "• nobody waiting"
        sched = (
            "ok" if bot.scheduler.last_error is None else f"last error: {bot.scheduler.last_error}"
        )
        await interaction.followup.send(
            f"**Queued**\n{queued}\n**Reserved entries:** {s.reserved}\n"
            f"**Pending proposals:** {s.proposals}\n"
            f"**Confirmed groups:** {s.finalized_recent}\n"
            f"**Temp voice channels:** {s.temp_channels}\n"
            f"**Scheduler:** {sched}",
            ephemeral=True,
        )

    @admin.command(name="rebuild", description="Rebuild the player registry from stored profiles")
    async def tianling_rebuild(interaction: discord.Interaction):
        if not await _is_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        stats = await bot.registry_syncer.reconcile(interaction.guild_id)
        if stats.get("no_channel"):
            await interaction.followup.send(
                "No player-registry channel is configured. Run `/tianling setup`.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Registry rebuilt: {stats['synced']} synced, {stats['failed']} failed, "
            f"{stats['orphans_removed']} orphan(s) removed.",
            ephemeral=True,
        )

    @admin.command(name="reconcile", description="Reconcile voice channels and pending proposals")
    async def tianling_reconcile(interaction: discord.Interaction):
        if not await _is_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        voice = await bot.voice.reconcile(interaction.guild_id)
        proposals = await bot.dispatcher.reconcile_proposals(interaction.guild_id)
        await interaction.followup.send(
            f"Voice: {voice['checked']} checked, {voice['removed']} missing removed, "
            f"{voice['grace_started']} grace started. Proposals refreshed: {proposals}.",
            ephemeral=True,
        )

    @admin.command(name="cancel_group", description="Cancel a pending proposal (no penalties)")
    async def tianling_cancel_group(interaction: discord.Interaction, group_id: str):
        if not await _is_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        from tianling.services.confirmation_service import ConfirmationService
        from tianling.services.settings import load_match_settings

        async with bot.scope() as session:
            cs = ConfirmationService(
                session, await load_match_settings(session, interaction.guild_id)
            )
            ok = await cs.cancel_group(group_id, "admin", rematch=True)
            events = cs.pop_events()
        await bot.dispatcher.dispatch(events)
        await interaction.followup.send(
            "Cancelled." if ok else "No pending proposal with that id.", ephemeral=True
        )

    # ------------------------------------------------------- /tianling test ...
    test = app_commands.Group(name="test", description="Test mode with fake players", parent=admin)

    @test.command(name="fill", description="Queue fake players and run matching")
    @app_commands.choices(game=_game_choices())
    async def test_fill(
        interaction: discord.Interaction,
        game: app_commands.Choice[str],
        activity: str,
        count: app_commands.Range[int, 1, 12],
    ):
        if not await _is_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        roles = [r["key"] for r in SeedLoader().load_roles_and_pool().roles.get(game.value, [])]
        order = [r for r in roles for _ in range(2)] or None
        async with bot.scope() as session:
            events = await TestMode(session, interaction.guild_id).fill(
                game.value, activity, count, roles=order
            )
        await bot.dispatcher.dispatch(events)
        await interaction.followup.send(f"Queued {count} test player(s).", ephemeral=True)

    @test.command(name="autoaccept", description="Make test players answer proposals automatically")
    async def test_autoaccept(
        interaction: discord.Interaction,
        enabled: bool,
        delay_seconds: float = 2.0,
        decline_probability: float = 0.0,
    ):
        if not await _is_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        from tianling.db.repositories.config import GuildConfigRepository

        async with bot.scope() as session:
            await GuildConfigRepository(session).set(
                interaction.guild_id,
                "test_mode",
                {
                    "auto_accept": enabled,
                    "delay_seconds": delay_seconds,
                    "decline_probability": decline_probability,
                },
            )
        await interaction.followup.send(
            f"Test auto-accept {'on' if enabled else 'off'}.", ephemeral=True
        )

    @test.command(name="clear", description="Remove all test players and their groups")
    async def test_clear(interaction: discord.Interaction):
        if not await _is_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        async with bot.scope() as session:
            await TestMode(session, interaction.guild_id).clear_all()
        await interaction.followup.send("Test data cleared.", ephemeral=True)

    for group in (profile, lfg, admin):
        bot.tree.add_command(group, guild=guild)
