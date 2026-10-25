"""Fix 2: queue -> proposal flow through the real matching engine."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.helpers import GOLD_II, join_marvel_solos
from tianling.core.timeutil import utcnow
from tianling.db.models import MatchGroup, MatchMember, PartyMember, QueueEntry
from tianling.db.repositories.penalties import PenaltyRepository
from tianling.services.confirmation_service import ConfirmationService
from tianling.services.events import GroupFinalized, ProposalCreated
from tianling.services.queue_service import (
    AlreadyQueued,
    CooldownActive,
    QueueService,
    new_guest_id,
)
from tianling.services.settings import MatchSettings

G = 1


async def _groups(session) -> list[MatchGroup]:
    return list((await session.execute(select(MatchGroup).order_by(MatchGroup.id))).scalars())


async def _members(session, group: MatchGroup) -> list[MatchMember]:
    res = await session.execute(
        select(MatchMember).where(MatchMember.match_group_id == group.id).order_by(MatchMember.id)
    )
    return list(res.scalars())


@pytest.mark.asyncio
async def test_six_solos_fill_a_marvel_rivals_roster(session_factory):
    async with session_factory() as session:
        qs = QueueService(session, MatchSettings())
        await join_marvel_solos(qs, [101, 102, 103, 104, 105, 106])
        await session.commit()
        events = qs.pop_events()
        proposals = [e for e in events if isinstance(e, ProposalCreated)]
        assert len(proposals) == 1
        assert sorted(proposals[0].member_ids) == [101, 102, 103, 104, 105, 106]

        (group,) = await _groups(session)
        members = await _members(session, group)
        assert len(members) == 6
        roles = sorted(m.role_assigned for m in members)
        assert roles == ["duelist", "duelist", "strategist", "strategist", "vanguard", "vanguard"]
        # Newly matched solo players start pending.
        assert all(m.response is None for m in members)
        entries = (await session.execute(select(QueueEntry))).scalars().all()
        assert {e.status for e in entries} == {"reserved"}
        assert {e.proposal_id for e in entries} == {group.proposal_id}


@pytest.mark.asyncio
async def test_two_solos_do_not_match_a_six_player_roster(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        await join_marvel_solos(qs, [1, 2], ["vanguard", "duelist"])
        assert qs.pop_events() == []
        assert await _groups(session) == []


@pytest.mark.asyncio
async def test_party_is_one_entry_and_every_player_gets_a_match_row(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        guest = new_guest_id()
        party_id = await qs.create_party(
            G,
            201,
            [201, 202, guest],
            declared_roles={201: "vanguard", 202: "vanguard", guest: "duelist"},
            guest_flags={guest: True},
            rank_snapshots={201: GOLD_II, 202: GOLD_II, guest: GOLD_II},
        )
        # Every party member (guest included) is persisted.
        pms = (await session.execute(select(PartyMember))).scalars().all()
        assert len(pms) == 3 and sum(p.is_guest for p in pms) == 1

        await qs.join_queue(
            G, 201, "marvel_rivals", "competitive", region="EMEA", party_id=party_id
        )
        await join_marvel_solos(qs, [301, 302, 303], ["duelist", "strategist", "strategist"])
        await session.commit()

        entries = (await session.execute(select(QueueEntry))).scalars().all()
        party_entries = [e for e in entries if e.party_id]
        assert len(party_entries) == 1 and party_entries[0].party_size == 3

        (group,) = await _groups(session)
        members = await _members(session, group)
        assert len(members) == 6  # one row per PLAYER, not per entry
        by_id = {m.member_id: m for m in members}
        # party members were accepted when the party formed; guest counts as accepted
        assert by_id[201].response == "accepted"
        assert by_id[202].response == "accepted"
        assert by_id[guest].response == "accepted" and by_id[guest].is_guest
        # newly matched players are pending
        assert all(by_id[m].response is None for m in (301, 302, 303))
        assert by_id[201].party_id == party_id == by_id[guest].party_id


@pytest.mark.asyncio
async def test_party_fields_are_not_corrupted_by_reservation(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        party_id = await qs.create_party(
            G,
            401,
            [401, 402],
            declared_roles={401: "vanguard", 402: "duelist"},
            rank_snapshots={401: GOLD_II, 402: GOLD_II},
        )
        await qs.join_queue(
            G, 401, "marvel_rivals", "competitive", party_id=party_id, region="EMEA"
        )
        await join_marvel_solos(
            qs, [1, 2, 3, 4], ["vanguard", "duelist", "strategist", "strategist"]
        )
        entry = (
            await session.execute(select(QueueEntry).where(QueueEntry.party_id == party_id))
        ).scalar_one()
        assert entry.status == "reserved"
        assert entry.party_id == party_id
        assert entry.party_size == 2
        assert entry.leader_member_id == 401
        assert entry.proposal_id is not None and entry.proposal_id != party_id


@pytest.mark.asyncio
async def test_multiple_proposals_in_one_run(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        # 12 players queued before any matching runs would normally join one by one;
        # here the first six form a group, and the next six form a second one.
        await join_marvel_solos(qs, list(range(1, 7)))
        await join_marvel_solos(qs, list(range(11, 17)))
        groups = await _groups(session)
        assert len(groups) == 2
        all_ids = [m.member_id for g in groups for m in await _members(session, g)]
        assert len(all_ids) == len(set(all_ids)) == 12


@pytest.mark.asyncio
async def test_one_run_returns_all_proposals(session_factory):
    """Entries inserted without matching, then ONE matching run creates every proposal."""
    async with session_factory() as session:
        qs = QueueService(session)
        users = qs.users
        now = utcnow()
        for i, member_id in enumerate(list(range(1, 13))):
            user = await users.get_or_create(G, member_id)
            session.add(
                QueueEntry(
                    guild_id=G,
                    user_id=user.id,
                    member_id=member_id,
                    game="marvel_rivals",
                    activity="competitive",
                    queue_type="default",
                    region="EMEA",
                    role_preference=["vanguard", "duelist", "strategist"][i % 3],
                    rank_snapshot=GOLD_II,
                    status="queued",
                    queued_at=now,
                    expires_at=now + timedelta(minutes=30),
                    party_size=1,
                )
            )
        await session.flush()
        created = await qs.run_matching(G, "marvel_rivals", "competitive")
        assert len(created) == 2
        assert len(await _groups(session)) == 2


@pytest.mark.asyncio
async def test_either_queue_cancels_siblings_when_one_is_reserved(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        entry = await qs.join_queue(
            G,
            501,
            "deadlock",
            "ranked",
            either_with=["standard"],
            region="EMEA",
            rank_snapshot={"tier": "Initiate", "division": "I"},
        )
        siblings = (
            (await session.execute(select(QueueEntry).where(QueueEntry.member_id == 501)))
            .scalars()
            .all()
        )
        assert {e.activity for e in siblings} == {"ranked", "standard"}
        assert all(e.either_group for e in siblings)
        # fill the ranked roster (6) with five more players
        for i in range(502, 507):
            await qs.join_queue(
                G,
                i,
                "deadlock",
                "ranked",
                region="EMEA",
                rank_snapshot={"tier": "Initiate", "division": "I"},
            )
        await session.refresh(entry)
        assert entry.status == "reserved"
        refreshed = (
            (await session.execute(select(QueueEntry).where(QueueEntry.member_id == 501)))
            .scalars()
            .all()
        )
        by_activity = {e.activity: e.status for e in refreshed}
        assert by_activity == {"ranked": "reserved", "standard": "cancelled"}


@pytest.mark.asyncio
async def test_penalty_cooldown_blocks_join(session_factory):
    async with session_factory() as session:
        await PenaltyRepository(session).increment_and_set(G, 601, 300, utcnow())
        qs = QueueService(session)
        with pytest.raises(CooldownActive) as exc:
            await qs.join_queue(G, 601, "marvel_rivals", "competitive", rank_snapshot=GOLD_II)
        assert "cooldown" in str(exc.value).lower()
        # nobody was queued
        assert (await session.execute(select(QueueEntry))).scalars().all() == []


@pytest.mark.asyncio
async def test_cooldown_applies_to_party_members_too(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        party_id = await qs.create_party(G, 1, [1, 2], declared_roles={1: "vanguard", 2: "duelist"})
        await PenaltyRepository(session).increment_and_set(G, 2, 300, utcnow())
        with pytest.raises(CooldownActive):
            await qs.join_queue(G, 1, "marvel_rivals", "competitive", party_id=party_id)


@pytest.mark.asyncio
async def test_cannot_queue_twice_for_the_same_activity(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        await qs.join_queue(G, 7, "marvel_rivals", "competitive", rank_snapshot=GOLD_II)
        with pytest.raises(AlreadyQueued):
            await qs.join_queue(G, 7, "marvel_rivals", "competitive", rank_snapshot=GOLD_II)
        party_id = await qs.create_party(G, 8, [8, 7], declared_roles={8: "vanguard", 7: "duelist"})
        with pytest.raises(AlreadyQueued):  # 7 is already queued; cannot be in a second entry
            await qs.join_queue(G, 8, "marvel_rivals", "competitive", party_id=party_id)


@pytest.mark.asyncio
async def test_pending_party_invite_blocks_queueing(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        party_id = await qs.create_party(
            G, 1, [1, 2], declared_roles={1: "vanguard", 2: "duelist"}, pending_ids={2}
        )
        from tianling.services.queue_service import PartyNotReady

        with pytest.raises(PartyNotReady):
            await qs.join_queue(G, 1, "marvel_rivals", "competitive", party_id=party_id)
        assert await qs.respond_party_invite(G, party_id, 2, accept=True)
        await qs.join_queue(G, 1, "marvel_rivals", "competitive", party_id=party_id)


@pytest.mark.asyncio
async def test_leave_queue_cancels_queued_entry(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        await qs.join_queue(G, 9, "marvel_rivals", "competitive", rank_snapshot=GOLD_II)
        result = await qs.leave_queue(G, 9)
        assert result and not result.in_proposal
        assert await qs.get_status(G, 9) == []


@pytest.mark.asyncio
async def test_failed_roster_hash_blocks_immediate_regeneration(session_factory):
    from tianling.db.models import FailedRoster

    async with session_factory() as session:
        settings = MatchSettings(cooldown_seconds=0)  # so the decliner can rejoin at once
        qs = QueueService(session, settings)
        await join_marvel_solos(qs, list(range(1, 7)))
        (group,) = await _groups(session)
        cs = ConfirmationService(session, settings)
        await cs.decline(G, group.group_uuid, 1)  # player 1 (a vanguard) declines
        assert len(await _groups(session)) == 1  # nobody to replace player 1 yet

        # Player 1 rejoins with the same role: the identical roster is on the failed list.
        qs2 = QueueService(session, settings)
        await qs2.join_queue(
            G,
            1,
            "marvel_rivals",
            "competitive",
            region="EMEA",
            role_preference="vanguard",
            rank_snapshot=GOLD_II,
        )
        assert len(await _groups(session)) == 1, "same failed roster must not be regenerated"

        # Once the TTL passes it can be proposed again.
        for row in (await session.execute(select(FailedRoster))).scalars():
            row.expires_at = utcnow() - timedelta(seconds=1)
        await session.flush()
        assert len(await qs2.run_matching(G, "marvel_rivals", "competitive")) == 1


@pytest.mark.asyncio
async def test_auto_confirm_finalizes_without_a_prompt(session_factory):
    async with session_factory() as session:
        qs = QueueService(session, MatchSettings(auto_confirm=True))
        await join_marvel_solos(qs, list(range(1, 7)))
        events = qs.pop_events()
        assert any(isinstance(e, GroupFinalized) for e in events)
        (group,) = await _groups(session)
        assert group.status == "finalized" and group.confirmation_deadline is None


@pytest.mark.asyncio
async def test_full_premade_party_finalizes_immediately(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        ids = [1, 2, 3, 4, 5, 6]
        party_id = await qs.create_party(
            G,
            1,
            ids,
            declared_roles=dict(
                zip(
                    ids,
                    ["vanguard", "vanguard", "duelist", "duelist", "strategist", "strategist"],
                    strict=True,
                )
            ),
            rank_snapshots={i: GOLD_II for i in ids},
        )
        await qs.join_queue(G, 1, "marvel_rivals", "competitive", party_id=party_id, region="EMEA")
        (group,) = await _groups(session)
        assert group.status == "finalized"


@pytest.mark.asyncio
async def test_services_do_not_commit(session_factory):
    """Rolling back after the service call must undo everything (caller owns the txn)."""
    async with session_factory() as session:
        qs = QueueService(session)
        await join_marvel_solos(qs, list(range(1, 7)))
        await session.rollback()
    async with session_factory() as session:
        assert (await session.execute(select(QueueEntry))).scalars().all() == []
        assert (await session.execute(select(MatchGroup))).scalars().all() == []


@pytest.mark.asyncio
async def test_datetimes_are_timezone_aware_after_read(session_factory):
    async with session_factory() as session:
        qs = QueueService(session)
        await qs.join_queue(G, 5, "marvel_rivals", "competitive", rank_snapshot=GOLD_II)
        await session.commit()
    async with session_factory() as session:
        entry = (await session.execute(select(QueueEntry))).scalar_one()
        assert entry.queued_at.tzinfo is not None
        assert entry.expires_at.tzinfo is not None
