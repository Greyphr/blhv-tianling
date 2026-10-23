"""Service events (plain dataclasses, no Discord)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


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
    roster: dict[str, Any] | None = None


@dataclass(slots=True)
class MemberAccepted:
    guild_id: int
    group_uuid: str
    member_id: int
    party_id: str | None


@dataclass(slots=True)
class MemberDeclined:
    guild_id: int
    group_uuid: str
    member_id: int
    party_id: str | None
    is_party_member: bool


@dataclass(slots=True)
class MemberTimedOut:
    guild_id: int
    group_uuid: str
    member_id: int
    party_id: str | None
    is_party_member: bool


@dataclass(slots=True)
class ReplacementSucceeded:
    guild_id: int
    group_uuid: str
    replaced_member_id: int
    new_member_id: int


@dataclass(slots=True)
class ReplacementFailed:
    guild_id: int
    group_uuid: str
    failed_member_id: int
    released: bool


@dataclass(slots=True)
class GroupFinalized:
    guild_id: int
    group_uuid: str
    status: str
    voice_channel_id: int | None = None


@dataclass(slots=True)
class QueueExpired:
    guild_id: int
    member_id: int
    game: str
    activity: str


@dataclass(slots=True)
class StillLooking:
    guild_id: int
    member_id: int
    game: str
    activity: str


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
class CooldownApplied:
    guild_id: int
    member_id: int
    reason: str
    expires_at: datetime | None
