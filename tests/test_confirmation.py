"""Fix 3: decline / timeout / replacement / idempotency / finalization rules."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.helpers import GOLD_II, join_marvel_solos
from tianling.core.timeutil import utcnow
from tianling.db.models import MatchGroup, MatchMember, QueueEntry
from tianling.db.repositories.penalties import PenaltyRepository
from tianling.services.confirmation_service import ConfirmationService
from tianling.services.events import (
    GroupCancelled,
    GroupFinalized,
    MemberAccepted,
    MemberDeclined,
    MemberTimedOut,
    ProposalCreated,
    ReplacementFailed,
    ReplacementStarted,
    ReplacementSucceeded,
)
from tianling.services.queue_service import QueueService
from tianling.services.settings import MatchSettings

G = 1
ALL = [1, 2, 3, 4, 5, 6]  # roles: v v d d s s


async def _group(session, status: str | None = None, n: int = 0) -> MatchGroup:
    stmt = select(MatchGroup).order_by(MatchGroup.id)
    if status:
        stmt = stmt.where(MatchGroup.status == status)
    return list((await session.execute(stmt)).scalars())[n]


async def _members(session, group) -> dict[int, MatchMember]:
    res = await session.execute(select(MatchMember).where(MatchMember.match_group_id == group.id))
    return {m.member_id: m for m in res.scalars()}


async def _entry(session, member_id: int, activity: str = "competitive") -> QueueEntry:
    res = await session.execute(
        select(QueueEntry)
        .where(QueueEntry.member_id == member_id, QueueEntry.activity == activity)
        .order_by(QueueEntry.id.desc())
    )
    return res.scalars().first()


async def _setup(session, settings=None, ids=ALL, roles=None):
    qs = QueueService(session, settings or MatchSettings())
    await join_marvel_solos(qs, ids, roles)
    qs.pop_events()
    return qs


def _kinds(events):
    return [type(e) for e in events]


@pytest.mark.asyncio
async def test_everyone_accepts_and_group_is_finalized(session_factory):
    async with session_factory() as session:
        await _setup(session)
        group = await _group(session)
        cs = ConfirmationService(session, MatchSettings())
        results = [await cs.accept(G, group.group_uuid, m) for m in ALL]
        assert [r.status for r in results[:-1]] == ["accepted"] * 5
        assert results[-1].status == "finalized"
        events = cs.pop_events()
        assert sum(isinstance(e, GroupFinalized) for e in events) == 1
        assert sum(isinstance(e, MemberAccepted) for e in events) == 6
        group = await _group(session)
        assert group.status == "finalized" and group.finalized_at is not None
        assert {(await _entry(session, m)).status for m in ALL} == {"matched"}


@pytest.mark.asyncio
async def test_decline_releases_others_and_rematches_with_a_replacement(session_factory):
    async with session_factory() as session:
        settings = MatchSettings()
        qs = await _setup(session, settings)
        # A seventh player (a strategist) is waiting in the queue behind the six.
        await join_marvel_solos(qs, [7], ["strategist"])
        group = await _group(session)
        original_queued_at = {m: (await _entry(session, m)).queued_at for m in (1, 2, 3, 4, 6)}

        cs = ConfirmationService(session, settings)
        for m in (1, 2):
            await cs.accept(G, group.group_uuid, m)
        cs.pop_events()
        result = await cs.decline(G, group.group_uuid, 5)  # a strategist declines
        assert result.status == "declined"
        events = cs.pop_events()
        kinds = _kinds(events)
        assert MemberDeclined in kinds and ReplacementStarted in kinds
        assert ReplacementSucceeded in kinds and ProposalCreated in kinds
        assert GroupCancelled in kinds

        old = await _group(session, "cancelled")
        new = await _group(session, "proposed")
        assert old.group_uuid != new.group_uuid
        new_members = await _members(session, new)
        assert sorted(new_members) == [1, 2, 3, 4, 6, 7]
        # Players who already accepted stay accepted; the replacement must answer.
        assert new_members[1].response == "accepted" and new_members[2].response == "accepted"
        assert new_members[7].response is None
        # Only the decliner was cancelled & penalised; the rest keep their queue position.
        assert (await _entry(session, 5)).status == "cancelled"
        for m, queued_at in original_queued_at.items():
            assert (await _entry(session, m)).queued_at == queued_at
            assert (await _entry(session, m)).status == "reserved"
        assert await PenaltyRepository(session).get_active(G, 5, utcnow()) is not None
        assert await PenaltyRepository(session).get_active(G, 1, utcnow()) is None


@pytest.mark.asyncio
async def test_decline_without_replacement_releases_everyone_else(session_factory):
    async with session_factory() as session:
        settings = MatchSettings()
        await _setup(session, settings)
        group = await _group(session)
        cs = ConfirmationService(session, settings)
        await cs.decline(G, group.group_uuid, 3)
        events = cs.pop_events()
        failed = [e for e in events if isinstance(e, ReplacementFailed)]
        assert len(failed) == 1 and failed[0].released is True
        assert sorted(failed[0].released_member_ids) == [1, 2, 4, 5, 6]
        # nobody is stuck in "reserved"
        statuses = {m: (await _entry(session, m)).status for m in ALL}
        assert statuses == {
            1: "queued",
            2: "queued",
            3: "cancelled",
            4: "queued",
            5: "queued",
            6: "queued",
        }


@pytest.mark.asyncio
async def test_cancel_policy_cancels_the_rest_when_no_replacement(session_factory):
    async with session_factory() as session:
        settings = MatchSettings(replacement_policy="cancel")
        await _setup(session, settings)
        group = await _group(session)
        cs = ConfirmationService(session, settings)
        await cs.decline(G, group.group_uuid, 3)
        failed = [e for e in cs.pop_events() if isinstance(e, ReplacementFailed)]
        assert failed[0].released is False
        assert {(await _entry(session, m)).status for m in (1, 2, 4, 5, 6)} == {"cancelled"}


@pytest.mark.asyncio
async def test_deadline_times_out_only_pending_members_once(session_factory):
    async with session_factory() as session:
        settings = MatchSettings()
        await _setup(session, settings)
        group = await _group(session)
        cs = ConfirmationService(session, settings)
        for m in (1, 2, 3):
            await cs.accept(G, group.group_uuid, m)
        cs.pop_events()
        group.confirmation_deadline = utcnow() - timedelta(seconds=5)
        await session.flush()

        events = await cs.handle_deadline(group.group_uuid)
        timed_out = sorted(e.member_id for e in events if isinstance(e, MemberTimedOut))
        assert timed_out == [4, 5, 6]  # only those still pending
        members = await _members(session, await _group(session, "cancelled"))
        assert [members[m].response for m in (1, 2, 3)] == ["accepted"] * 3
        assert [members[m].response for m in (4, 5, 6)] == ["timeout"] * 3
        for m in (4, 5, 6):
            assert await PenaltyRepository(session).get_active(G, m, utcnow()) is not None
        # accepted members are released, timed-out ones cancelled
        assert {(await _entry(session, m)).status for m in (1, 2, 3)} == {"queued"}
        assert {(await _entry(session, m)).status for m in (4, 5, 6)} == {"cancelled"}

        # Running the deadline job again does nothing.
        assert await cs.handle_deadline(group.group_uuid) == []


@pytest.mark.asyncio
async def test_party_member_decline_cancels_whole_party_without_penalty(session_factory):
    async with session_factory() as session:
        settings = MatchSettings()
        qs = QueueService(session, settings)
        party_id = await qs.create_party(
            G,
            101,
            [101, 102],
            declared_roles={101: "vanguard", 102: "vanguard"},
            rank_snapshots={101: GOLD_II, 102: GOLD_II},
        )
        await qs.join_queue(
            G, 101, "marvel_rivals", "competitive", party_id=party_id, region="EMEA"
        )
        await join_marvel_solos(
            qs, [3, 4, 5, 6], ["duelist", "duelist", "strategist", "strategist"]
        )
        qs.pop_events()
        group = await _group(session)
        cs = ConfirmationService(session, settings)
        await cs.accept(G, group.group_uuid, 3)
        await cs.decline(G, group.group_uuid, 102)  # a PARTY member declines
        events = cs.pop_events()
        declined = [e for e in events if isinstance(e, MemberDeclined)]
        assert declined[0].is_party_member and declined[0].penalized is False
        # the whole party entry is gone, the solos are released
        party_entry = await _entry(session, 101)
        assert party_entry.status == "cancelled" and party_entry.party_id == party_id
        assert {(await _entry(session, m)).status for m in (3, 4, 5, 6)} == {"queued"}
        repo = PenaltyRepository(session)
        assert await repo.get_active(G, 101, utcnow()) is None
        assert await repo.get_active(G, 102, utcnow()) is None


@pytest.mark.asyncio
async def test_outside_decline_never_penalizes_the_party(session_factory):
    async with session_factory() as session:
        settings = MatchSettings()
        qs = QueueService(session, settings)
        party_id = await qs.create_party(
            G,
            101,
            [101, 102],
            declared_roles={101: "vanguard", 102: "vanguard"},
            rank_snapshots={101: GOLD_II, 102: GOLD_II},
        )
        await qs.join_queue(
            G, 101, "marvel_rivals", "competitive", party_id=party_id, region="EMEA"
        )
        await join_marvel_solos(
            qs, [3, 4, 5, 6], ["duelist", "duelist", "strategist", "strategist"]
        )
        group = await _group(session)
        cs = ConfirmationService(session, settings)
        await cs.decline(G, group.group_uuid, 6)  # an outside solo declines
        repo = PenaltyRepository(session)
        assert await repo.get_active(G, 6, utcnow()) is not None  # the decliner is penalised
        assert await repo.get_active(G, 101, utcnow()) is None
        assert await repo.get_active(G, 102, utcnow()) is None
        # the party keeps its place in the queue
        party_entry = await _entry(session, 101)
        assert party_entry.status == "queued" and party_entry.party_id == party_id


@pytest.mark.asyncio
async def test_double_accept_is_a_noop(session_factory):
    async with session_factory() as session:
        await _setup(session)
        group = await _group(session)
        cs = ConfirmationService(session, MatchSettings())
        first = await cs.accept(G, group.group_uuid, 1)
        second = await cs.accept(G, group.group_uuid, 1)
        assert first.status == "accepted" and second.status == "already_accepted"
        assert sum(isinstance(e, MemberAccepted) for e in cs.pop_events()) == 1


@pytest.mark.asyncio
async def test_accept_after_finalized_or_cancelled_is_a_noop(session_factory):
    async with session_factory() as session:
        await _setup(session)
        group = await _group(session)
        cs = ConfirmationService(session, MatchSettings())
        for m in ALL:
            await cs.accept(G, group.group_uuid, m)
        cs.pop_events()
        again = await cs.accept(G, group.group_uuid, 1)
        assert again.status == "group_closed" and "confirmed" in again.message
        assert cs.pop_events() == []  # no second finalization
        assert (await cs.decline(G, group.group_uuid, 2)).status == "group_closed"

    async with session_factory() as session2:
        await _setup(session2, ids=[11, 12, 13, 14, 15, 16])
        group2 = await _group(session2, "proposed")
        cs2 = ConfirmationService(session2, MatchSettings())
        await cs2.decline(G, group2.group_uuid, 11)
        late = await cs2.accept(G, group2.group_uuid, 12)
        assert late.status == "group_closed" and "cancelled" in late.message


@pytest.mark.asyncio
async def test_outsiders_cannot_respond(session_factory):
    async with session_factory() as session:
        await _setup(session)
        group = await _group(session)
        cs = ConfirmationService(session, MatchSettings())
        assert (await cs.accept(G, group.group_uuid, 999)).status == "not_in_group"
        assert (await cs.decline(G, group.group_uuid, 999)).status == "not_in_group"
        assert (await cs.accept(G, "no-such-group", 1)).status == "not_found"
        assert (await cs.accept(2, group.group_uuid, 1)).status == "not_found"  # other guild


@pytest.mark.asyncio
async def test_restart_mid_proposal_continues_from_the_database(session_factory):
    async with session_factory() as session:
        await _setup(session)
        group = await _group(session)
        cs = ConfirmationService(session, MatchSettings())
        for m in (1, 2, 3):
            await cs.accept(G, group.group_uuid, m)
        uuid = group.group_uuid
        await session.commit()

    # "Restart": brand-new session and service objects, nothing in memory.
    async with session_factory() as session:
        cs = ConfirmationService(session, MatchSettings())
        assert (await cs.accept(G, uuid, 1)).status == "already_accepted"
        for m in (4, 5):
            assert (await cs.accept(G, uuid, m)).status == "accepted"
        assert (await cs.accept(G, uuid, 6)).status == "finalized"
        await session.commit()
    async with session_factory() as session:
        assert (await _group(session)).status == "finalized"


@pytest.mark.asyncio
async def test_member_who_left_the_server_blocks_finalization_without_penalty(session_factory):
    async with session_factory() as session:
        settings = MatchSettings()
        await _setup(session, settings)
        group = await _group(session)
        cs = ConfirmationService(session, settings)
        present = {1, 2, 3, 4, 5}  # 6 left the server
        for m in (1, 2, 3, 4, 5):
            await cs.accept(G, group.group_uuid, m, present_member_ids=present)
        result = await cs.accept(G, group.group_uuid, 6, present_member_ids=present)
        assert result.status == "cancelled"
        assert (await _group(session)).status == "cancelled"
        assert await PenaltyRepository(session).get_active(G, 6, utcnow()) is None
        assert (await _entry(session, 6)).status == "cancelled"
        assert (await _entry(session, 1)).status == "queued"


@pytest.mark.asyncio
async def test_banned_member_blocks_finalization(session_factory):
    async with session_factory() as session:
        await _setup(session)
        group = await _group(session)
        cs = ConfirmationService(session, MatchSettings())
        for m in ALL:
            res = await cs.accept(G, group.group_uuid, m, banned_member_ids={4})
        assert res.status == "cancelled"
        assert (await _entry(session, 4)).status == "cancelled"


@pytest.mark.asyncio
async def test_withdrawn_entry_blocks_finalization(session_factory):
    """A player whose queue entry was cancelled (e.g. profile deleted) cannot be finalized."""
    async with session_factory() as session:
        await _setup(session)
        group = await _group(session)
        (await _entry(session, 2)).status = "cancelled"
        await session.flush()
        cs = ConfirmationService(session, MatchSettings())
        for m in ALL:
            res = await cs.accept(G, group.group_uuid, m)
        assert res.status == "cancelled"
        assert (await _group(session)).status == "cancelled"


@pytest.mark.asyncio
async def test_member_in_another_finalized_group_blocks_finalization(session_factory):
    async with session_factory() as session:
        await _setup(session)
        group = await _group(session)
        other = MatchGroup(
            guild_id=G,
            group_uuid="other-finalized",
            game="marvel_rivals",
            activity="competitive",
            status="finalized",
            proposal_status="finalized",
            finalized_at=utcnow(),
        )
        session.add(other)
        await session.flush()
        session.add(
            MatchMember(
                guild_id=G,
                match_group_id=other.id,
                member_id=3,
                response="accepted",
            )
        )
        await session.flush()
        cs = ConfirmationService(session, MatchSettings())
        for m in ALL:
            res = await cs.accept(G, group.group_uuid, m)
        assert res.status == "cancelled"


@pytest.mark.asyncio
async def test_requeueing_releases_the_previous_finalized_membership(session_factory):
    """A player who played a match and queues again must not be blocked by the old group."""
    async with session_factory() as session:
        settings = MatchSettings(auto_confirm=True)
        qs = QueueService(session, settings)
        await join_marvel_solos(qs, ALL)
        assert (await _group(session)).status == "finalized"
        await join_marvel_solos(qs, [1, 12, 13, 14, 15, 16])  # 1 plays again straight away
        groups = list((await session.execute(select(MatchGroup).order_by(MatchGroup.id))).scalars())
        assert [g.status for g in groups] == ["finalized", "finalized"]


@pytest.mark.asyncio
async def test_deadline_job_is_scheduled_and_cancelled_on_finalize(session_factory):
    from tianling.db.models import ScheduledJob

    async with session_factory() as session:
        await _setup(session)
        group = await _group(session)
        jobs = (
            (
                await session.execute(
                    select(ScheduledJob).where(ScheduledJob.job_type == "confirmation_deadline")
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1 and jobs[0].payload == {"group_uuid": group.group_uuid}
        cs = ConfirmationService(session, MatchSettings())
        for m in ALL:
            await cs.accept(G, group.group_uuid, m)
        await session.refresh(jobs[0])
        assert jobs[0].status == "cancelled"
