"""Profile registration wizard (discord.py layer).

Step order and options come from config via ``registration_flow``.  Callbacks defer
first, read/write the DB afterwards, and update the same ephemeral message.  Component
custom ids are prefixed ``tianling:reg:`` so menus orphaned by a restart are caught by
``ExpiredFlowItem`` (see ``flow_common``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import discord

from tianling.core.seed_loader import GameEntry, RolesAndPool, SeedLoader
from tianling.db.repositories.ranks import RankRepository
from tianling.discord_ui.registration_flow import (
    STEP_TITLES,
    RankOptions,
    RegState,
    activity_options,
    hero_label,
    hero_pages,
    plan_steps,
    review_lines,
)
from tianling.discord_ui.theme import THEME
from tianling.services.profile_service import ProfileError, ProfileService

log = logging.getLogger(__name__)

PFX = "tianling:reg"


@dataclass
class FlowContext:
    game: GameEntry
    rank_pools: list[str]
    roles_and_pool: RolesAndPool
    steps: list[str]
    ranks: dict[str, RankOptions] = field(default_factory=dict)

    @property
    def heroes(self) -> list[str]:
        return list((self.roles_and_pool.hero_pool.get(self.game.key) or {}).get("items") or [])

    @property
    def roles(self) -> list[dict]:
        return list(self.roles_and_pool.roles.get(self.game.key) or [])


async def load_flow_context(scope, guild_id: int, game_key: str) -> FlowContext:
    loader = SeedLoader()
    game = next(g for g in loader.load_games().games if g.key == game_key)
    pools = loader.rank_pools(game_key)
    rp = loader.load_roles_and_pool()
    ctx = FlowContext(game, pools, rp, plan_steps(game, rank_pools=pools, roles_and_pool=rp))
    async with scope() as session:
        repo = RankRepository(session)
        for pool in pools:
            ctx.ranks[pool] = RankOptions.from_definitions(
                await repo.list_for(guild_id, game_key, pool)
            )
    return ctx


async def save_registration(scope, syncer, state: RegState, steps: list[str]):
    """Persist the wizard result, then sync the registry *after* the commit."""
    async with scope() as session:
        svc = ProfileService(session, state.guild_id)
        fields = state.to_fields(steps)
        if await svc.get_profile(state.member_id, state.game, validate=False) is None:
            profile = await svc.create_profile(state.member_id, state.game, **fields)
        else:
            profile = await svc.update_profile(state.member_id, state.game, **fields)
        await svc.set_availability_display(state.member_id, state.show_availability)
        ops = svc.pop_registry_ops()
        profile_id = profile.id
    if syncer is not None:
        await syncer.apply(state.guild_id, ops)
    return profile_id


def _opt(label: str, value: str, *, default: bool = False, description: str | None = None):
    return discord.SelectOption(
        label=label[:100], value=value[:100], default=default, description=description
    )


class FlowView(discord.ui.View):
    """Base view: owner-only, Back/Cancel row, friendly timeout."""

    def __init__(self, flow: RegistrationFlow, *, back: bool = True) -> None:
        super().__init__(timeout=600)
        self.flow = flow
        if back and flow.idx > 0:
            btn = discord.ui.Button(label="Back", custom_id=f"{PFX}:back", row=4)
            btn.callback = self._back  # type: ignore[method-assign]
            self.add_item(btn)
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

    async def _back(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.flow.idx = max(0, self.flow.idx - 1)
        await self.flow.show(interaction)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        await interaction.edit_original_response(
            embed=discord.Embed(
                title="Registration cancelled",
                description="Nothing was saved.",
                color=THEME.color_warning,
            ),
            view=None,
        )


class NameModal(discord.ui.Modal, title="In-game name"):
    def __init__(self, flow: RegistrationFlow) -> None:
        super().__init__(custom_id=f"{PFX}:name_modal")
        self.flow = flow
        self.name_input = discord.ui.TextInput(
            label="Your in-game name", default=flow.state.ingame_name, min_length=1, max_length=64
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.flow.state.ingame_name = self.name_input.value.strip()
        await self.flow.advance(interaction)


class RegistrationFlow:
    def __init__(self, bot, ctx: FlowContext, state: RegState) -> None:
        self.bot, self.ctx, self.state = bot, ctx, state
        self.idx = 0
        self.hero_page = 0
        self.pending_tier: str | None = None  # tier chosen, division still to pick
        self.pending_kind = "current"
        self._last: discord.Interaction | None = None

    @property
    def step(self) -> str:
        return self.ctx.steps[self.idx]

    @property
    def options(self) -> RankOptions:
        pool = self.state.queue_type if self.state.queue_type in self.ctx.ranks else "default"
        if pool not in self.ctx.ranks:
            pool = next(iter(self.ctx.ranks))
        return self.ctx.ranks[pool]

    async def advance(self, interaction: discord.Interaction) -> None:
        self.idx = min(self.idx + 1, len(self.ctx.steps) - 1)
        await self.show(interaction)

    async def expire(self) -> None:
        if self._last is not None:
            try:
                await self._last.edit_original_response(
                    embed=discord.Embed(
                        title="This menu timed out",
                        description="Press Register/Edit My Profile to start again.",
                        color=THEME.color_warning,
                    ),
                    view=None,
                )
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------ render

    async def show(self, interaction: discord.Interaction) -> None:
        self._last = interaction
        embed, view = self._build()
        await interaction.edit_original_response(embed=embed, view=view)

    def _embed(self, description: str) -> discord.Embed:
        n, total = self.idx + 1, len(self.ctx.steps)
        return discord.Embed(
            title=(
                f"{THEME.emoji_profile} {self.ctx.game.name} — "
                f"{STEP_TITLES[self.step]} ({n}/{total})"
            ),
            description=description,
            color=THEME.color_primary,
        ).set_footer(text=THEME.footer)

    def _build(self) -> tuple[discord.Embed, discord.ui.View]:
        builder = getattr(self, f"_step_{self.step}")
        return builder()

    # ------------------------------------------------------------------- steps

    def _step_name(self):
        view = FlowView(self)
        btn = discord.ui.Button(
            label="Enter in-game name", style=discord.ButtonStyle.primary, custom_id=f"{PFX}:name"
        )

        async def cb(i: discord.Interaction) -> None:
            await i.response.send_modal(NameModal(self))

        btn.callback = cb  # type: ignore[method-assign]
        view.add_item(btn)
        return self._embed("What is your in-game name? (It is shown in the registry.)"), view

    def _select_view(
        self,
        placeholder: str,
        options: list[discord.SelectOption],
        cid: str,
        on_pick,
        *,
        min_values: int = 1,
        max_values: int = 1,
    ):
        view = FlowView(self)
        select = discord.ui.Select(
            placeholder=placeholder,
            options=options,
            custom_id=f"{PFX}:{cid}",
            min_values=min_values,
            max_values=max_values,
        )

        async def cb(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            await on_pick(interaction, list(select.values))

        select.callback = cb  # type: ignore[method-assign]
        view.add_item(select)
        return view

    def _step_region(self):
        cur = self.state.region

        async def pick(i, values):
            self.state.region = values[0]
            await self.advance(i)

        opts = [_opt(r, r, default=r == cur) for r in self.ctx.game.regions]
        return self._embed("Which region do you play in?"), self._select_view(
            "Choose a region", opts, "region", pick
        )

    def _step_queue(self):
        async def pick(i, values):
            changed = self.state.queue_type != values[0]
            self.state.queue_type = values[0]
            if changed:  # ranks differ per ladder, so they must be picked again
                self.state.cur_tier = self.state.cur_div = self.state.peak_tier = (
                    self.state.peak_div
                ) = None
            await self.advance(i)

        opts = [
            _opt(q.replace("_", " ").title(), q, default=q == self.state.queue_type)
            for q in self.ctx.rank_pools
        ]
        return self._embed("Which queue do you play?"), self._select_view(
            "Choose a queue", opts, "queue", pick
        )

    def _rank_step(self, kind: str):
        options = self.options
        tier_attr = "cur_tier" if kind == "current" else "peak_tier"
        div_attr = "cur_div" if kind == "current" else "peak_div"
        if self.pending_tier and self.pending_kind == kind:
            tier = self.pending_tier
            divs = options.divisions[tier]

            async def pick_div(i, values):
                setattr(self.state, tier_attr, tier)
                setattr(self.state, div_attr, values[0])
                self.pending_tier = None
                await self._after_rank(i, kind)

            opts = [
                _opt(f"{tier} {d}", d, default=d == getattr(self.state, div_attr)) for d in divs
            ]
            return (
                self._embed(f"Select your **{tier}** division."),
                self._select_view("Choose a division", opts, f"{kind}_div", pick_div),
            )

        async def pick_tier(i, values):
            tier = values[0]
            if options.needs_division(tier):
                self.pending_tier, self.pending_kind = tier, kind
                await self.show(i)  # same step, now asking for the division
                return
            setattr(self.state, tier_attr, tier)
            setattr(self.state, div_attr, None)  # tiers like Eternity / Top 500 have no division
            await self._after_rank(i, kind)

        cur = getattr(self.state, tier_attr)
        opts = [_opt(t, t, default=t == cur) for t in options.tiers]
        label = "current" if kind == "current" else "highest (peak)"
        return (
            self._embed(f"What is your {label} rank? Ranks are self-reported."),
            self._select_view(f"Choose your {label} tier", opts, f"{kind}_tier", pick_tier),
        )

    async def _after_rank(self, interaction: discord.Interaction, kind: str) -> None:
        if kind == "peak" and self.state.peak_below_current(self.options):
            # peak can never be lower than current: ask again with an explanation
            self.state.peak_tier = self.state.peak_div = None
            await self.show(interaction)
            await interaction.followup.send(
                "Your peak rank can't be lower than your current rank.", ephemeral=True
            )
            return
        await self.advance(interaction)

    def _step_current_rank(self):
        return self._rank_step("current")

    def _step_peak_rank(self):
        return self._rank_step("peak")

    def _step_roles(self):
        roles = self.ctx.roles
        view = FlowView(self)
        pref = discord.ui.Select(
            placeholder="Preferred role",
            options=[
                _opt(r["name"], r["key"], default=r["key"] == self.state.preferred_role)
                for r in roles
            ],
            custom_id=f"{PFX}:role_pref",
        )
        accept = discord.ui.Select(
            placeholder="Other roles you'd also play (optional)",
            options=[
                _opt(r["name"], r["key"], default=r["key"] in self.state.acceptable_roles)
                for r in roles
            ],
            custom_id=f"{PFX}:role_ok",
            min_values=0,
            max_values=len(roles),
            row=1,
        )

        async def on_pref(i: discord.Interaction) -> None:
            await i.response.defer()
            self.state.preferred_role = pref.values[0]
            await self.show(i)

        async def on_accept(i: discord.Interaction) -> None:
            await i.response.defer()
            self.state.acceptable_roles = list(accept.values)
            await self.show(i)

        async def done(i: discord.Interaction) -> None:
            await i.response.defer()
            if not self.state.preferred_role:
                await i.followup.send("Pick a preferred role first.", ephemeral=True)
                return
            await self.advance(i)

        pref.callback, accept.callback = on_pref, on_accept  # type: ignore[method-assign]
        ok = discord.ui.Button(
            label="Continue",
            style=discord.ButtonStyle.success,
            custom_id=f"{PFX}:roles_done",
            row=2,
        )
        ok.callback = done  # type: ignore[method-assign]
        view.add_item(pref)
        view.add_item(accept)
        view.add_item(ok)
        sel = (self.state.preferred_role or "-").title()
        return self._embed(
            f"Pick your preferred role, then any others you'd accept.\nPreferred: **{sel}**"
        ), view

    def _step_heroes(self):
        pages = hero_pages(self.ctx.heroes)
        self.hero_page = min(self.hero_page, len(pages) - 1)
        page = pages[self.hero_page]
        view = FlowView(self)
        chosen = set(self.state.heroes)
        select = discord.ui.Select(
            placeholder=f"{hero_label(self.ctx.game)} (page {self.hero_page + 1}/{len(pages)})",
            options=[_opt(h, h, default=h in chosen) for h in page],
            custom_id=f"{PFX}:heroes",
            min_values=0,
            max_values=len(page),
        )

        async def on_pick(i: discord.Interaction) -> None:
            await i.response.defer()
            on_page = set(page)
            kept = [h for h in self.state.heroes if h not in on_page]  # keep picks from other pages
            self.state.heroes = kept + list(select.values)
            await self.show(i)

        select.callback = on_pick  # type: ignore[method-assign]
        view.add_item(select)
        if len(pages) > 1:
            for label, delta, cid in (("◀ Prev page", -1, "hp"), ("Next page ▶", 1, "hn")):
                b = discord.ui.Button(
                    label=label,
                    custom_id=f"{PFX}:{cid}",
                    row=1,
                    disabled=not 0 <= self.hero_page + delta < len(pages),
                )

                async def flip(i: discord.Interaction, d=delta) -> None:
                    await i.response.defer()
                    self.hero_page += d
                    await self.show(i)

                b.callback = flip  # type: ignore[method-assign]
                view.add_item(b)
        done = discord.ui.Button(
            label="Continue",
            style=discord.ButtonStyle.success,
            custom_id=f"{PFX}:heroes_done",
            row=1,
        )

        async def go(i: discord.Interaction) -> None:
            await i.response.defer()
            await self.advance(i)

        done.callback = go  # type: ignore[method-assign]
        view.add_item(done)
        shown = ", ".join(self.state.heroes) or "none yet (optional)"
        return self._embed(
            f"Pick the {hero_label(self.ctx.game).lower()} you play (optional).\nSelected: {shown}"
        ), view

    def _step_playstyle(self):
        async def pick(i, values):
            self.state.playstyle = values[0]
            await self.advance(i)

        opts = [
            _opt(p.title(), p, default=p == self.state.playstyle)
            for p in self.ctx.roles_and_pool.playstyles[self.ctx.game.key]
        ]
        return self._embed("What is your playstyle?"), self._select_view(
            "Choose a playstyle", opts, "playstyle", pick
        )

    def _step_platform(self):
        async def pick(i, values):
            self.state.platform = values[0]
            await self.advance(i)

        opts = [
            _opt(p.upper() if p == "pc" else p.title(), p, default=p == self.state.platform)
            for p in self.ctx.game.platforms
        ]
        return self._embed("Which platform do you play on?"), self._select_view(
            "Choose a platform", opts, "platform", pick
        )

    def _step_activity(self):
        async def pick(i, values):
            self.state.activity = values[0]
            await self.advance(i)

        opts = [
            _opt(a.replace("_", " ").title(), a, default=a == self.state.activity)
            for a in activity_options(self.ctx.game)
        ]
        return self._embed("What do you want to play?"), self._select_view(
            "Choose an activity", opts, "activity", pick
        )

    def _step_review(self):
        view = FlowView(self)
        lines = "\n".join(
            f"**{k}:** {v}" for k, v in review_lines(self.state, self.ctx.steps, self.options)
        )
        toggle = discord.ui.Button(
            label=f"Availability in registry: {'On' if self.state.show_availability else 'Off'}",
            custom_id=f"{PFX}:avail",
            row=0,
        )
        confirm = discord.ui.Button(
            label="Save profile", style=discord.ButtonStyle.success, custom_id=f"{PFX}:save", row=1
        )

        async def flip(i: discord.Interaction) -> None:
            await i.response.defer()
            self.state.show_availability = not self.state.show_availability
            await self.show(i)

        async def save(i: discord.Interaction) -> None:
            await i.response.defer()
            try:
                await save_registration(
                    self.bot.scope, self.bot.registry_syncer, self.state, self.ctx.steps
                )
            except ProfileError as exc:
                await i.followup.send(f"Couldn't save: {exc}", ephemeral=True)
                return
            except Exception:
                log.exception("saving profile failed")
                await i.followup.send(
                    "Something went wrong saving your profile. Please try again.", ephemeral=True
                )
                return
            view.stop()
            await i.edit_original_response(
                embed=discord.Embed(
                    title=f"{THEME.emoji_success} Profile saved",
                    description=f"Your **{self.ctx.game.name}** profile is in the registry.",
                    color=THEME.color_success,
                ),
                view=None,
            )

        toggle.callback, confirm.callback = flip, save  # type: ignore[method-assign]
        view.add_item(toggle)
        view.add_item(confirm)
        return self._embed(
            lines + "\n\nRanks are self-reported. Check everything, then save."
        ), view


# --------------------------------------------------------------------- entry points


class GameSelectView(discord.ui.View):
    """Step 0: choose a game, then edit/delete if a profile already exists."""

    def __init__(self, bot, member_id: int) -> None:
        super().__init__(timeout=300)
        self.bot, self.member_id = bot, member_id
        games = SeedLoader().load_games().games
        select = discord.ui.Select(
            placeholder="Which game?",
            custom_id=f"{PFX}:game",
            options=[_opt(g.name, g.key) for g in games],
        )
        select.callback = self._pick  # type: ignore[method-assign]
        self._select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "This menu belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def _pick(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        game_key = self._select.values[0]
        guild_id = interaction.guild_id
        ctx = await load_flow_context(self.bot.scope, guild_id, game_key)
        async with self.bot.scope() as session:
            svc = ProfileService(session, guild_id)
            profile = await svc.get_profile(self.member_id, game_key, validate=False)
            show = (await svc.get_preferences(self.member_id))["show_availability"]
            state = (
                RegState.from_profile(profile, guild_id, self.member_id, show)
                if profile
                else RegState(guild_id, self.member_id, game_key, show_availability=show)
            )
        flow = RegistrationFlow(self.bot, ctx, state)
        if profile is None:
            await flow.show(interaction)
            return
        view = ExistingProfileView(self.bot, flow)
        await interaction.edit_original_response(
            embed=discord.Embed(
                title=f"{THEME.emoji_profile} You already have a {ctx.game.name} profile",
                description="Edit it (your answers are pre-filled), or delete it from the "
                "registry.",
                color=THEME.color_info,
            ),
            view=view,
        )


class ExistingProfileView(discord.ui.View):
    def __init__(self, bot, flow: RegistrationFlow) -> None:
        super().__init__(timeout=300)
        self.bot, self.flow = bot, flow
        for label, style, cid, cb in (
            ("Edit profile", discord.ButtonStyle.primary, "edit", self._edit),
            ("Delete profile", discord.ButtonStyle.danger, "delete", self._delete),
            ("Cancel", discord.ButtonStyle.secondary, "cancel", self._cancel),
        ):
            b = discord.ui.Button(label=label, style=style, custom_id=f"{PFX}:ex_{cid}")
            b.callback = cb  # type: ignore[method-assign]
            self.add_item(b)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.flow.state.member_id

    async def _edit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.flow.show(interaction)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.edit_original_response(
            embed=discord.Embed(title="Cancelled", color=THEME.color_warning), view=None
        )

    async def _delete(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        view = ConfirmDeleteView(self.bot, self.flow.state)
        await interaction.edit_original_response(
            embed=discord.Embed(
                title="Delete this profile?",
                description="This removes your profile and registry entry for this game, "
                "and cancels any queue request or party that uses it.",
                color=THEME.color_danger,
            ),
            view=view,
        )


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, bot, state: RegState) -> None:
        super().__init__(timeout=120)
        self.bot, self.state = bot, state
        yes = discord.ui.Button(
            label="Yes, delete", style=discord.ButtonStyle.danger, custom_id=f"{PFX}:del_yes"
        )
        no = discord.ui.Button(label="Keep it", custom_id=f"{PFX}:del_no")
        yes.callback, no.callback = self._yes, self._no  # type: ignore[method-assign]
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.state.member_id

    async def _no(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.edit_original_response(
            embed=discord.Embed(title="Kept your profile."), view=None
        )

    async def _yes(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await delete_registration(
            self.bot, self.state.guild_id, self.state.member_id, self.state.game
        )
        await interaction.edit_original_response(
            embed=discord.Embed(
                title=f"{THEME.emoji_success} Profile deleted", color=THEME.color_success
            ),
            view=None,
        )


async def delete_registration(bot, guild_id: int, member_id: int, game: str) -> bool:
    async with bot.scope() as session:
        svc = ProfileService(session, guild_id)
        deleted = await svc.delete_profile(member_id, game)
        ops = svc.pop_registry_ops()
    await bot.registry_syncer.apply(guild_id, ops)
    return deleted


async def start_registration(interaction: discord.Interaction) -> None:
    """Entry point for the Register/Edit My Profile button."""
    await interaction.response.defer(ephemeral=True)
    view = GameSelectView(interaction.client, interaction.user.id)
    await interaction.followup.send(
        embed=discord.Embed(
            title=f"{THEME.emoji_profile} Register or edit your profile",
            description="Choose a game to begin. Ranks are self-reported.",
            color=THEME.color_primary,
        ),
        view=view,
        ephemeral=True,
    )
