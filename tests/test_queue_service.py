"""Integration: queue -> proposal -> all accept -> finalized, with a party and fake players."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from tests.helpers import GOLD_II
from tianling.db.models import MatchGroup, MatchMember, QueueEntry
from tianling.services.confirmation_service import ConfirmationService
from tianling.services.events import GroupFinalized, ProposalCreated
from tianling.services.queue_service import QueueError, QueueService
from tianling.services.settings import MatchSettings
from tianling.services.test_mode import TestMode

G = 1


@pytest.mark.asyncio
async def test_queue_proposal_accept_finalize_with_party_and_fake_players(session_factory):
    async with session_factory() as session:
        qs = QueueService(session, MatchSettings())
        party_id = await qs.create_party(
            G,
            111,
            [111, 112],
            declared_roles={111: "vanguard", 112: "duelist"},
            rank_snapshots={111: GOLD_II, 112: GOLD_II},
        )
        await qs.join_queue(
            G, 111, "marvel_rivals", "competitive", party_id=party_id, region="EMEA"
        )
        tm = TestMode(session, G)
        await tm.add_player(9001, "marvel_rivals", "competitive", GOLD_II, "vanguard", "EMEA")
        await tm.add_player(9002, "marvel_rivals", "competitive", GOLD_II, "duelist", "EMEA")
        await tm.add_player(9003, "marvel_rivals", "competitive", GOLD_II, "strategist", "EMEA")
        await tm.add_player(9004, "marvel_rivals", "competitive", GOLD_II, "strategist", "EMEA")
        events = qs.pop_events() + await tm.match()
        assert any(isinstance(e, ProposalCreated) for e in events)
        await session.commit()

    async with session_factory() as session:
        group = (await session.execute(select(MatchGroup))).scalar_one()
        members = (
            (
                await session.execute(
                    select(MatchMember).where(MatchMember.match_group_id == group.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(members) == 6
        cs = ConfirmationService(session, MatchSettings())
        pending = [m.member_id for m in members if m.response is None]
        assert sorted(pending) == [9001, 9002, 9003, 9004]  # party members were pre-accepted
        last = None
        for mid in pending:
            last = await cs.accept(G, group.group_uuid, mid)
        assert last is not None and last.finalized
        assert any(isinstance(e, GroupFinalized) for e in cs.pop_events())
        await session.commit()

    async with session_factory() as session:
        assert (await session.execute(select(MatchGroup))).scalar_one().status == "finalized"
        entries = (await session.execute(select(QueueEntry))).scalars().all()
        assert {e.status for e in entries} == {"matched"}


@pytest.mark.asyncio
async def test_ten_players_join_at_once_nobody_lands_in_two_groups(session_factory):
    roles = ["vanguard", "duelist", "strategist"]

    async def join(member_id: int):
        async with session_factory() as session:
            qs = QueueService(session, MatchSettings())
            try:
                await qs.join_queue(
                    G,
                    member_id,
                    "marvel_rivals",
                    "competitive",
                    region="EMEA",
                    role_preference=roles[member_id % 3],
                    rank_snapshot=GOLD_II,
                )
            except QueueError:
                pass
            await session.commit()

    await asyncio.gather(*(join(i) for i in range(1, 11)))
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(MatchMember.member_id)
                    .join(MatchGroup, MatchGroup.id == MatchMember.match_group_id)
                    .where(MatchGroup.status == "proposed")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == len(set(rows)), "a player is in two proposed groups"
        reserved = (
            (await session.execute(select(QueueEntry).where(QueueEntry.status == "reserved")))
            .scalars()
            .all()
        )
        assert len(reserved) == len(rows)


@pytest.mark.asyncio
async def test_same_player_joining_twice_concurrently_creates_one_entry(session_factory):
    async def join():
        async with session_factory() as session:
            qs = QueueService(session, MatchSettings())
            try:
                await qs.join_queue(G, 42, "marvel_rivals", "competitive", rank_snapshot=GOLD_II)
                await session.commit()
                return True
            except QueueError:
                return False

    results = await asyncio.gather(*(join() for _ in range(5)))
    assert results.count(True) == 1
