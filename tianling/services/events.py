"""Service events (plain dataclasses, no Discord).

Services append these to an in-memory list; the caller commits the transaction
and then hands the events to the Discord dispatcher.  Every event carries enough
data for the UI to render it without another database read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

RosterView = list[dict[str, Any]]


@dataclass(slots=True)
class ProposalCreated:
    guild_id: int
    group_uuid: str
    game: str
    activity: str
    queue_type: str
    region: str | None
    party_ids: list[str]
    member_ids: list[int]
    confirmation_deadline: datetime | None
    auto_confirm: bool
    roster: RosterView = field(default_factory=list)
    pending_member_ids: list[int] = field(default_factory=list)
    partial: bool = False


@dataclass(slots=True)
class MemberAccepted:
    guild_id: int
    group_uuid: str
    member_id: int
    party_id: str | None
    roster: RosterView = field(default_factory=list)


@dataclass(slots=True)
class MemberDeclined:
    guild_id: int
    group_uuid: str
    member_id: int
    party_id: str | None
    is_party_member: bool
    penalized: bool = False


@dataclass(slots=True)
class MemberTimedOut:
    guild_id: int
    group_uuid: str
    member_id: int
    party_id: str | None
    is_party_member: bool
    penalized: bool = False


@dataclass(slots=True)
class ReplacementStarted:
    guild_id: int
    group_uuid: str
    failed_member_ids: list[int]
    removed_member_ids: list[int]
    released_member_ids: list[int]
    reason: str


@dataclass(slots=True)
class ReplacementSucceeded:
    guild_id: int
    group_uuid: str
    new_group_uuid: str
    failed_member_ids: list[int]
    roster: RosterView = field(default_factory=list)


@dataclass(slots=True)
class ReplacementFailed:
    guild_id: int
    group_uuid: str
    failed_member_ids: list[int]
    released_member_ids: list[int]
    released: bool
    policy: str = "requeue"


@dataclass(slots=True)
class GroupFinalized:
    guild_id: int
    group_uuid: str
    status: str
    voice_channel_id: int | None = None
    game: str = ""
    activity: str = ""
    region: str | None = None
    member_ids: list[int] = field(default_factory=list)
    roster: RosterView = field(default_factory=list)


@dataclass(slots=True)
class GroupCancelled:
    guild_id: int
    group_uuid: str
    reason: str
    member_ids: list[int] = field(default_factory=list)
    roster: RosterView = field(default_factory=list)


@dataclass(slots=True)
class CooldownApplied:
    guild_id: int
    member_id: int
    reason: str
    expires_at: datetime | None


@dataclass(slots=True)
class QueueExpired:
    guild_id: int
    member_id: int
    game: str
    activity: str
    member_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class StillLooking:
    guild_id: int
    member_id: int
    game: str
    activity: str
    expires_at: datetime | None = None
    member_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class PartyCreated:
    guild_id: int
    party_id: str
    leader_member_id: int
    member_ids: list[int]


@dataclass(slots=True)
class PartyUpdated:
    guild_id: int
    party_id: str
    member_ids: list[int]


@dataclass(slots=True)
class PartyMemberLeft:
    guild_id: int
    party_id: str
    member_id: int


@dataclass(slots=True)
class PartyInvite:
    guild_id: int
    party_id: str
    leader_member_id: int
    invited_member_id: int
    expires_at: datetime | None


@dataclass(slots=True)
class StaleProfiles:
    guild_id: int
    #: member_id -> games whose profile has not been updated recently
    members: dict[int, list[str]]


Event = (
    ProposalCreated
    | MemberAccepted
    | MemberDeclined
    | MemberTimedOut
    | ReplacementStarted
    | ReplacementSucceeded
    | ReplacementFailed
    | GroupFinalized
    | GroupCancelled
    | CooldownApplied
    | QueueExpired
    | StillLooking
    | PartyCreated
    | PartyUpdated
    | PartyMemberLeft
    | PartyInvite
    | StaleProfiles
)
