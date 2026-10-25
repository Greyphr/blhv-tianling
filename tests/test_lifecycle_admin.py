"""Fix 5/8: proposal button handler, lifecycle, admin config, expired menus, migration drift."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select

from tests.helpers import join_marvel_solos
from tests.test_dispatch_voice import Env
from tests.test_lfg_party import make_profile
from tests.test_registration_wizard import FakeInteraction
from tianling.db.models import GameProfile, MatchGroup, QueueEntry, RegistryMessage
from tianling.discord_ui import lifecycle
from tianling.discord_ui.fake_adapter import FakeDiscordAdapter
from tianling.discord_ui.flow_common import ExpiredButtonItem
from tianling.discord_ui.lfg_flow import queue_solo
from tianling.discord_ui.proposal import ProposalButton, respond_to_proposal
from tianling.discord_ui.registry import RegistrySyncer
from tianling.services import admin
from tianling.services.queue_service import QueueService
from tianling.services.settings import MatchSettings, load_match_settings, set_channel_ids

G = 1
ALL = [1, 2, 3, 4, 5, 6]


@pytest.fixture
async def env(session_factory):
    e = Env(session_factory)
    await e.configure()
    return e


# ---------------------------------------------------------------- proposal buttons


@pytest.mark.asyncio
async def test_button_custom_id_carries_the_group_and_survives_restart():
    btn = ProposalButton("accept", "0f0e-12")
    assert btn.item.custom_id == "tianling:prop:accept:0f0e-12" and len(btn.item.custom_id) <= 100
    import re

    match = re.fullmatch(
        ProposalButton.__discord_ui_compiled_template__.pattern, btn.item.custom_id
    )
    revived = await ProposalButton.from_custom_id(
        None, None, match
    )  # what discord.py does after a restart
    assert (revived.action, revived.group_uuid) == ("accept", "0f0e-12")
    assert ProposalButton("decline", "ab-12", disabled=True).item.disabled


@pytest.mark.asyncio
async def test_respond_to_proposal_handles_double_clicks_and_strangers(env):
    await env.queue_six()
    grp = await env.group()
    scope, gw = env.scope, env.gateway
    first, events = await respond_to_proposal(scope, gw, G, grp.group_uuid, 1, "accept")
    again, again_events = await respond_to_proposal(scope, gw, G, grp.group_uuid, 1, "accept")
    stranger, _ = await respond_to_proposal(scope, gw, G, grp.group_uuid, 999, "accept")
    assert (first.status, again.status, stranger.status) == (
        "accepted",
        "already_accepted",
        "not_in_group",
    )
    assert again_events == []
    declined, events = await respond_to_proposal(scope, gw, G, grp.group_uuid, 2, "decline")
    assert declined.status == "declined"
    late, _ = await respond_to_proposal(scope, gw, G, grp.group_uuid, 3, "accept")
    assert late.status == "group_closed"
    await env.dispatcher.dispatch(events)
    assert "Cancelled" in env.gateway.messages[(grp.channel_id, grp.message_id)].title


@pytest.mark.asyncio
async def test_accept_blocks_finalization_if_someone_left_the_server(env):
    await env.queue_six()
    grp = await env.group()
    env.gateway.absent_members = {6}
    for m in ALL:
        result, _ = await respond_to_proposal(
            env.scope, env.gateway, G, grp.group_uuid, m, "accept"
        )
    assert result.status == "cancelled" and (await env.group()).status == "cancelled"


@pytest.mark.asyncio
async def test_dynamic_items_registered_for_every_template():
    from tianling.discord_ui.lfg import PartyInviteButton, StillLookingButton

    for cls, sample in (
        (ProposalButton, "tianling:prop:decline:abc-1"),
        (PartyInviteButton, "tianling:party:accept:abc-1:123"),
        (StillLookingButton, "tianling:still:yes:marvel_rivals:competitive"),
    ):
        assert cls.__discord_ui_compiled_template__.fullmatch(sample), sample


@pytest.mark.asyncio
async def test_expired_menu_catchall_only_answers_when_nobody_else_did(monkeypatch):
    monkeypatch.setattr("tianling.discord_ui.flow_common.YIELD_SECONDS", 0.01)
    item = ExpiredButtonItem("tianling:reg:region")
    orphan = FakeInteraction()
    await item.callback(orphan)
    assert orphan.response.done  # replied with the 'expired' message
    live = FakeInteraction()
    live.response.done = True  # a live wizard view already deferred
    sent = []
    live.response.send_message = lambda *a, **k: sent.append(a)  # would fail if called
    await item.callback(live)
    assert sent == []


# -------------------------------------------------------------------- lifecycle


@pytest.mark.asyncio
async def test_member_leaving_cleans_up_everything_and_unsticks_others(env):
    adapter = FakeDiscordAdapter()
    syncer = RegistrySyncer(env.scope, adapter)
    async with env.scope() as session:
        await set_channel_ids(session, G, player_registry=777)
    await make_profile(env.scope, 1, "vanguard")
    async with env.scope() as session:
        from tianling.services.profile_service import ProfileService

        svc = ProfileService(session, G)
        ops = svc.pop_registry_ops()
    await syncer.reconcile(G)
    assert len(adapter.messages) == 1
    await env.queue_six()
    grp = await env.group()
    summary = await lifecycle.handle_member_remove(
        env.scope, G, 1, dispatcher=env.dispatcher, registry_syncer=syncer
    )
    assert summary is not None
    async with env.scope() as session:
        assert (await session.execute(select(GameProfile))).scalars().all() == []
        assert (await session.execute(select(RegistryMessage))).scalars().all() == []
        statuses = {
            e.member_id: e.status for e in (await session.execute(select(QueueEntry))).scalars()
        }
    assert 1 not in statuses and all(statuses[m] == "queued" for m in (2, 3, 4, 5, 6))
    assert adapter.messages == {}  # the registry message is gone from Discord
    assert (await env.group()).status == "cancelled" and grp.group_uuid
    assert "Cancelled" in env.gateway.messages[(grp.channel_id, grp.message_id)].title
    _ = ops


@pytest.mark.asyncio
async def test_startup_reconcile_isolates_failures(env):
    bot = SimpleNamespace(
        guild_id=G, dispatcher=env.dispatcher, voice=env.voice, scheduler=env.scheduler
    )

    async def broken(_guild):
        raise RuntimeError("boom")

    env.voice.reconcile = broken  # type: ignore[method-assign]
    await env.queue_six()
    report = await lifecycle.startup_reconcile(bot)
    assert report["voice"] == "failed" and report["proposals"] == 1  # the others still ran


@pytest.mark.asyncio
async def test_voice_events_ignore_none_and_duplicates(env):
    calls = []

    class V:
        async def on_voice_update(self, g, c):
            calls.append(c)
            return "x"

    await lifecycle.handle_voice_update(V(), G, [5, None, 5, 6])
    assert calls == [5, 6]


# -------------------------------------------------------------------------- admin


@pytest.mark.asyncio
async def test_admin_settings_validate_and_persist(session_factory):
    async with session_factory() as session:
        assert await admin.set_setting(session, G, "confirmation_timeout_seconds", "90") == 90
        assert await admin.set_setting(session, G, "auto_confirm", "on") is True
        assert await admin.set_setting(session, G, "replacement_policy", "cancel") == "cancel"
        assert await admin.set_setting(session, G, "voice_grace_seconds", "120") == 120
        assert await admin.set_setting(session, G, "moderator_role", "42") == [42]
        assert await admin.set_setting(session, G, "moderator_role", "43") == [42, 43]
        for key, bad in (
            ("confirmation_timeout_seconds", "5"),
            ("confirmation_timeout_seconds", "abc"),
            ("auto_confirm", "maybe"),
            ("replacement_policy", "nope"),
            ("nope", "1"),
        ):
            with pytest.raises(admin.SettingError):
                await admin.set_setting(session, G, key, bad)
        settings = await load_match_settings(session, G)
        assert (
            settings.confirmation_timeout_seconds,
            settings.auto_confirm,
            settings.replacement_policy,
        ) == (90, True, "cancel")
        current = await admin.current_settings(session, G)
        assert current["voice_grace_seconds"] == 120 and current["moderator_role"] == [42, 43]


@pytest.mark.asyncio
async def test_admin_settings_actually_change_matching_behaviour(session_factory):
    async with session_factory() as session:
        await admin.set_setting(session, G, "auto_confirm", "true")
        qs = await QueueService.create(session, G)
        await join_marvel_solos(qs, ALL)
        assert (await session.execute(select(MatchGroup))).scalar_one().status == "finalized"


@pytest.mark.asyncio
async def test_status_summary(env):
    await env.queue_six()
    async with env.scope() as session:
        qs = QueueService(session, MatchSettings())
        await qs.join_queue(
            G, 77, "deadlock", "ranked", rank_snapshot={"tier": "Initiate", "division": "I"}
        )
        summary = await admin.status_summary(session, G)
    assert (
        summary.proposals == 1
        and summary.reserved == 6
        and summary.queued == {"deadlock/ranked": 1}
    )


# --------------------------------------------------------------- migration drift


def test_models_and_migrations_agree(tmp_path):
    """After `alembic upgrade head` the schema must equal the models (no missing columns/tables)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from alembic import command
    from tianling.db import models  # noqa: F401
    from tianling.db.base import Base
    from tianling.db.migrate import alembic_config

    url = f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}"
    import os

    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(alembic_config(), "head")
    finally:
        os.environ.pop("DATABASE_URL", None)
    engine = create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata
        )
    engine.dispose()
    assert diff == [], f"models and migrations disagree: {diff}"


def test_downgrade_then_upgrade_is_clean(tmp_path):
    import os

    from alembic import command
    from tianling.db.migrate import alembic_config

    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(tmp_path / 'd.db').as_posix()}"
    try:
        cfg = alembic_config()
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0004_proposal_fields")
        command.upgrade(cfg, "head")
    finally:
        os.environ.pop("DATABASE_URL", None)


_ = (asyncio, queue_solo)


def test_setup_adopts_existing_channels_instead_of_duplicating():
    from tianling.discord_ui.channels import LEGACY_ALIASES, find_by_name, normalize

    cats = [SimpleNamespace(name="General"), SimpleNamespace(name="✨ The Heavenly Registry ✨")]
    assert find_by_name(cats, "THE HEAVENLY REGISTRY") is cats[1]
    chans = [
        SimpleNamespace(name="chat"),
        SimpleNamespace(name="profile-registry"),
        SimpleNamespace(name="Matchmaking"),
    ]
    assert (
        find_by_name(chans, "profile-registration", LEGACY_ALIASES["profile_registration"])
        is chans[1]
    )
    assert (
        find_by_name(chans, "matchmaking-alerts", LEGACY_ALIASES["matchmaking_alerts"]) is chans[2]
    )
    assert find_by_name(chans, "player-registry") is None
    assert normalize("🌙-Find_A-Team") == "findateam"
