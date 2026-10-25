"""Confirmation service: accept/decline/timeout, replacement, finalization.

All state lives in the database (group status, per-member responses, deadline
jobs), so a restart mid-proposal simply continues from the stored rows.  Every
state change is a status-guarded UPDATE, which makes double clicks and racing
timeouts harmless no-ops.  Services never commit: the caller owns the
transaction and dispatches ``pop_events()`` after committing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from tianling.core.timeutil import utcnow
from tianling.db.models import MatchGroup
from tianling.db.models import MatchMember as DBMatchMember
from tianling.db.repositories.compositions import CompositionRepository
from tianling.db.repositories.failed_rosters import FailedRosterRepository
from tianling.db.repositories.jobs import JobRepository
from tianling.db.repositories.matches import MatchRepository
from tianling.db.repositories.parties import PartyRepository
from tianling.db.repositories.penalties import PenaltyRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.services.events import (
    CooldownApplied,
    Event,
    GroupCancelled,
    GroupFinalized,
    MemberAccepted,
    MemberDeclined,
    MemberTimedOut,
    ReplacementFailed,
    ReplacementStarted,
    ReplacementSucceeded,
)
from tianling.services.roster import is_real_member, roster_view
from tianling.services.settings import ConfirmationConfig, MatchSettings

log = logging.getLogger(__name__)

__all__ = ["ConfirmationConfig", "ConfirmationService", "ResponseResult"]

_FAIL_REASONS = {"declined", "timeout"}


@dataclass
class ResponseResult:
    """Outcome of an Accept/Decline click. Never raises for 'nothing to do' cases."""

    status: str
    message: str
    group_uuid: str
    group_status: str | None = None

    @property
    def changed(self) -> bool:
        return self.status in {"accepted", "declined", "finalized", "cancelled"}

    @property
    def finalized(self) -> bool:
        return self.status == "finalized"


class ConfirmationService:
    def __init__(self, session, config: MatchSettings | None = None) -> None:
        self.session = session
        self.config = config or MatchSettings()
        self.matches = MatchRepository(session)
        self.queue = QueueRepository(session)
        self.parties = PartyRepository(session)
        self.jobs = JobRepository(session)
        self.penalties = PenaltyRepository(session)
        self.failed = FailedRosterRepository(session)
        self.compositions = CompositionRepository(session)
        self._events: list[Event] = []

    def pop_events(self) -> list[Event]:
        events = list(self._events)
        self._events.clear()
        return events

    async def _open_group(
        self, guild_id: int, group_uuid: str
    ) -> tuple[MatchGroup | None, ResponseResult | None]:
        """Return the group if it still accepts responses, else a no-op result."""
        group = await self.matches.get_group(group_uuid)
        if group is None or group.guild_id != guild_id:
            return None, ResponseResult("not_found", "That group no longer exists.", group_uuid)
        if group.status != "proposed":
            text = {
                "finalized": "This group is already confirmed.",
                "cancelled": "This proposal was cancelled.",
            }.get(group.status, "This proposal is closed.")
            return None, ResponseResult("group_closed", text, group_uuid, group.status)
        return group, None

    # ---------------------------------------------------------------- clicks

    async def accept(
        self,
        guild_id: int,
        group_uuid: str,
        member_id: int,
        *,
        present_member_ids: set[int] | None = None,
        banned_member_ids: set[int] | None = None,
    ) -> ResponseResult:
        """Accept a proposal. ``present_member_ids`` / ``banned_member_ids`` are supplied by
        the UI (it can see who is still in the server) and checked before finalizing."""
        group, early = await self._open_group(guild_id, group_uuid)
        if group is None:
            return early  # type: ignore[return-value]
        mm = await self.matches.get_member(group.id, member_id)
        if mm is None:
            return ResponseResult(
                "not_in_group", "You are not part of this group.", group_uuid, group.status
            )
        if mm.response == "accepted":
            return ResponseResult(
                "already_accepted", "You already accepted.", group_uuid, group.status
            )
        if mm.response in _FAIL_REASONS:
            return ResponseResult(
                "already_responded",
                "You already responded to this proposal.",
                group_uuid,
                group.status,
            )
        if not await self.matches.respond_if_pending(group.id, member_id, "accepted"):
            return ResponseResult(
                "already_accepted", "You already accepted.", group_uuid, group.status
            )

        members = await self.matches.list_members(group.id)
        self._events.append(
            MemberAccepted(
                guild_id=guild_id,
                group_uuid=group_uuid,
                member_id=member_id,
                party_id=mm.party_id,
                roster=roster_view(members),
            )
        )
        outcome = await self.try_finalize(
            group_uuid, present_member_ids=present_member_ids, banned_member_ids=banned_member_ids
        )
        if outcome == "finalized":
            return ResponseResult(
                "finalized", "Everyone accepted. The group is confirmed!", group_uuid, "finalized"
            )
        if outcome == "cancelled":
            return ResponseResult(
                "cancelled",
                "The group could not be confirmed; looking for a replacement.",
                group_uuid,
                "cancelled",
            )
        return ResponseResult(
            "accepted", "Accepted. Waiting for the others.", group_uuid, "proposed"
        )

    async def decline(self, guild_id: int, group_uuid: str, member_id: int) -> ResponseResult:
        group, early = await self._open_group(guild_id, group_uuid)
        if group is None:
            return early  # type: ignore[return-value]
        mm = await self.matches.get_member(group.id, member_id)
        if mm is None:
            return ResponseResult(
                "not_in_group", "You are not part of this group.", group_uuid, group.status
            )
        if mm.response == "declined":
            return ResponseResult(
                "already_responded", "You already declined.", group_uuid, group.status
            )
        if mm.response == "timeout":
            return ResponseResult(
                "already_responded",
                "Your response window already closed.",
                group_uuid,
                group.status,
            )
        # A decline is allowed even after accepting (changing your mind before the others do).
        await self.matches.set_member_response(group.id, member_id, "declined")
        await self._fail_group(group, [(mm, "declined")])
        return ResponseResult(
            "declined", "You declined. The others are being re-queued.", group_uuid, "cancelled"
        )

    async def timeout(self, guild_id: int, group_uuid: str, member_id: int) -> ResponseResult:
        """Force one pending member to time out (normally ``handle_deadline`` does this)."""
        group, early = await self._open_group(guild_id, group_uuid)
        if group is None:
            return early  # type: ignore[return-value]
        mm = await self.matches.get_member(group.id, member_id)
        if mm is None or mm.response is not None:
            return ResponseResult("not_in_group", "Nothing to time out.", group_uuid, group.status)
        await self.matches.respond_if_pending(group.id, member_id, "timeout")
        await self._fail_group(group, [(mm, "timeout")])
        return ResponseResult("cancelled", "Timed out.", group_uuid, "cancelled")

    async def handle_deadline(self, group_uuid: str) -> list[Event]:
        """Deadline job: mark only still-pending members as timed out, exactly once."""
        group = await self.matches.get_group(group_uuid)
        if group is None or group.status != "proposed":
            return []
        deadline = group.confirmation_deadline
        if deadline is not None and deadline > utcnow() + timedelta(seconds=1):
            return []  # fired early (deadline was moved); the job will be re-created
        members = await self.matches.list_members(group.id)
        pending = [m for m in members if m.response is None]
        if not pending:
            await self.try_finalize(group_uuid)
            return self.pop_events()
        for m in pending:
            await self.matches.respond_if_pending(group.id, m.member_id, "timeout")
        await self._fail_group(group, [(m, "timeout") for m in pending])
        return self.pop_events()

    async def member_left(self, guild_id: int, member_id: int) -> int:
        """A member left the server: pull them out of every pending proposal (no penalty)."""
        count = 0
        for group in await self.matches.groups_containing(
            guild_id, [member_id], statuses=("proposed",)
        ):
            mm = await self.matches.get_member(group.id, member_id)
            if mm is not None:
                await self._fail_group(group, [(mm, "left_server")])
                count += 1
        return count

    async def cancel_group(
        self, group_uuid: str, reason: str = "admin", *, rematch: bool = False
    ) -> bool:
        """Admin/force cancel: everyone is released (no penalties)."""
        group = await self.matches.get_group(group_uuid)
        if group is None or group.status != "proposed":
            return False
        await self._fail_group(group, [], reason=reason, rematch=rematch)
        return True

    # ------------------------------------------------------------ finalizing

    async def try_finalize(
        self,
        group_uuid: str,
        *,
        present_member_ids: set[int] | None = None,
        banned_member_ids: set[int] | None = None,
    ) -> str:
        """Finalize if everyone accepted and everyone is still eligible.

        Returns 'finalized', 'cancelled', 'waiting' or 'closed'.
        """
        group = await self.matches.get_group(group_uuid)
        if group is None or group.status != "proposed":
            return "closed"
        members = await self.matches.list_members(group.id)
        if not members or any(m.response != "accepted" for m in members):
            return "waiting"

        problems = await self._eligibility_problems(
            group, members, present_member_ids, banned_member_ids
        )
        if problems == "composition":
            await self._fail_group(group, [], reason="composition_invalid")
            return "cancelled"
        if problems:
            by_id = {m.member_id: m for m in members}
            await self._fail_group(
                group, [(by_id[mid], reason) for mid, reason in problems.items()]
            )
            return "cancelled"

        if not await self.matches.transition(group_uuid, "proposed", "finalized", finalized=True):
            return "closed"  # someone else finalized or cancelled it first
        entry_ids = [m.entry_id for m in members if m.entry_id is not None]
        await self.queue.set_status(sorted(set(entry_ids)), "matched")
        await self.jobs.cancel_matching("confirmation_deadline", "group_uuid", group_uuid)
        await self.session.flush()
        group = await self.matches.get_group(group_uuid)
        assert group is not None
        self._events.append(
            GroupFinalized(
                guild_id=group.guild_id,
                group_uuid=group_uuid,
                status="finalized",
                voice_channel_id=group.voice_channel_id,
                game=group.game,
                activity=group.activity,
                region=group.region,
                member_ids=[m.member_id for m in members],
                roster=roster_view(members),
            )
        )
        return "finalized"

    async def _eligibility_problems(
        self,
        group: MatchGroup,
        members: list[DBMatchMember],
        present: set[int] | None,
        banned: set[int] | None,
    ) -> dict[int, str] | str | None:
        """Per-member problems ({member_id: reason}), 'composition', or None if all fine."""
        problems: dict[int, str] = {}
        human = [
            m.member_id
            for m in members
            if is_real_member(m.member_id, is_guest=m.is_guest, is_test=m.is_test)
        ]
        for m in members:
            if not is_real_member(m.member_id, is_guest=m.is_guest, is_test=m.is_test):
                continue
            if banned and m.member_id in banned:
                problems[m.member_id] = "banned"
            elif present is not None and m.member_id not in present:
                problems[m.member_id] = "left_server"

        # Still in the queue? Profile deletion / leaving the server cancels the entry.
        proposal_entries = {
            e.id: e for e in await self.queue.list_by_proposal(group.proposal_id or "")
        }
        for m in members:
            if m.entry_id is None or m.member_id in problems:
                continue
            entry = proposal_entries.get(m.entry_id)
            if entry is None or entry.status != "reserved":
                problems[m.member_id] = "withdrew"

        window = timedelta(seconds=self.config.finalized_conflict_window_seconds)
        conflicting = await self.matches.conflicting_member_ids(
            group.guild_id,
            human,
            statuses=("finalized",),
            game=group.game,
            activity=group.activity,
            exclude_group_id=group.id,
            finalized_since=utcnow() - window,
        )
        for mid in conflicting:
            problems.setdefault(mid, "in_other_group")
        if problems:
            return problems
        return None if await self._composition_valid(group, members) else "composition"

    async def _composition_valid(self, group: MatchGroup, members: list[DBMatchMember]) -> bool:
        comp = await self.compositions.get(group.guild_id, group.game, group.activity)
        if comp is None:
            return True
        partial = bool((group.roster or {}).get("partial"))
        if not partial and len(members) != comp.group_size:
            return False
        if partial and len(members) > comp.group_size:
            return False
        if comp.role_required and comp.roles:
            wanted = {r["key"]: int(r["count"]) for r in comp.roles}
            counts: dict[str, int] = {}
            for m in members:
                if m.role_assigned is None:
                    return False
                counts[m.role_assigned] = counts.get(m.role_assigned, 0) + 1
            for role, n in counts.items():
                if role not in wanted or n > wanted[role]:
                    return False
            if not partial and counts != wanted:
                return False
        return True

    # ------------------------------------------------------- failure / rematch

    async def _fail_group(
        self,
        group: MatchGroup,
        failures: list[tuple[DBMatchMember, str]],
        *,
        reason: str | None = None,
        rematch: bool = True,
    ) -> None:
        """Cancel a proposal because some members failed, then try to replace them.

        * The failing members' entries are cancelled; a failing PARTY member takes
          the whole party entry with them (the party is a unit).
        * Every other entry is released back to ``queued`` with its original
          ``queued_at`` so those players keep their priority.
        * Matching is re-run immediately; players who already accepted stay
          accepted in the new proposal.
        * Only a non-party decliner/timeout is penalised, never a party.
        """
        cancel_reason = reason or failures[0][1]
        if not await self.matches.transition(
            group.group_uuid, "proposed", "cancelled", reason=cancel_reason
        ):
            return  # another worker already closed this group
        members = await self.matches.list_members(group.id)
        entries = await self.queue.list_by_proposal(group.proposal_id or "")
        party_sizes: dict[str, int] = {}
        for m in members:
            if m.party_id:
                party_sizes[m.party_id] = party_sizes.get(m.party_id, 0) + 1

        failed_entry_ids = {mm.entry_id for mm, _ in failures if mm.entry_id is not None}
        removed = [m for m in members if m.entry_id in failed_entry_ids]
        removed_ids = {m.member_id for m in removed}
        released_entries = [e.id for e in entries if e.id not in failed_entry_ids]
        failed_ids = [mm.member_id for mm, _ in failures]
        now = utcnow()

        await self.queue.set_status(sorted(failed_entry_ids), "cancelled")
        await self.queue.release(released_entries)
        await self.jobs.cancel_matching("confirmation_deadline", "group_uuid", group.group_uuid)

        for mm, why in failures:
            is_party = bool(mm.party_id) and party_sizes.get(mm.party_id or "", 0) > 1
            penalized = (
                why in _FAIL_REASONS
                and not is_party
                and is_real_member(mm.member_id, is_guest=mm.is_guest, is_test=mm.is_test)
            )
            if penalized:
                penalty = await self.penalties.increment_and_set(
                    group.guild_id, mm.member_id, self.config.cooldown_seconds, now
                )
                self._events.append(
                    CooldownApplied(
                        guild_id=group.guild_id,
                        member_id=mm.member_id,
                        reason=why,
                        expires_at=penalty.expires_at,
                    )
                )
            event_cls = MemberTimedOut if why == "timeout" else MemberDeclined
            if why in _FAIL_REASONS:
                self._events.append(
                    event_cls(
                        guild_id=group.guild_id,
                        group_uuid=group.group_uuid,
                        member_id=mm.member_id,
                        party_id=mm.party_id,
                        is_party_member=is_party,
                        penalized=penalized,
                    )
                )
        if cancel_reason in _FAIL_REASONS and group.roster_hash:
            await self.failed.add(
                group.guild_id,
                group.game,
                group.activity,
                group.roster_hash,
                now,
                self.config.failed_roster_ttl_seconds,
            )

        prior_accepted = {
            m.member_id
            for m in members
            if m.response == "accepted" and m.member_id not in removed_ids
        }
        released_humans = [
            m.member_id
            for m in members
            if m.member_id not in removed_ids
            and is_real_member(m.member_id, is_guest=m.is_guest, is_test=m.is_test)
        ]
        view = roster_view(await self.matches.list_members(group.id))
        self._events.append(
            GroupCancelled(
                guild_id=group.guild_id,
                group_uuid=group.group_uuid,
                reason=cancel_reason,
                member_ids=[m.member_id for m in members],
                roster=view,
            )
        )
        self._events.append(
            ReplacementStarted(
                guild_id=group.guild_id,
                group_uuid=group.group_uuid,
                failed_member_ids=failed_ids,
                removed_member_ids=sorted(removed_ids),
                released_member_ids=released_humans,
                reason=cancel_reason,
            )
        )
        await self.session.flush()
        if not rematch:
            return

        # Try to replace the missing players straight away.
        from tianling.services.queue_service import QueueService

        qs = QueueService(self.session, self.config)
        new_groups = await qs.run_matching(
            group.guild_id, group.game, group.activity, carry_accepted=prior_accepted
        )
        self._events.extend(qs.pop_events())

        released_set = set(released_humans)
        successor = None
        for uuid_ in new_groups:
            new_group = await self.matches.get_group(uuid_)
            if new_group is None:
                continue
            new_members = await self.matches.list_members(new_group.id)
            if released_set.intersection(m.member_id for m in new_members):
                successor = (new_group, new_members)
                break
        if successor is not None:
            self._events.append(
                ReplacementSucceeded(
                    guild_id=group.guild_id,
                    group_uuid=group.group_uuid,
                    new_group_uuid=successor[0].group_uuid,
                    failed_member_ids=failed_ids,
                    roster=roster_view(successor[1]),
                )
            )
            return

        # No replacement available: policy decides whether the others keep waiting.
        released = self.config.replacement_policy != "cancel"
        if not released:
            await self.queue.set_status(released_entries, "cancelled")
        self._events.append(
            ReplacementFailed(
                guild_id=group.guild_id,
                group_uuid=group.group_uuid,
                failed_member_ids=failed_ids,
                released_member_ids=released_humans,
                released=released,
                policy=self.config.replacement_policy,
            )
        )
        await self.session.flush()
