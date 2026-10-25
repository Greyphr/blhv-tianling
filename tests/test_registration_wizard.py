"""Fix 6: registration logic, UI component limits and the wizard driven end to end."""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest
from sqlalchemy import select

from tianling.core.seed_loader import SeedLoader
from tianling.db.models import GameProfile, RegistryMessage
from tianling.db.session import make_session_scope
from tianling.discord_ui.fake_adapter import FakeDiscordAdapter
from tianling.discord_ui.registration import (
    NameModal,
    RegistrationFlow,
    delete_registration,
    load_flow_context,
    save_registration,
)
from tianling.discord_ui.registration_flow import (
    PAGE_SIZE,
    RankOptions,
    RegState,
    hero_pages,
    plan_steps,
    review_lines,
)
from tianling.discord_ui.registry import RegistrySyncer, format_profile
from tianling.services.profile_service import ProfileService
from tianling.services.settings import set_channel_ids

G = 1
LOADER = SeedLoader()
GAMES = [g.key for g in LOADER.load_games().games]


class FakeResponse:
    def __init__(self):
        self.done = False

    async def defer(self, **_):
        self.done = True

    async def send_modal(self, modal):
        self.done = True
        self.modal = modal

    def is_done(self):
        return self.done

    async def send_message(self, *a, **k):
        self.done = True


class FakeInteraction:
    def __init__(self, user_id=500):
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = G
        self.response = FakeResponse()
        self.followup = SimpleNamespace(send=self._followup)
        self.edits: list[dict] = []
        self.followups: list[str] = []

    async def edit_original_response(self, **kw):
        self.edits.append(kw)

    async def _followup(self, content=None, **kw):
        self.followups.append(content)

    @property
    def view(self):
        return self.edits[-1].get("view")

    @property
    def embed(self):
        return self.edits[-1].get("embed")


def pick(interaction_view: discord.ui.View, suffix: str):
    for child in interaction_view.children:
        if getattr(child, "custom_id", "").endswith(suffix):
            return child
    raise AssertionError(
        f"no component ending {suffix!r}: {[c.custom_id for c in interaction_view.children]}"
    )


async def choose(view, suffix: str, *values: str):
    select = pick(view, suffix)
    select._values = list(values)
    it = FakeInteraction()
    await select.callback(it)
    return it


class Bot:
    def __init__(self, factory, adapter):
        self.scope = make_session_scope(factory)
        self.registry_syncer = RegistrySyncer(self.scope, adapter)


# ------------------------------------------------------------------ pure logic


@pytest.mark.parametrize("game", GAMES)
def test_steps_are_driven_by_config(game):
    g = next(x for x in LOADER.load_games().games if x.key == game)
    rp = LOADER.load_roles_and_pool()
    steps = plan_steps(g, rank_pools=LOADER.rank_pools(game), roles_and_pool=rp)
    assert steps[:2] == ["name", "region"] and steps[-1] == "review"
    assert ("queue" in steps) == (game == "overwatch_2")  # only ladders with several pools
    assert ("roles" in steps) == (g.role_system == "role_slots")
    assert ("playstyle" in steps) == (game == "apex_legends")
    assert ("platform" in steps) == (len(g.platforms) > 1)


def test_divisions_are_skipped_for_tiers_without_them(session_factory):
    from tianling.db.repositories.ranks import RankRepository

    async def go():
        async with session_factory() as session:
            repo = RankRepository(session)
            mr = RankOptions.from_definitions(await repo.list_for(G, "marvel_rivals", "default"))
            dl = RankOptions.from_definitions(await repo.list_for(G, "deadlock", "default"))
            apex = RankOptions.from_definitions(await repo.list_for(G, "apex_legends", "default"))
        return mr, dl, apex

    import asyncio

    mr, dl, apex = asyncio.run(go())
    assert mr.needs_division("Gold") and not mr.needs_division("One Above All")
    assert not mr.needs_division("Eternity")
    assert not dl.needs_division("Obscurus")
    assert not apex.needs_division("Apex Predator")


def test_hero_pages_respect_the_25_option_limit():
    pages = hero_pages([f"h{i}" for i in range(60)])
    assert [len(p) for p in pages] == [25, 25, 10] and PAGE_SIZE == 25


# --------------------------------------------------- UI limits for every step


@pytest.mark.asyncio
@pytest.mark.parametrize("game", GAMES)
async def test_every_step_view_fits_discord_limits(session_factory, game):
    scope = make_session_scope(session_factory)
    ctx = await load_flow_context(scope, G, game)
    state = RegState(G, 500, game, queue_type=ctx.rank_pools[0], region="EMEA")
    flow = RegistrationFlow(Bot(session_factory, FakeDiscordAdapter()), ctx, state)
    opts = flow.options
    for idx, step in enumerate(ctx.steps):
        flow.idx = idx
        flow.pending_tier = None
        embed, view = flow._build()
        _assert_limits(view, embed)
        if step in ("current_rank", "peak_rank"):  # also check the division sub-step
            tier = next(t for t in opts.tiers if opts.needs_division(t))
            flow.pending_tier, flow.pending_kind = (
                tier,
                "current" if step == "current_rank" else "peak",
            )
            embed, view = flow._build()
            _assert_limits(view, embed)
            flow.pending_tier = None
        if step == "heroes":
            for page in range(len(hero_pages(ctx.heroes))):
                flow.hero_page = page
                _assert_limits(flow._build()[1], flow._build()[0])


def _assert_limits(view, embed):
    assert len(view.children) <= 25
    ids = [c.custom_id for c in view.children]
    assert len(ids) == len(set(ids)), f"duplicate custom ids: {ids}"
    for child in view.children:
        assert child.custom_id.startswith("tianling:reg:") and len(child.custom_id) <= 100
        if isinstance(child, discord.ui.Select):
            assert 1 <= len(child.options) <= 25
            assert child.max_values <= len(child.options)
    assert len(embed.description or "") <= 4096


# ------------------------------------------------------- wizard, end to end


@pytest.mark.asyncio
async def test_wizard_end_to_end_marvel_rivals_saves_and_syncs_registry(session_factory):
    adapter = FakeDiscordAdapter()
    bot = Bot(session_factory, adapter)
    async with bot.scope() as session:
        await set_channel_ids(session, G, player_registry=777)
    ctx = await load_flow_context(bot.scope, G, "marvel_rivals")
    flow = RegistrationFlow(bot, ctx, RegState(G, 500, "marvel_rivals"))
    it = FakeInteraction()
    await flow.show(it)
    assert flow.step == "name"

    modal = NameModal(flow)
    modal.name_input._value = "Tester#1"
    await modal.on_submit(it)
    assert flow.step == "region"
    it = await choose(it.view, ":region", "EMEA")
    assert flow.step == "current_rank"
    it = await choose(it.view, ":current_tier", "Gold")
    assert flow.pending_tier == "Gold"  # a division is still needed
    it = await choose(it.view, ":current_div", "II")
    assert flow.step == "peak_rank"
    it = await choose(it.view, ":peak_tier", "Eternity")  # no division for this tier
    assert flow.step == "roles"
    pref_it = await choose(it.view, ":role_pref", "duelist")
    await choose(pref_it.view, ":role_ok", "vanguard", "strategist")
    cont = pick(flow._build()[1], ":roles_done")
    it = FakeInteraction()
    await cont.callback(it)
    assert flow.step == "heroes"
    hero_it = await choose(it.view, ":heroes", "Hulk", "Thor")
    go = pick(hero_it.view, ":heroes_done")
    it = FakeInteraction()
    await go.callback(it)
    assert flow.step == "activity"
    it = await choose(it.view, ":activity", "either")
    assert flow.step == "review"
    text = it.embed.description
    assert "Tester#1" in text and "Gold II" in text and "Eternity" in text and "Duelist" in text

    save = pick(it.view, ":save")
    final = FakeInteraction()
    await save.callback(final)
    assert "saved" in final.embed.title.lower()

    async with bot.scope() as session:
        profile = (await session.execute(select(GameProfile))).scalar_one()
        assert (profile.current_rank_tier, profile.current_division) == ("Gold", "II")
        assert (profile.peak_rank_tier, profile.peak_division) == ("Eternity", None)
        assert profile.preferred_roles == ["duelist"] and set(profile.acceptable_roles) == {
            "vanguard",
            "strategist",
        }
        assert profile.preferred_heroes == ["Hulk", "Thor"] and profile.activity == "either"
        assert (await session.execute(select(RegistryMessage))).scalar_one().channel_id == 777
    assert len(adapter.messages) == 1  # the registry entry exists on "Discord"


@pytest.mark.asyncio
async def test_peak_below_current_is_rejected(session_factory):
    bot = Bot(session_factory, FakeDiscordAdapter())
    ctx = await load_flow_context(bot.scope, G, "marvel_rivals")
    flow = RegistrationFlow(
        bot, ctx, RegState(G, 500, "marvel_rivals", ingame_name="x", region="EMEA")
    )
    flow.idx = ctx.steps.index("current_rank")
    it = FakeInteraction()
    await flow.show(it)
    it = await choose(it.view, ":current_tier", "Gold")
    it = await choose(it.view, ":current_div", "I")
    it = await choose(it.view, ":peak_tier", "Bronze")
    it = await choose(it.view, ":peak_div", "III")
    assert flow.step == "peak_rank" and flow.state.peak_tier is None
    assert any("lower" in (m or "") for m in it.followups)


@pytest.mark.asyncio
async def test_wizard_is_owner_only(session_factory):
    bot = Bot(session_factory, FakeDiscordAdapter())
    ctx = await load_flow_context(bot.scope, G, "marvel_rivals")
    flow = RegistrationFlow(bot, ctx, RegState(G, 500, "marvel_rivals"))
    _, view = flow._build()
    assert await view.interaction_check(FakeInteraction(user_id=500)) is True
    assert await view.interaction_check(FakeInteraction(user_id=999)) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("game", GAMES)
async def test_state_to_fields_passes_profile_validation_for_every_game(session_factory, game):
    """The wizard's output must be accepted by the real ProfileService for each game."""
    scope = make_session_scope(session_factory)
    ctx = await load_flow_context(scope, G, game)
    pool = ctx.rank_pools[0]
    options = ctx.ranks[pool]
    tier = options.tiers[2]
    div = options.divisions[tier][-1] if options.needs_division(tier) else None
    state = RegState(
        G,
        600,
        game,
        ingame_name="Tester",
        region=ctx.game.regions[0],
        queue_type=pool,
        cur_tier=tier,
        cur_div=div,
        peak_tier=tier,
        peak_div=div,
        preferred_role=(ctx.roles[0]["key"] if ctx.roles else None),
        heroes=ctx.heroes[:2],
        playstyle=(ctx.roles_and_pool.playstyles.get(game) or [None])[0],
        platform=(ctx.game.platforms[0] if len(ctx.game.platforms) > 1 else None),
        activity=(ctx.game.activities[0] if len(ctx.game.activities) > 1 else None),
    )
    pid = await save_registration(scope, None, state, ctx.steps)
    async with scope() as session:
        profile = await session.get(GameProfile, pid)
        assert profile.game == game and profile.current_rank_tier == tier
    # editing again updates instead of failing
    state.existing = True
    await save_registration(scope, None, state, ctx.steps)


@pytest.mark.asyncio
async def test_deleting_a_profile_removes_the_registry_message(session_factory):
    adapter = FakeDiscordAdapter()
    bot = Bot(session_factory, adapter)
    async with bot.scope() as session:
        await set_channel_ids(session, G, player_registry=777)
    ctx = await load_flow_context(bot.scope, G, "marvel_rivals")
    st = RegState(
        G,
        500,
        "marvel_rivals",
        ingame_name="T",
        region="EMEA",
        cur_tier="Gold",
        cur_div="II",
        peak_tier="Gold",
        peak_div="II",
        preferred_role="duelist",
    )
    await save_registration(bot.scope, bot.registry_syncer, st, ctx.steps)
    assert len(adapter.messages) == 1
    bot.dispatcher = None
    assert await delete_registration(bot, G, 500, "marvel_rivals")
    assert adapter.messages == {}
    async with bot.scope() as session:
        assert (await session.execute(select(GameProfile))).scalars().all() == []


@pytest.mark.asyncio
async def test_editing_a_profile_edits_the_same_registry_message(session_factory):
    adapter = FakeDiscordAdapter()
    bot = Bot(session_factory, adapter)
    async with bot.scope() as session:
        await set_channel_ids(session, G, player_registry=777)
    ctx = await load_flow_context(bot.scope, G, "marvel_rivals")
    st = RegState(
        G,
        500,
        "marvel_rivals",
        ingame_name="T",
        region="EMEA",
        cur_tier="Gold",
        cur_div="II",
        peak_tier="Gold",
        peak_div="II",
        preferred_role="duelist",
    )
    await save_registration(bot.scope, bot.registry_syncer, st, ctx.steps)
    st.cur_div, st.peak_div = "I", "I"
    await save_registration(bot.scope, bot.registry_syncer, st, ctx.steps)
    assert len(adapter.messages) == 1
    embed = next(iter(adapter.messages.values()))["embed"]
    assert any("Gold I" in v for _, v, _ in embed.fields)


@pytest.mark.asyncio
async def test_lost_registry_message_is_reposted(session_factory):
    adapter = FakeDiscordAdapter()
    bot = Bot(session_factory, adapter)
    async with bot.scope() as session:
        await set_channel_ids(session, G, player_registry=777)
    ctx = await load_flow_context(bot.scope, G, "marvel_rivals")
    st = RegState(
        G,
        500,
        "marvel_rivals",
        ingame_name="T",
        region="EMEA",
        cur_tier="Gold",
        cur_div="II",
        peak_tier="Gold",
        peak_div="II",
        preferred_role="duelist",
    )
    await save_registration(bot.scope, bot.registry_syncer, st, ctx.steps)
    adapter.messages.clear()  # someone deleted it by hand
    stats = await bot.registry_syncer.reconcile(G)
    assert stats["synced"] == 1 and len(adapter.messages) == 1


@pytest.mark.asyncio
async def test_availability_is_hidden_unless_the_member_opts_in(session_factory):
    adapter = FakeDiscordAdapter()
    bot = Bot(session_factory, adapter)
    async with bot.scope() as session:
        await set_channel_ids(session, G, player_registry=777)
    ctx = await load_flow_context(bot.scope, G, "marvel_rivals")
    st = RegState(
        G,
        500,
        "marvel_rivals",
        ingame_name="T",
        region="EMEA",
        cur_tier="Gold",
        cur_div="II",
        peak_tier="Gold",
        peak_div="II",
        preferred_role="duelist",
    )
    await save_registration(bot.scope, bot.registry_syncer, st, ctx.steps)
    labels = lambda: [n for n, _, _ in next(iter(adapter.messages.values()))["embed"].fields]  # noqa: E731
    assert "Availability" not in labels()
    st.show_availability = True
    await save_registration(bot.scope, bot.registry_syncer, st, ctx.steps)
    assert "Availability" in labels()


def test_review_and_registry_formatting_smoke():
    st = RegState(
        G,
        1,
        "marvel_rivals",
        ingame_name="Name",
        region="EMEA",
        cur_tier="Gold",
        cur_div="II",
        peak_tier="Gold",
        peak_div="II",
        preferred_role="duelist",
    )
    rows = review_lines(st, ["name", "region", "current_rank", "peak_rank", "roles"], RankOptions())
    assert ("In-game name", "Name") in rows
    assert format_profile is not None and ProfileService is not None
