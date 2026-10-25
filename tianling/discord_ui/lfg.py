"""Find My Team: queue wizard, party builder, status, invite buttons (discord.py layer)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import discord

from tianling.core.seed_loader import GameEntry, SeedLoader
from tianling.db.repositories.ranks import RankRepository
from tianling.discord_ui.lfg_flow import (
    MemberSpec,
    composition_for,
    create_party_from_specs,
    guest_spec,
    members_missing_profiles,
    my_party,
    party_slot_summary,
    queue_party,
    queue_solo,
    spec_from_profile,
)
from tianling.discord_ui.registration_flow import RankOptions
from tianling.discord_ui.theme import THEME
from tianling.services.profile_service import ProfileService
from tianling.services.queue_service import InvalidQueueRequest, QueueError, QueueService
from tianling.services.settings import load_match_settings

log = logging.getLogger(__name__)
PFX = "tianling:lfg"


def _opt(label: str, value: str, default: bool = False) -> discord.SelectOption:
    return discord.SelectOption(label=label[:100], value=value[:100], default=default)


# ------------------------------------------------------------ persistent buttons


class PartyInviteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"tianling:party:(?P<action>accept|decline):(?P<party>[0-9a-f-]+):(?P<member>\d+)",
):
    def __init__(self, action: str, party_id: str, member_id: int) -> None:
        self.action, self.party_id, self.member_id = action, party_id, member_id
        accept = action == "accept"
        super().__init__(
            discord.ui.Button(
                label="Accept" if accept else "Decline",
                style=discord.ButtonStyle.success if accept else discord.ButtonStyle.danger,
                custom_id=f"tianling:party:{action}:{party_id}:{member_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match: re.Match, /):
        return cls(match["action"], match["party"], int(match["member"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        bot = interaction.client
        if interaction.user.id != self.member_id:
            await interaction.followup.send("This invite isn't for you.", ephemeral=True)
            return
        guild_id = self._guild_id(bot, interaction)
        async with bot.scope() as session:
            qs = QueueService(session, await load_match_settings(session, guild_id))
            changed = await qs.respond_party_invite(
                guild_id, self.party_id, self.member_id, self.action == "accept"
            )
            events = qs.pop_events()
        await bot.dispatcher.dispatch(events)
        text = (
            (
                "You joined the party. Your leader can now queue."
                if self.action == "accept"
                else "Invite declined."
            )
            if changed
            else "This invite has expired or was already answered."
        )
        await interaction.edit_original_response(
            embed=discord.Embed(
                title=f"{THEME.emoji_team} Party invite", description=text, color=THEME.color_info
            ),
            view=None,
        )

    @staticmethod
    def _guild_id(bot, interaction: discord.Interaction) -> int:
        return (
            interaction.guild_id or bot.guild_id
        )  # invites arrive by DM: use the configured guild


class StillLookingButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"tianling:still:(?P<action>yes|no):(?P<game>[a-z0-9_]+):(?P<activity>[a-z0-9_]+)",
):
    def __init__(self, action: str, game: str, activity: str) -> None:
        self.action, self.game, self.activity = action, game, activity
        yes = action == "yes"
        super().__init__(
            discord.ui.Button(
                label="Keep looking" if yes else "Leave queue",
                style=discord.ButtonStyle.success if yes else discord.ButtonStyle.secondary,
                custom_id=f"tianling:still:{action}:{game}:{activity}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match: re.Match, /):
        return cls(match["action"], match["game"], match["activity"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        bot = interaction.client
        guild_id = interaction.guild_id or bot.guild_id
        async with bot.scope() as session:
            qs = QueueService(session, await load_match_settings(session, guild_id))
            if self.action == "yes":
                ok = await qs.extend_entry(guild_id, interaction.user.id, self.game, self.activity)
                text = (
                    "Still searching — your request was extended."
                    if ok
                    else "That request is no longer active."
                )
            else:
                await qs.leave_queue(guild_id, interaction.user.id, self.game, self.activity)
                text = "You left the queue."
        await interaction.edit_original_response(
            embed=discord.Embed(
                title=f"{THEME.emoji_team} Queue", description=text, color=THEME.color_info
            ),
            view=None,
        )


# ----------------------------------------------------------------- wizard state


@dataclass
class LFGState:
    guild_id: int
    member_id: int
    game: str | None = None
    activity: str | None = None
    region: str | None = None
    specs: list[MemberSpec] = field(default_factory=list)
    party_id: str | None = None
    party_mode: bool = False
    profile_region: str | None = None


class LFGView(discord.ui.View):
    def __init__(self, flow: LFGFlow, *, timeout: float = 600) -> None:
        super().__init__(timeout=timeout)
        self.flow = flow
        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.danger, custom_id=f"{PFX}:cancel", row=4
        )
        cancel.callback = self._cancel  # type: ignore[method-assign]
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.flow.state.member_id:
            await interaction.response.send_message(
                "This menu belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        await self.flow.expire()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        await interaction.edit_original_response(
            embed=discord.Embed(
                title="Cancelled",
                description="You were not added to the queue.",
                color=THEME.color_warning,
            ),
            view=None,
        )


class LFGFlow:
    def __init__(self, bot, state: LFGState) -> None:
        self.bot, self.state = bot, state
        self._last: discord.Interaction | None = None
        self.games = {g.key: g for g in SeedLoader().load_games().games}

    @property
    def game(self) -> GameEntry:
        return self.games[self.state.game]  # type: ignore[index]

    async def expire(self) -> None:
        if self._last is not None:
            try:
                await self._last.edit_original_response(
                    embed=discord.Embed(
                        title="This menu timed out",
                        description="Press Find My Team to start again.",
                        color=THEME.color_warning,
                    ),
                    view=None,
                )
            except discord.HTTPException:
                pass

    async def render(
        self, interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View | None
    ) -> None:
        self._last = interaction
        embed.set_footer(text=THEME.footer)
        await interaction.edit_original_response(embed=embed, view=view)

    def _embed(self, title: str, description: str, color: int | None = None) -> discord.Embed:
        return discord.Embed(
            title=f"{THEME.emoji_team} {title}",
            description=description,
            color=color or THEME.color_primary,
        )

    def _select(self, view: LFGView, placeholder: str, options, cid: str, cb) -> None:
        select = discord.ui.Select(
            placeholder=placeholder, options=options, custom_id=f"{PFX}:{cid}"
        )

        async def handler(i: discord.Interaction) -> None:
            await i.response.defer()
            await cb(i, select.values[0])

        select.callback = handler  # type: ignore[method-assign]
        view.add_item(select)

    # -- step 1: game
    async def step_game(self, interaction: discord.Interaction) -> None:
        async with self.bot.scope() as session:
            profiles = await ProfileService(session, self.state.guild_id).list_profiles(
                self.state.member_id, validate=False
            )
            regions = {p.game: p.region for p in profiles}
        if not profiles:
            await self.render(
                interaction,
                self._embed(
                    "No profile yet",
                    "Register a profile first: use **Register/Edit My Profile**.",
                    THEME.color_warning,
                ),
                None,
            )
            return
        self.state.profile_region = None
        view = LFGView(self)

        async def picked(i: discord.Interaction, value: str) -> None:
            self.state.game = value
            self.state.profile_region = regions.get(value)
            await self.step_activity(i)

        self._select(
            view,
            "Which game?",
            [_opt(self.games[p.game].name, p.game) for p in profiles if p.game in self.games],
            "game",
            picked,
        )
        await self.render(
            interaction, self._embed("Find My Team", "Which game do you want a team for?"), view
        )

    # -- step 2: activity
    async def step_activity(self, interaction: discord.Interaction) -> None:
        acts = list(self.game.activities)
        options = [_opt(a.replace("_", " ").title(), a) for a in acts]
        if len(acts) > 1:
            options.append(_opt("Either (match whichever fills first)", "either"))
        view = LFGView(self)

        async def picked(i: discord.Interaction, value: str) -> None:
            self.state.activity = value
            await self.step_region(i)

        self._select(view, "What do you want to play?", options, "activity", picked)
        await self.render(interaction, self._embed(self.game.name, "Choose an activity."), view)

    # -- step 3: region
    async def step_region(self, interaction: discord.Interaction) -> None:
        view = LFGView(self)

        async def picked(i: discord.Interaction, value: str) -> None:
            self.state.region = value
            if self.state.party_mode:
                await self.step_party(i)
            else:
                await self.step_mode(i)

        options = [_opt(r, r, r == self.state.profile_region) for r in self.game.regions]
        self._select(view, "Region", options, "region", picked)
        await self.render(interaction, self._embed(self.game.name, "Which region?"), view)

    # -- step 4: solo or party
    async def step_mode(self, interaction: discord.Interaction) -> None:
        view = LFGView(self)
        solo = discord.ui.Button(
            label="Queue solo", style=discord.ButtonStyle.success, custom_id=f"{PFX}:solo"
        )
        party = discord.ui.Button(
            label="Build a party", style=discord.ButtonStyle.primary, custom_id=f"{PFX}:party"
        )

        async def on_solo(i: discord.Interaction) -> None:
            await i.response.defer()
            await self.step_confirm_solo(i)

        async def on_party(i: discord.Interaction) -> None:
            await i.response.defer()
            await self.step_party(i)

        solo.callback, party.callback = on_solo, on_party  # type: ignore[method-assign]
        view.add_item(solo)
        view.add_item(party)
        await self.render(
            interaction, self._embed(self.game.name, "Queue by yourself, or bring a party?"), view
        )

    async def step_confirm_solo(self, interaction: discord.Interaction) -> None:
        view = LFGView(self)
        join = discord.ui.Button(
            label="Join Queue", style=discord.ButtonStyle.success, custom_id=f"{PFX}:join_solo"
        )

        async def go(i: discord.Interaction) -> None:
            await i.response.defer()
            await self._submit(i, solo=True)

        join.callback = go  # type: ignore[method-assign]
        view.add_item(join)
        st = self.state
        await self.render(
            interaction,
            self._embed(
                "Ready?",
                f"**{self.game.name}** · "
                f"{(st.activity or '').replace('_', ' ').title()} · {st.region}\nSolo queue.",
            ),
            view,
        )

    async def _submit(self, interaction: discord.Interaction, *, solo: bool) -> None:
        st = self.state
        try:
            if solo:
                status, events = await queue_solo(
                    self.bot.scope, st.guild_id, st.member_id, st.game, st.activity, st.region
                )
            else:
                status, events = await queue_party(
                    self.bot.scope,
                    st.guild_id,
                    st.member_id,
                    st.party_id,
                    st.game,
                    st.activity,
                    st.region,
                )
        except QueueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception:
            log.exception("queue join failed")
            await interaction.followup.send(
                "Something went wrong joining the queue. Please try again.", ephemeral=True
            )
            return
        await self.bot.dispatcher.dispatch(events)
        text = (
            "A match was found for you! Check the matchmaking channel to confirm."
            if status == "reserved"
            else "You're in the queue. I'll let you know when a group forms."
        )
        await self.render(
            interaction, self._embed("You're queued", text, THEME.color_success), None
        )

    # -- party builder
    def _party_text(self) -> str:
        st = self.state
        lines = []
        for s in st.specs:
            who = s.label or (f"<@{s.member_id}>" if not s.is_guest else "Guest")
            rank = f" · {s.snapshot.get('label') or s.snapshot.get('tier')}" if s.snapshot else ""
            lines.append(f"• {who} — {(s.role or 'any role').replace('_', ' ').title()}{rank}")
        summary = party_slot_summary(
            st.game, st.activity if st.activity != "either" else self.game.activities[0], st.specs
        )
        return "\n".join(lines) + f"\n\n**Open:** {summary}"

    async def step_party(self, interaction: discord.Interaction) -> None:
        st = self.state
        if not st.specs:
            async with self.bot.scope() as session:
                profile = await ProfileService(session, st.guild_id).get_profile(
                    st.member_id, st.game, validate=False
                )
            if profile is None:
                await self.render(
                    interaction,
                    self._embed(
                        "Missing profile", "Register a profile first.", THEME.color_warning
                    ),
                    None,
                )
                return
            st.specs = [spec_from_profile(st.member_id, profile, label=f"<@{st.member_id}> (you)")]
        act = st.activity if st.activity != "either" else self.game.activities[0]
        comp = composition_for(st.game, act)
        max_size = comp.party_max_size if comp else 5
        view = LFGView(self)
        if len(st.specs) < max_size:
            users = discord.ui.UserSelect(
                placeholder="Add a server member", custom_id=f"{PFX}:add_user", row=0
            )

            async def add_user(i: discord.Interaction) -> None:
                await i.response.defer()
                await self._add_user(i, users.values[0], max_size)

            users.callback = add_user  # type: ignore[method-assign]
            view.add_item(users)
        extras = [s for s in st.specs if s.member_id != st.member_id]
        if extras:
            rm = discord.ui.Select(
                placeholder="Remove someone",
                custom_id=f"{PFX}:remove",
                row=1,
                options=[_opt(s.label or str(s.member_id), str(s.member_id)) for s in extras],
            )

            async def remove(i: discord.Interaction) -> None:
                await i.response.defer()
                st.specs = [s for s in st.specs if str(s.member_id) != rm.values[0]]
                await self.step_party(i)

            rm.callback = remove  # type: ignore[method-assign]
            view.add_item(rm)
        buttons = [
            (
                "Add guest",
                f"{PFX}:guest",
                self._guest,
                discord.ButtonStyle.secondary,
                len(st.specs) >= max_size,
            ),
            (
                "Set roles",
                f"{PFX}:roles",
                self._roles,
                discord.ButtonStyle.secondary,
                comp is None or not comp.roles,
            ),
            (
                "Create party",
                f"{PFX}:create",
                self._create,
                discord.ButtonStyle.success,
                len(st.specs) < 2,
            ),
        ]
        for label, cid, handler, style, disabled in buttons:
            b = discord.ui.Button(label=label, custom_id=cid, style=style, row=2, disabled=disabled)
            b.callback = handler  # type: ignore[method-assign]
            view.add_item(b)
        await self.render(
            interaction,
            self._embed(f"{self.game.name} party ({len(st.specs)}/{max_size})", self._party_text()),
            view,
        )

    async def _add_user(self, interaction: discord.Interaction, user, max_size: int) -> None:
        st = self.state
        if (
            getattr(user, "bot", False)
            or user.id == st.member_id
            or any(s.member_id == user.id for s in st.specs)
        ):
            await interaction.followup.send("You can't add that member.", ephemeral=True)
            return
        if await members_missing_profiles(self.bot.scope, st.guild_id, st.game, [user.id]):
            await interaction.followup.send(
                f"{user.mention} has no {self.game.name} profile yet.", ephemeral=True
            )
            return
        async with self.bot.scope() as session:
            profile = await ProfileService(session, st.guild_id).get_profile(
                user.id, st.game, validate=False
            )
        st.specs.append(spec_from_profile(user.id, profile, label=f"<@{user.id}> (invited)"))
        await self.step_party(interaction)

    async def _guest(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(GuestModal(self))

    async def add_guest(
        self, interaction: discord.Interaction, tier: str, division: str, role: str
    ) -> None:
        st = self.state
        async with self.bot.scope() as session:
            pool = (self.bot_pools(st.game))[0]
            options = RankOptions.from_definitions(
                await RankRepository(session).list_for(st.guild_id, st.game, pool)
            )
        roles = list(SeedLoader().load_roles_and_pool().roles.get(st.game) or [])
        try:
            st.specs.append(
                guest_spec(options, roles, tier=tier, division=division or None, role=role or None)
            )
        except InvalidQueueRequest as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await self.step_party(interaction)

    @staticmethod
    def bot_pools(game: str) -> list[str]:
        return SeedLoader().rank_pools(game)

    async def _roles(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        st = self.state
        roles = list(SeedLoader().load_roles_and_pool().roles.get(st.game) or [])
        view = LFGView(self)
        target: dict[str, str] = {}
        who = discord.ui.Select(
            placeholder="Who?",
            custom_id=f"{PFX}:role_who",
            options=[_opt(s.label or str(s.member_id), str(s.member_id)) for s in st.specs],
        )
        role = discord.ui.Select(
            placeholder="Their role",
            custom_id=f"{PFX}:role_pick",
            row=1,
            options=[_opt(r["name"], r["key"]) for r in roles],
        )

        async def pick_who(i: discord.Interaction) -> None:
            await i.response.defer()
            target["id"] = who.values[0]

        async def pick_role(i: discord.Interaction) -> None:
            await i.response.defer()
            if "id" not in target:
                await i.followup.send("Pick who first.", ephemeral=True)
                return
            for s in st.specs:
                if str(s.member_id) == target["id"]:
                    s.role = role.values[0]
            await self.step_party(i)

        who.callback, role.callback = pick_who, pick_role  # type: ignore[method-assign]
        view.add_item(who)
        view.add_item(role)
        await self.render(
            interaction, self._embed("Set roles", "Pick a member, then their role."), view
        )

    async def _create(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        st = self.state
        try:
            st.party_id, events = await create_party_from_specs(
                self.bot.scope, st.guild_id, st.member_id, st.game, st.specs
            )
        except QueueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await self.bot.dispatcher.dispatch(events)
        await self.step_party_ready(interaction)

    async def step_party_ready(self, interaction: discord.Interaction) -> None:
        view = LFGView(self)
        join = discord.ui.Button(
            label="Join Queue", style=discord.ButtonStyle.success, custom_id=f"{PFX}:join_party"
        )
        disband = discord.ui.Button(
            label="Disband party", style=discord.ButtonStyle.danger, custom_id=f"{PFX}:disband"
        )

        async def go(i: discord.Interaction) -> None:
            await i.response.defer()
            await self._submit(i, solo=False)

        async def drop(i: discord.Interaction) -> None:
            await i.response.defer()
            await disband_party(self.bot, self.state.guild_id, self.state.member_id)
            await self.render(
                i,
                self._embed("Party disbanded", "Your party was removed.", THEME.color_warning),
                None,
            )

        join.callback, disband.callback = go, drop  # type: ignore[method-assign]
        view.add_item(join)
        view.add_item(disband)
        invited = [
            s for s in self.state.specs if not s.is_guest and s.member_id != self.state.member_id
        ]
        note = (
            "Invites were sent. **Join Queue** works once everyone has accepted."
            if invited
            else "Your party is ready."
        )
        await self.render(
            interaction, self._embed("Party created", f"{self._party_text()}\n\n{note}"), view
        )


class GuestModal(discord.ui.Modal, title="Add a guest"):
    def __init__(self, flow: LFGFlow) -> None:
        super().__init__(custom_id=f"{PFX}:guest_modal")
        self.flow = flow
        self.tier = discord.ui.TextInput(label="Rank tier (e.g. Gold)", max_length=32)
        self.division = discord.ui.TextInput(
            label="Division (e.g. II) if the tier has one", required=False, max_length=8
        )
        self.role = discord.ui.TextInput(
            label="Role (if the game has roles)", required=False, max_length=32
        )
        for item in (self.tier, self.division, self.role):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.flow.add_guest(
            interaction, self.tier.value, self.division.value, self.role.value
        )


async def disband_party(bot, guild_id: int, member_id: int) -> None:
    async with bot.scope() as session:
        qs = QueueService(session, await load_match_settings(session, guild_id))
        await qs.leave_party(guild_id, member_id)
        events = qs.pop_events()
    await bot.dispatcher.dispatch(events)


# --------------------------------------------------------------------- entry points


async def start_lfg(interaction: discord.Interaction, *, party_mode: bool = False) -> None:
    """Begin the wizard in an ephemeral message (never touches the public panel)."""
    await interaction.response.defer(ephemeral=True)
    bot = interaction.client
    flow = LFGFlow(bot, LFGState(interaction.guild_id, interaction.user.id, party_mode=party_mode))
    await flow.step_game(interaction)


def status_embed(statuses, names: dict[str, str] | None = None) -> discord.Embed:
    embed = discord.Embed(title=f"{THEME.emoji_team} Queue Status", color=THEME.color_primary)
    if not statuses:
        embed.description = "You're not in any queue."
        embed.color = THEME.color_warning
    for s in statuses:
        e = s.entry
        who = f"Party of {e.party_size}" if e.party_id else "Solo"
        state = {"queued": "🔎 Searching", "reserved": "⏳ Awaiting confirmations"}.get(
            e.status, e.status
        )
        until = f"<t:{int(e.expires_at.timestamp())}:R>" if e.expires_at else "-"
        embed.add_field(
            name=f"{e.game.replace('_', ' ').title()} · {e.activity.replace('_', ' ').title()}",
            value=f"{state}\n{who} · {e.region or 'Any region'}\nExpires {until}",
            inline=False,
        )
    embed.set_footer(text=THEME.footer)
    return embed


class StatusView(discord.ui.View):
    """Leave buttons for each queued entry (ephemeral, so no persistence needed)."""

    def __init__(self, bot, guild_id: int, member_id: int, statuses) -> None:
        super().__init__(timeout=300)
        self.bot, self.guild_id, self.member_id = bot, guild_id, member_id
        for n, s in enumerate(statuses[:4]):
            e = s.entry
            btn = discord.ui.Button(
                label=f"Leave {e.game.replace('_', ' ').title()} {e.activity}"[:80],
                style=discord.ButtonStyle.danger,
                custom_id=f"{PFX}:leave:{n}",
            )

            async def leave(
                i: discord.Interaction, game=e.game, act=e.activity, group=s.group_uuid
            ) -> None:
                await i.response.defer()
                if group:
                    await i.followup.send(
                        "You're in a pending proposal: use **Decline** on it to leave.",
                        ephemeral=True,
                    )
                    return
                async with self.bot.scope() as session:
                    qs = QueueService(session, await load_match_settings(session, self.guild_id))
                    await qs.leave_queue(self.guild_id, self.member_id, game, act)
                await i.edit_original_response(
                    embed=discord.Embed(title="You left the queue.", color=THEME.color_success),
                    view=None,
                )

            btn.callback = leave  # type: ignore[method-assign]
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.member_id


async def show_status(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    bot = interaction.client
    async with bot.scope() as session:
        statuses = await QueueService(session).get_status(interaction.guild_id, interaction.user.id)
    view = (
        StatusView(bot, interaction.guild_id, interaction.user.id, statuses)
        if statuses
        else discord.utils.MISSING
    )
    await interaction.followup.send(embed=status_embed(statuses), view=view, ephemeral=True)


async def leave_everything(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    bot = interaction.client
    async with bot.scope() as session:
        qs = QueueService(session, await load_match_settings(session, interaction.guild_id))
        result = await qs.leave_queue(interaction.guild_id, interaction.user.id)
    if result:
        text = f"Left the queue ({len(result.cancelled)} request(s) cancelled)."
    elif result.in_proposal:
        text = "You're in a pending proposal: use **Decline** on it to leave."
    else:
        text = "You weren't in a queue."
    await interaction.followup.send(text, ephemeral=True)


async def show_party(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    bot = interaction.client
    party_id, members = await my_party(bot.scope, interaction.guild_id, interaction.user.id)
    if not members:
        await interaction.followup.send(
            "You don't lead a party. Use **Find My Team → Build a party**.", ephemeral=True
        )
        return
    lines = [
        f"• {'Guest' if m.is_guest else f'<@{m.member_id}>'} — "
        f"{(m.declared_role or 'any role').title()}"
        f"{'' if m.confirmed else ' (invite pending)'}"
        for m in members
    ]
    game = members[0].game
    view = discord.ui.View(timeout=300)
    disband = discord.ui.Button(
        label="Disband party", style=discord.ButtonStyle.danger, custom_id=f"{PFX}:disband_cmd"
    )

    async def drop(i: discord.Interaction) -> None:
        await i.response.defer()
        await disband_party(bot, interaction.guild_id, interaction.user.id)
        await i.edit_original_response(embed=discord.Embed(title="Party disbanded"), view=None)

    disband.callback = drop  # type: ignore[method-assign]
    view.add_item(disband)
    await interaction.followup.send(
        embed=discord.Embed(
            title=f"{THEME.emoji_team} Your party ({game or 'game'})",
            description="\n".join(lines),
            color=THEME.color_primary,
        ),
        view=view,
        ephemeral=True,
    )
