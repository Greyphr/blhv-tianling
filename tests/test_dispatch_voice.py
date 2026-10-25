"""Fix 4: dispatcher, voice manager and scheduler against the fake gateway."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.helpers import join_marvel_solos
from tianling.core.timeutil import utcnow
from tianling.db.models import MatchGroup, ScheduledJob, TemporaryChannel
from tianling.db.repositories.config import GuildConfigRepository
from tianling.db.session import make_session_scope
from tianling.discord_ui.dispatcher import Dispatcher
from tianling.discord_ui.fake_gateway import FakeGateway
from tianling.discord_ui.notifications import NotificationService
from tianling.discord_ui.retry import is_retryable, with_retry
from tianling.discord_ui.voice import VoiceManager
from tianling.scheduler import Scheduler
from tianling.services.confirmation_service import ConfirmationService
from tianling.services.queue_service import QueueService
from tianling.services.settings import MatchSettings

G = 1
ALERTS = 500
ALL = [1, 2, 3, 4, 5, 6]


class Env:
    def __init__(self, factory):
        self.scope = make_session_scope(factory)
        self.gateway = FakeGateway()
        self.voice = VoiceManager(self.gateway, self.scope)
        self.notifier = NotificationService(self.gateway, self.scope, cooldown_seconds=0)
        self.dispatcher = Dispatcher(self.gateway, self.scope, self.voice, self.notifier)
        self.scheduler = Scheduler(self.scope, dispatcher=self.dispatcher, voice=self.voice)

    async def configure(self, **voice):
        async with self.scope() as session:
            cfg = GuildConfigRepository(session)
            await cfg.set(G, "channels", {"matchmaking_alerts": ALERTS})
            await cfg.set(
                G, "voice", {"category_id": 77, "grace_seconds": 60, "mod_role_ids": [9], **voice}
            )

    async def queue_six(self):
        async with self.scope() as session:
            qs = QueueService(session, MatchSettings())
            await join_marvel_solos(qs, ALL)
            events = qs.pop_events()
        await self.dispatcher.dispatch(events)

    async def group(self) -> MatchGroup:
        async with self.scope() as session:
            return (
                (await session.execute(select(MatchGroup).order_by(MatchGroup.id)))
                .scalars()
                .first()
            )

    async def accept(self, member_id: int):
        grp = await self.group()
        async with self.scope() as session:
            cs = ConfirmationService(session, MatchSettings())
            res = await cs.accept(G, grp.group_uuid, member_id)
            events = cs.pop_events()
        await self.dispatcher.dispatch(events)
        return res

    async def finalize(self):
        for m in ALL:
            await self.accept(m)


@pytest.fixture
async def env(session_factory):
    e = Env(session_factory)
    await e.configure()
    return e


@pytest.mark.asyncio
async def test_proposal_is_one_message_edited_in_place(env):
    await env.queue_six()
    grp = await env.group()
    assert grp.channel_id == ALERTS and grp.message_id is not None
    key = (grp.channel_id, grp.message_id)
    first = env.gateway.messages[key]
    assert "not confirmed" in first.title and first.proposal_group == grp.group_uuid
    assert first.buttons_enabled
    await env.accept(1)
    await env.accept(2)
    assert len([c for c in env.gateway.sent if c[1].proposal_group]) == 1  # never reposted
    assert "✅" in env.gateway.messages[key].fields[0][1]
    assert env.gateway.messages[key].title == first.title  # still not confirmed


@pytest.mark.asyncio
async def test_finalization_creates_a_private_voice_channel_and_announces(env):
    await env.queue_six()
    await env.finalize()
    grp = await env.group()
    assert grp.status == "finalized" and grp.voice_channel_id in env.gateway.voices
    voice = env.gateway.voices[grp.voice_channel_id]
    assert voice.name == "⚔️・marvel-rivals-01"  # readable per-game counter, not a uuid slice
    assert voice.category_id == 77 and voice.mod_role_ids == [9]
    assert sorted(voice.member_ids) == ALL  # every roster member
    msg = env.gateway.messages[(grp.channel_id, grp.message_id)]
    assert "Confirmed" in msg.title and not msg.buttons_enabled  # buttons disabled
    announce = [c for _, c in env.gateway.sent if "Match Found" in c.title]
    assert len(announce) == 1 and f"<#{voice.channel_id}>" in announce[0].description
    async with env.scope() as session:
        assert (
            await session.execute(select(TemporaryChannel))
        ).scalar_one().channel_id == voice.channel_id
    assert {m for m, _ in env.gateway.dms} == set(ALL)  # DM'd (everyone is opted in by default)


@pytest.mark.asyncio
async def test_second_group_gets_the_next_counter(env):
    await env.queue_six()
    await env.finalize()
    async with env.scope() as session:
        qs = QueueService(session, MatchSettings(auto_confirm=True))
        await join_marvel_solos(qs, [11, 12, 13, 14, 15, 16])
        events = qs.pop_events()
    await env.dispatcher.dispatch(events)
    assert sorted(v.name for v in env.gateway.voices.values()) == [
        "⚔️・marvel-rivals-01",
        "⚔️・marvel-rivals-02",
    ]


async def _finalized_with_channel(env):
    await env.queue_six()
    await env.finalize()
    grp = await env.group()
    return grp, env.gateway.voices[grp.voice_channel_id]


@pytest.mark.asyncio
async def test_empty_channel_is_deleted_only_after_the_grace_period(env):
    grp, voice = await _finalized_with_channel(env)
    cid = voice.channel_id
    voice.occupants = {1, 2}
    assert await env.voice.on_voice_update(G, cid) in {"active", "deletion_cancelled"}
    voice.occupants = set()
    assert await env.voice.on_voice_update(G, cid) == "grace_started"
    # Job exists but is not due yet: nothing happens, and an early run does not delete.
    assert await env.scheduler.tick() == 0
    assert await env.voice.cleanup_if_empty(G, cid) == "not_yet"
    assert cid in env.gateway.voices
    # Time passes: make the grace period elapse.
    async with env.scope() as session:
        tc = (await session.execute(select(TemporaryChannel))).scalar_one()
        tc.empty_since = utcnow() - timedelta(seconds=120)
        for job in (
            await session.execute(
                select(ScheduledJob).where(
                    ScheduledJob.job_type == "empty_channel_cleanup",
                    ScheduledJob.status == "pending",
                )
            )
        ).scalars():
            job.run_at = utcnow() - timedelta(seconds=1)
    await env.scheduler.tick()
    assert cid not in env.gateway.voices and env.gateway.deleted_channels == [cid]
    async with env.scope() as session:
        assert (await session.execute(select(TemporaryChannel))).scalars().all() == []
        assert (await session.execute(select(MatchGroup))).scalars().first().status == "completed"


@pytest.mark.asyncio
async def test_rejoining_cancels_the_deletion(env):
    grp, voice = await _finalized_with_channel(env)
    cid = voice.channel_id
    voice.occupants = set()
    assert await env.voice.on_voice_update(G, cid) == "grace_started"
    voice.occupants = {3}  # someone comes back before the grace is over
    assert await env.voice.on_voice_update(G, cid) == "deletion_cancelled"
    async with env.scope() as session:
        pending = (
            (
                await session.execute(
                    select(ScheduledJob).where(
                        ScheduledJob.job_type == "empty_channel_cleanup",
                        ScheduledJob.status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert pending == []
        for job in (
            await session.execute(
                select(ScheduledJob).where(ScheduledJob.job_type == "empty_channel_cleanup")
            )
        ).scalars():
            job.run_at = utcnow() - timedelta(seconds=1)
    await env.scheduler.tick()
    assert cid in env.gateway.voices  # still there


@pytest.mark.asyncio
async def test_a_stale_cleanup_job_rechecks_occupancy_at_run_time(env):
    grp, voice = await _finalized_with_channel(env)
    voice.occupants = {4}  # occupied when the (creation-time) cleanup job fires
    assert await env.voice.cleanup_if_empty(G, voice.channel_id) == "occupied"
    assert voice.channel_id in env.gateway.voices


@pytest.mark.asyncio
async def test_channels_we_did_not_create_are_never_touched(env):
    env.gateway.voices[4242] = type(
        next(iter(env.gateway.voices.values()), None)
        or __import__("tianling.discord_ui.fake_gateway", fromlist=["FakeVoice"]).FakeVoice(
            4242, "general", None, [], []
        )
    )(4242, "general", None, [], [])
    assert await env.voice.on_voice_update(G, 4242) == "untracked"
    assert await env.voice.cleanup_if_empty(G, 4242) == "untracked"
    assert 4242 in env.gateway.voices


@pytest.mark.asyncio
async def test_manually_deleted_channel_is_reconciled(env):
    grp, voice = await _finalized_with_channel(env)
    del env.gateway.voices[voice.channel_id]
    stats = await env.voice.reconcile(G)
    assert stats["removed"] == 1
    async with env.scope() as session:
        assert (await session.execute(select(TemporaryChannel))).scalars().all() == []


@pytest.mark.asyncio
async def test_voice_failure_falls_back_to_shared_channel_and_alerts_admins(env):
    await env.configure(fallback_channel_id=888)
    env.gateway.fail_voice_creation = True
    await env.queue_six()
    await env.finalize()
    grp = await env.group()
    assert grp.status == "finalized"  # the group is retained
    assert grp.voice_channel_id == 888
    announce = [c for _, c in env.gateway.sent if "Match Found" in c.title][0]
    assert "<#888>" in announce.description
    problem = [c for _, c in env.gateway.sent if "Voice channel problem" in c.title]
    assert problem and "<@&9>" in problem[0].content  # moderators are pinged
    async with env.scope() as session:  # the fallback channel is NOT recorded, so never deleted
        assert (await session.execute(select(TemporaryChannel))).scalars().all() == []


@pytest.mark.asyncio
async def test_voice_failure_without_fallback_still_confirms_and_notifies(env):
    env.gateway.fail_voice_creation = True
    await env.queue_six()
    await env.finalize()
    assert (await env.group()).status == "finalized"
    assert any("Voice channel problem" in c.title for _, c in env.gateway.sent)


@pytest.mark.asyncio
async def test_cancelled_proposal_edits_the_message_and_disables_buttons(env):
    await env.queue_six()
    grp = await env.group()
    async with env.scope() as session:
        cs = ConfirmationService(session, MatchSettings())
        await cs.decline(G, grp.group_uuid, 4)
        events = cs.pop_events()
    await env.dispatcher.dispatch(events)
    card = env.gateway.messages[(grp.channel_id, grp.message_id)]
    assert "Cancelled" in card.title and not card.buttons_enabled
    assert any("No replacement" in c.title for _, c in env.gateway.sent)
    assert any("Cooldown" in c.title.title() or "cooldown" in c.title for _, c in env.gateway.dms)


@pytest.mark.asyncio
async def test_restart_rebuilds_pending_proposals_and_they_still_work(env, session_factory):
    # A proposal exists in the DB, but the bot died before posting/after losing its message.
    async with env.scope() as session:
        qs = QueueService(session, MatchSettings())
        await join_marvel_solos(qs, ALL)
    assert env.gateway.sent == []
    new_env = Env(session_factory)  # "restarted" process: fresh objects
    assert await new_env.dispatcher.reconcile_proposals(G) == 1
    grp = await new_env.group()
    assert (
        grp.message_id is not None
        and new_env.gateway.latest_card().proposal_group == grp.group_uuid
    )
    # An old message that no longer exists is reposted rather than silently broken.
    new_env.gateway.messages.clear()
    await new_env.dispatcher.reconcile_proposals(G)
    assert len(new_env.gateway.messages) == 1
    # And accepting afterwards works from the DB alone.
    for m in ALL:
        res = await new_env.accept(m)
    assert res.finalized


@pytest.mark.asyncio
async def test_scheduler_runs_the_deadline_job_and_dispatches(env):
    await env.queue_six()
    grp = await env.group()
    async with env.scope() as session:
        g = await session.get(MatchGroup, grp.id)
        g.confirmation_deadline = utcnow() - timedelta(seconds=2)
        for job in (
            await session.execute(
                select(ScheduledJob).where(ScheduledJob.job_type == "confirmation_deadline")
            )
        ).scalars():
            job.run_at = utcnow() - timedelta(seconds=1)
    assert await env.scheduler.tick() == 1
    assert (await env.group()).status == "cancelled"
    assert "Cancelled" in env.gateway.messages[(grp.channel_id, grp.message_id)].title
    # running again does not repeat it
    assert await env.scheduler.tick() == 0


@pytest.mark.asyncio
async def test_scheduler_logs_failures_instead_of_swallowing_them(env, caplog):
    async with env.scope() as session:
        session.add(
            ScheduledJob(
                guild_id=G,
                job_type="matching_tick",
                run_at=utcnow(),
                payload={"game": "marvel_rivals", "activity": "competitive"},
            )
        )
        session.add(ScheduledJob(guild_id=G, job_type="boom", run_at=utcnow(), payload={}))

    async def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    original = env.scheduler._handle_job

    async def handle(job_type, guild_id, payload):
        if job_type == "boom":
            await boom()
        return await original(job_type, guild_id, payload)

    env.scheduler._handle_job = handle  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        ran = await env.scheduler.tick()
    assert ran == 1  # the healthy job still ran
    assert "kaboom" in env.scheduler.last_error
    assert any("kaboom" in (r.exc_text or "") or r.exc_info for r in caplog.records)
    async with env.scope() as session:
        statuses = {
            j.job_type: j.status for j in (await session.execute(select(ScheduledJob))).scalars()
        }
    assert statuses["boom"] == "failed" and statuses["matching_tick"] == "completed"


@pytest.mark.asyncio
async def test_jobs_left_running_by_a_crash_are_recovered_on_start(env):
    async with env.scope() as session:
        session.add(
            ScheduledJob(
                guild_id=G, job_type="matching_tick", run_at=utcnow(), payload={}, status="running"
            )
        )
    await env.scheduler.start()
    await env.scheduler.stop()
    async with env.scope() as session:
        job = (await session.execute(select(ScheduledJob))).scalars().first()
        assert job.status in {"pending", "completed"}


@pytest.mark.asyncio
async def test_sweep_runs_matching_and_expiry(env):
    async with env.scope() as session:
        qs = QueueService(session, MatchSettings())
        for m, role in zip(
            ALL,
            ["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"],
            strict=True,
        ):
            await qs.queue.join(
                __import__("tianling.db.models", fromlist=["QueueEntry"]).QueueEntry(
                    guild_id=G,
                    user_id=(await qs.users.get_or_create(G, m)).id,
                    member_id=m,
                    game="marvel_rivals",
                    activity="competitive",
                    region="EMEA",
                    role_preference=role,
                    rank_snapshot={"tier": "Gold", "division": "II"},
                    queued_at=utcnow(),
                    expires_at=utcnow() + timedelta(minutes=30),
                    party_size=1,
                )
            )
    await env.scheduler.tick(force_sweep=True)  # matching_tick for every waiting game/activity
    assert (await env.group()) is not None


@pytest.mark.asyncio
async def test_queue_expiry_notifies_the_member(env):
    async with env.scope() as session:
        qs = QueueService(session, MatchSettings())
        await qs.join_queue(
            G, 77, "marvel_rivals", "competitive", rank_snapshot={"tier": "Gold", "division": "II"}
        )
        entry = (await qs.queue.list_active(G, "marvel_rivals", "competitive"))[0]
        entry.expires_at = utcnow() - timedelta(seconds=1)
    await env.scheduler.tick(force_sweep=True)
    assert any(m == 77 and "expired" in c.title for m, c in env.gateway.dms)


@pytest.mark.asyncio
async def test_test_players_are_never_dmd_or_pinged(env):
    from tianling.services.test_mode import TestMode

    async with env.scope() as session:
        tm = TestMode(session, G)
        events = await tm.fill(
            "marvel_rivals",
            "competitive",
            6,
            roles=["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"],
        )
    await env.dispatcher.dispatch(events)
    card = env.gateway.latest_card(ALERTS)
    assert "🧪" in card.fields[0][1] and "(test)" in card.fields[0][1]
    assert card.content == ""  # nobody pinged
    assert env.gateway.dms == []


def test_retry_only_for_429_and_5xx():
    class Err(Exception):
        def __init__(self, status):
            self.status = status

    assert is_retryable(Err(429)) and is_retryable(Err(503))
    assert (
        not is_retryable(Err(403)) and not is_retryable(Err(404)) and not is_retryable(ValueError())
    )


@pytest.mark.asyncio
async def test_with_retry_backs_off_then_succeeds_and_caps_attempts():
    class Err(Exception):
        def __init__(self, status):
            self.status = status

    delays, calls = [], {"n": 0}

    async def sleep(d):
        delays.append(d)

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Err(500)
        return "ok"

    assert await with_retry(flaky, sleep=sleep) == "ok" and calls["n"] == 3
    assert len(delays) == 2 and delays[1] > delays[0]

    async def always():
        raise Err(429)

    calls["n"] = 0
    with pytest.raises(Err):
        await with_retry(always, attempts=3, sleep=sleep)

    async def forbidden():
        calls["n"] += 1
        raise Err(403)

    calls["n"] = 0
    with pytest.raises(Err):
        await with_retry(forbidden, sleep=sleep)
    assert calls["n"] == 1  # 403 is not retried
