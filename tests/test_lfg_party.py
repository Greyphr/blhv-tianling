"""Fix 7: parties, guests, invites, LFG wizard, test mode, status."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.helpers import GOLD_II
from tests.test_registration_wizard import FakeInteraction, choose, pick
from tianling.core.party_slots import open_slots, role_overflow, slots_text
from tianling.core.seed_loader import SeedLoader
from tianling.core.timeutil import utcnow
from tianling.db.models import MatchGroup, MatchMember, PartyMember, QueueEntry, ScheduledJob
from tianling.db.repositories.config import GuildConfigRepository
from tianling.db.session import make_session_scope
from tianling.discord_ui.dispatcher import Dispatcher
from tianling.discord_ui.fake_gateway import FakeGateway
from tianling.discord_ui.lfg import LFGFlow, LFGState, StatusView, status_embed
from tianling.discord_ui.lfg_flow import (
    MemberSpec,
    create_party_from_specs,
    guest_spec,
    party_slot_summary,
    queue_party,
    queue_solo,
)
from tianling.discord_ui.notifications import NotificationService
from tianling.discord_ui.registration_flow import RankOptions
from tianling.discord_ui.voice import VoiceManager
from tianling.scheduler import Scheduler
from tianling.services.events import PartyInvite
from tianling.services.profile_service import ProfileService
from tianling.services.queue_service import (
    InvalidQueueRequest,
    PartyNotReady,
    QueueService,
)
from tianling.services.settings import MatchSettings, set_channel_ids
from tianling.services.test_mode import TestMode

G = 1
COMP = next(
    c
    for c in SeedLoader().load_compositions().compositions
    if c.game == "marvel_rivals" and c.activity == "competitive"
)


async def make_profile(scope, member_id, role="duelist", game="marvel_rivals", region="EMEA"):
    async with scope() as session:
        await ProfileService(session, G).create_profile(
            member_id,
            game,
            ingame_name=f"P{member_id}",
            region=region,
            current_rank_tier="Gold",
            current_division="II",
            peak_rank_tier="Gold",
            peak_division="II",
            preferred_roles=[role],
        )


# ------------------------------------------------------------ pure slot maths


def test_open_slots_and_overflow():
    assert open_slots(COMP, ["duelist"]) == {"vanguard": 2, "duelist": 1, "strategist": 2}
    assert open_slots(COMP, ["duelist", "duelist", "duelist"])["duelist"] == 0
    assert role_overflow(COMP, ["duelist"] * 3) == {"duelist": 1}
    assert role_overflow(COMP, ["vanguard", "duelist", None]) == {}
    assert slots_text({"vanguard": 2, "duelist": 0}, {"vanguard": "Vanguard"}) == "2 Vanguard"
    assert "none" in slots_text({"duelist": 0})


def test_party_slot_summary_text():
    text = party_slot_summary(
        "marvel_rivals", "competitive", [MemberSpec(1, "duelist"), MemberSpec(2, "vanguard")]
    )
    assert "4 open seat" in text and "Strategist" in text


# -------------------------------------------------------------------- guests


def _options():

    from tianling.db.repositories.ranks import RankRepository  # noqa: F401

    return None


@pytest.mark.asyncio
async def test_guest_validation(session_factory):
    from tianling.db.repositories.ranks import RankRepository

    async with session_factory() as session:
        opts = RankOptions.from_definitions(
            await RankRepository(session).list_for(G, "marvel_rivals", "default")
        )
    roles = SeedLoader().load_roles_and_pool().roles["marvel_rivals"]
    g = guest_spec(opts, roles, tier="gold", division="ii", role="Duelist")
    assert g.is_guest and g.member_id < 0 and g.role == "duelist" and g.snapshot["tier"] == "Gold"
    with pytest.raises(InvalidQueueRequest, match="division"):
        guest_spec(opts, roles, tier="Gold", division=None, role="duelist")
    with pytest.raises(InvalidQueueRequest, match="Unknown rank"):
        guest_spec(opts, roles, tier="Mythic", division="I", role="duelist")
    with pytest.raises(InvalidQueueRequest, match="Unknown role"):
        guest_spec(opts, roles, tier="Gold", division="I", role="healer")
    assert (
        guest_spec(opts, roles, tier="Eternity", division=None, role="duelist").snapshot["division"]
        is None
    )


# ------------------------------------------------------- service-level checks


async def _party(qs, roles, ranks=None, size=None):
    ids = list(range(1, len(roles) + 1))
    return await qs.create_party(
        G,
        1,
        ids,
        declared_roles=dict(zip(ids, roles, strict=True)),
        rank_snapshots={i: (ranks or {}).get(i, GOLD_II) for i in ids},
    )


@pytest.mark.asyncio
async def test_party_role_overflow_is_rejected(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        pid = await _party(qs, ["duelist", "duelist", "duelist"])
        with pytest.raises(InvalidQueueRequest, match="too many duelist"):
            await qs.join_queue(G, 1, "marvel_rivals", "competitive", party_id=pid)


@pytest.mark.asyncio
async def test_party_without_roles_is_rejected(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        pid = await _party(qs, ["duelist", None])
        with pytest.raises(InvalidQueueRequest, match="needs a role"):
            await qs.join_queue(G, 1, "marvel_rivals", "competitive", party_id=pid)


@pytest.mark.asyncio
async def test_party_larger_than_activity_allows_is_rejected(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        pid = await _party(
            qs,
            [
                "vanguard",
                "vanguard",
                "duelist",
                "duelist",
                "strategist",
                "strategist",
                "strategist",
            ],
        )
        with pytest.raises(InvalidQueueRequest, match="too large|too many"):
            await qs.join_queue(G, 1, "marvel_rivals", "competitive", party_id=pid)


@pytest.mark.asyncio
async def test_party_with_a_huge_rank_spread_is_rejected(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        pid = await _party(
            qs,
            ["duelist", "vanguard"],
            ranks={2: {"tier": "Bronze", "division": "III"}, 1: {"tier": "Eternity"}},
        )
        with pytest.raises(InvalidQueueRequest, match="too far apart"):
            await qs.join_queue(G, 1, "marvel_rivals", "competitive", party_id=pid)


@pytest.mark.asyncio
async def test_open_slots_are_stored_on_the_queue_entry(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        pid = await _party(qs, ["duelist", "vanguard"])
        entry = await qs.join_queue(G, 1, "marvel_rivals", "competitive", party_id=pid)
        assert entry.open_slots == {"vanguard": 1, "duelist": 1, "strategist": 2}


# ---------------------------------------------------------------- invites


@pytest.mark.asyncio
async def test_invited_member_must_accept_before_party_can_queue(session_factory):
    scope = make_session_scope(session_factory)
    await make_profile(scope, 10, "vanguard")
    await make_profile(scope, 11, "duelist")
    specs = [MemberSpec(10, "vanguard", GOLD_II), MemberSpec(11, "duelist", GOLD_II)]
    party_id, events = await create_party_from_specs(scope, G, 10, "marvel_rivals", specs)
    invites = [e for e in events if isinstance(e, PartyInvite)]
    assert [(e.invited_member_id, e.leader_member_id) for e in invites] == [(11, 10)]
    with pytest.raises(PartyNotReady, match="<@11>"):
        await queue_party(scope, G, 10, party_id, "marvel_rivals", "competitive", "EMEA")
    async with scope() as session:
        assert await QueueService(session).respond_party_invite(G, party_id, 11, True)
    status, _ = await queue_party(scope, G, 10, party_id, "marvel_rivals", "competitive", "EMEA")
    assert status == "queued"
    async with scope() as session:
        entry = (await session.execute(select(QueueEntry))).scalar_one()
        assert entry.party_id == party_id and entry.party_size == 2 and entry.leader_member_id == 10


@pytest.mark.asyncio
async def test_declined_or_expired_invites_remove_the_member(session_factory):
    scope = make_session_scope(session_factory)
    specs = [
        MemberSpec(10, "vanguard", GOLD_II),
        MemberSpec(11, "duelist", GOLD_II),
        MemberSpec(12, "duelist", GOLD_II),
    ]
    party_id, _ = await create_party_from_specs(scope, G, 10, "marvel_rivals", specs)
    async with scope() as session:
        qs = QueueService(session)
        await qs.respond_party_invite(G, party_id, 11, False)  # declines
        for pm in (
            await session.execute(select(PartyMember).where(PartyMember.member_id == 12))
        ).scalars():
            pm.invite_expires_at = utcnow() - timedelta(seconds=1)
        await session.flush()
        assert await qs.expire_party_invites() == 1
        left = [m.member_id for m in (await session.execute(select(PartyMember))).scalars()]
        assert left == [10]
        # a stale click on an expired invite does nothing
        assert not await qs.respond_party_invite(G, party_id, 12, True)


@pytest.mark.asyncio
async def test_leaving_a_party_cancels_its_queue_entry(session_factory):
    scope = make_session_scope(session_factory)
    await make_profile(scope, 10, "vanguard")
    specs = [MemberSpec(10, "vanguard", GOLD_II), MemberSpec(-5, "duelist", GOLD_II, is_guest=True)]
    party_id, _ = await create_party_from_specs(scope, G, 10, "marvel_rivals", specs)
    await queue_party(scope, G, 10, party_id, "marvel_rivals", "competitive", "EMEA")
    async with scope() as session:
        await QueueService(session).leave_party(G, 10)
        assert (await session.execute(select(QueueEntry))).scalar_one().status == "cancelled"


# --------------------------------------------------------------- solo queue


@pytest.mark.asyncio
async def test_solo_queue_requires_a_profile_and_uses_it(session_factory):
    scope = make_session_scope(session_factory)
    with pytest.raises(InvalidQueueRequest, match="profile"):
        await queue_solo(scope, G, 20, "marvel_rivals", "competitive", "EMEA")
    await make_profile(scope, 20, "strategist")
    status, _ = await queue_solo(scope, G, 20, "marvel_rivals", "competitive", None)
    assert status == "queued"
    async with scope() as session:
        e = (await session.execute(select(QueueEntry))).scalar_one()
        assert e.role_preference == "strategist" and e.region == "EMEA"
        assert e.rank_snapshot["tier"] == "Gold"


@pytest.mark.asyncio
async def test_either_activity_queues_both(session_factory):
    scope = make_session_scope(session_factory)
    async with scope() as session:
        await ProfileService(session, G).create_profile(
            30,
            "deadlock",
            ingame_name="D",
            region="EMEA",
            current_rank_tier="Initiate",
            current_division="I",
            peak_rank_tier="Initiate",
            peak_division="I",
            activity="either",
        )
    await queue_solo(scope, G, 30, "deadlock", "either", "EMEA")
    async with scope() as session:
        acts = {e.activity for e in (await session.execute(select(QueueEntry))).scalars()}
    assert acts == {"ranked", "standard"}


# ----------------------------------------------------------------- the wizard


class Bot:
    def __init__(self, factory):
        self.scope = make_session_scope(factory)
        self.gateway = FakeGateway()
        self.voice = VoiceManager(self.gateway, self.scope)
        self.notifier = NotificationService(self.gateway, self.scope, cooldown_seconds=0)
        self.dispatcher = Dispatcher(self.gateway, self.scope, self.voice, self.notifier)
        self.scheduler = Scheduler(self.scope, dispatcher=self.dispatcher, voice=self.voice)


@pytest.mark.asyncio
async def test_lfg_wizard_solo_flow(session_factory):
    bot = Bot(session_factory)
    await make_profile(bot.scope, 500, "duelist")
    flow = LFGFlow(bot, LFGState(G, 500))
    it = FakeInteraction()
    await flow.step_game(it)
    it = await choose(it.view, ":game", "marvel_rivals")
    assert [o.value for o in pick(it.view, ":activity").options] == [
        "competitive",
        "casual",
        "either",
    ]
    it = await choose(it.view, ":activity", "competitive")
    region = pick(it.view, ":region")
    assert [o.value for o in region.options if o.default] == ["EMEA"]  # profile region preselected
    it = await choose(it.view, ":region", "EMEA")
    solo = pick(it.view, ":solo")
    it = FakeInteraction()
    await solo.callback(it)
    join = pick(it.view, ":join_solo")
    final = FakeInteraction()
    await join.callback(final)
    assert "queued" in final.embed.title.lower()
    async with bot.scope() as session:
        assert (await session.execute(select(QueueEntry))).scalar_one().member_id == 500


@pytest.mark.asyncio
async def test_lfg_wizard_without_profile_points_to_registration(session_factory):
    bot = Bot(session_factory)
    flow = LFGFlow(bot, LFGState(G, 501))
    it = FakeInteraction(501)
    await flow.step_game(it)
    assert "profile" in it.embed.description.lower() and it.view is None


@pytest.mark.asyncio
async def test_lfg_wizard_party_with_guest_then_queue(session_factory):
    bot = Bot(session_factory)
    await make_profile(bot.scope, 500, "vanguard")
    flow = LFGFlow(
        bot,
        LFGState(
            G, 500, game="marvel_rivals", activity="competitive", region="EMEA", party_mode=True
        ),
    )
    it = FakeInteraction()
    await flow.step_party(it)
    assert "Open:" in it.embed.description and pick(it.view, ":create").disabled  # needs 2+ members
    await flow.add_guest(it, "Gold", "II", "Duelist")
    assert len(flow.state.specs) == 2 and "Guest" in flow._party_text()
    await flow.add_guest(it, "Mythic", "I", "Duelist")  # invalid rank -> error, nothing added
    assert len(flow.state.specs) == 2 and "Unknown rank" in it.followups[-1]
    it2 = FakeInteraction()
    await flow.step_party(it2)
    create = pick(it2.view, ":create")
    assert not create.disabled
    it3 = FakeInteraction()
    await create.callback(it3)
    join = pick(it3.view, ":join_party")
    final = FakeInteraction()
    await join.callback(final)
    assert "queued" in final.embed.title.lower()
    async with bot.scope() as session:
        entry = (await session.execute(select(QueueEntry))).scalar_one()
        assert entry.party_size == 2
        guests = (
            (await session.execute(select(PartyMember).where(PartyMember.is_guest.is_(True))))
            .scalars()
            .all()
        )
        assert len(guests) == 1 and guests[0].declared_role == "duelist"


@pytest.mark.asyncio
async def test_lfg_menu_is_owner_only(session_factory):
    bot = Bot(session_factory)
    await make_profile(bot.scope, 500)
    flow = LFGFlow(bot, LFGState(G, 500))
    it = FakeInteraction()
    await flow.step_game(it)
    assert await it.view.interaction_check(FakeInteraction(999)) is False


# ---------------------------------------------------------- status & leaving


@pytest.mark.asyncio
async def test_status_embed_and_leave_button(session_factory):
    bot = Bot(session_factory)
    await make_profile(bot.scope, 40)
    await queue_solo(bot.scope, G, 40, "marvel_rivals", "competitive", "EMEA")
    async with bot.scope() as session:
        statuses = await QueueService(session).get_status(G, 40)
    embed = status_embed(statuses)
    assert "Searching" in embed.fields[0].value and "Marvel Rivals" in embed.fields[0].name
    assert "not in any queue" in status_embed([]).description
    view = StatusView(bot, G, 40, statuses)
    it = FakeInteraction(40)
    await view.children[0].callback(it)
    async with bot.scope() as session:
        assert await QueueService(session).get_status(G, 40) == []


# ------------------------------------------------------------------ test mode


@pytest.mark.asyncio
async def test_test_mode_autoaccept_runs_through_the_scheduler(session_factory):
    bot = Bot(session_factory)
    async with bot.scope() as session:
        await GuildConfigRepository(session).update_value(
            G, "matching", confirmation_timeout_seconds=300
        )
        await GuildConfigRepository(session).set(
            G, "test_mode", {"auto_accept": True, "delay_seconds": 0}
        )
        await set_channel_ids(session, G, matchmaking_alerts=500)
        events = await TestMode(session, G).fill(
            "marvel_rivals",
            "competitive",
            6,
            roles=["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"],
        )
    await bot.dispatcher.dispatch(events)
    async with bot.scope() as session:
        assert (await session.execute(select(MatchGroup))).scalar_one().status == "proposed"
        jobs = [j.job_type for j in (await session.execute(select(ScheduledJob))).scalars()]
        assert "test_auto_accept" in jobs
    await bot.scheduler.tick()
    async with bot.scope() as session:
        group = (await session.execute(select(MatchGroup))).scalar_one()
        members = (await session.execute(select(MatchMember))).scalars().all()
        assert group.status == "finalized" and all(
            m.is_test and m.response == "accepted" for m in members
        )
    assert bot.gateway.dms == []  # fake players are never DM'd
    assert any("Match Found" in c.title for _, c in bot.gateway.sent)


@pytest.mark.asyncio
async def test_test_mode_decline_probability_exercises_replacement(session_factory):
    bot = Bot(session_factory)
    async with bot.scope() as session:
        await GuildConfigRepository(session).set(
            G, "test_mode", {"auto_accept": True, "delay_seconds": 0, "decline_probability": 1.0}
        )
        await TestMode(session, G, MatchSettings(cooldown_seconds=0)).fill(
            "marvel_rivals",
            "competitive",
            6,
            roles=["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"],
        )
    await bot.scheduler.tick()
    async with bot.scope() as session:
        group = (await session.execute(select(MatchGroup))).scalars().first()
        assert group.status == "cancelled"  # real decline path ran
        assert (
            (await session.execute(select(QueueEntry).where(QueueEntry.status == "cancelled")))
            .scalars()
            .first()
        )


@pytest.mark.asyncio
async def test_test_mode_clear_removes_everything(session_factory):
    async with session_factory() as session:
        tm = TestMode(session, G)
        await tm.fill(
            "marvel_rivals",
            "competitive",
            6,
            roles=["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"],
        )
        await tm.clear_all()
        assert (await session.execute(select(QueueEntry))).scalars().all() == []
        assert (await session.execute(select(MatchGroup))).scalars().all() == []
        assert (await session.execute(select(MatchMember))).scalars().all() == []


# ---------------------------------------------------------- still looking?


@pytest.mark.asyncio
async def test_still_looking_prompt_and_extension(session_factory):
    async with session_factory() as session:
        qs = QueueService(
            session, MatchSettings(queue_expiry_minutes=30, still_looking_lead_minutes=5)
        )
        await qs.join_queue(G, 70, "marvel_rivals", "competitive", rank_snapshot=GOLD_II)
        entry = (await session.execute(select(QueueEntry))).scalar_one()
        entry.expires_at = utcnow() + timedelta(minutes=3)
        await qs.expire_entries()
        kinds = [type(e).__name__ for e in qs.pop_events()]
        assert kinds == ["StillLooking"]
        await qs.expire_entries()  # not repeated
        assert qs.pop_events() == []
        assert await qs.extend_entry(G, 70, "marvel_rivals", "competitive")
        assert entry.expires_at > utcnow() + timedelta(minutes=25) and not entry.still_looking_sent


_ = SimpleNamespace
