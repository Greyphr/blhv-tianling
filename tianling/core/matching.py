"""Pure, deterministic matchmaking for Tianling.

The engine has no Discord or database dependencies.  Callers provide queue
entries, composition definitions, and the current time; the engine returns
stable roster proposals that can be persisted or presented by the bot layer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from tianling.core.ranks import Rank, RankError, rank_gap
from tianling.core.seed_loader import CompositionEntry


class MatchingError(ValueError):
    """Base class for matching errors."""


class MatchingValidationError(MatchingError):
    """Raised when queue input is internally inconsistent."""


class MatchingConfigError(MatchingError):
    """Raised when matching configuration is internally inconsistent."""


@dataclass(frozen=True)
class RelaxationStep:
    """Time-based relaxation thresholds for one queue level."""

    after_minutes: int = 0
    rank_gap_bonus: int = 0
    allow_adjacent_regions: bool = False
    allow_cross_region: bool = False


@dataclass(frozen=True)
class MatchingConfig:
    """Configuration for time-based matching relaxation."""

    relaxation: tuple[RelaxationStep, ...] = ()
    region_adjacency: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        steps = tuple(sorted(self.relaxation, key=lambda step: step.after_minutes))
        if any(step.after_minutes < 0 for step in steps):
            raise MatchingConfigError("relaxation after_minutes cannot be negative")
        if any(step.rank_gap_bonus < 0 for step in steps):
            raise MatchingConfigError("relaxation rank_gap_bonus cannot be negative")
        minutes = [step.after_minutes for step in steps]
        if len(set(minutes)) != len(minutes):
            raise MatchingConfigError("relaxation after_minutes values must be unique")
        adjacency = self.region_adjacency or {}
        object.__setattr__(self, "relaxation", steps)
        object.__setattr__(
            self,
            "region_adjacency",
            {
                str(region).casefold(): tuple(str(neighbor).casefold() for neighbor in neighbors)
                for region, neighbors in adjacency.items()
            },
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | Any | None) -> MatchingConfig:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            steps_value = value.get("relaxation", ())
            adjacency_value = value.get("region_adjacency", {})
        else:
            steps_value = getattr(value, "relaxation", ())
            adjacency_value = getattr(value, "region_adjacency", {})
        steps: list[RelaxationStep] = []
        for step in steps_value or ():
            if isinstance(step, RelaxationStep):
                steps.append(step)
            elif isinstance(step, Mapping):
                steps.append(
                    RelaxationStep(
                        after_minutes=int(step.get("after_minutes", 0)),
                        rank_gap_bonus=int(step.get("rank_gap_bonus", 0)),
                        allow_adjacent_regions=bool(step.get("allow_adjacent_regions", False)),
                        allow_cross_region=bool(step.get("allow_cross_region", False)),
                    )
                )
            else:
                steps.append(
                    RelaxationStep(
                        after_minutes=int(getattr(step, "after_minutes", 0)),
                        rank_gap_bonus=int(getattr(step, "rank_gap_bonus", 0)),
                        allow_adjacent_regions=bool(
                            getattr(step, "allow_adjacent_regions", False)
                        ),
                        allow_cross_region=bool(getattr(step, "allow_cross_region", False)),
                    )
                )
        adjacency: dict[str, tuple[str, ...]] = {}
        for region, neighbors in (adjacency_value or {}).items():
            adjacency[str(region)] = tuple(str(neighbor) for neighbor in neighbors)
        return cls(relaxation=tuple(steps), region_adjacency=adjacency)


@dataclass(frozen=True)
class MatchMember:
    """One person inside a queue entry."""

    member_id: int
    rank: Rank | None = None
    preferred_roles: tuple[str, ...] = ()
    acceptable_roles: tuple[str, ...] = ()
    declared_role: str | None = None
    preferred_heroes: tuple[str, ...] = ()
    is_guest: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "preferred_roles", tuple(_role(role) for role in self.preferred_roles)
        )
        object.__setattr__(
            self, "acceptable_roles", tuple(_role(role) for role in self.acceptable_roles)
        )
        object.__setattr__(self, "declared_role", _optional_role(self.declared_role))
        object.__setattr__(
            self,
            "preferred_heroes",
            tuple(str(hero).strip() for hero in self.preferred_heroes if str(hero).strip()),
        )


@dataclass(frozen=True)
class MatchEntry:
    """An atomic solo or party queue entry."""

    entry_id: str
    game: str
    activity: str
    members: tuple[MatchMember, ...]
    queue_type: str = "default"
    region: str | None = None
    platform: str | None = None
    party_id: str | None = None
    leader_member_id: int | None = None
    queued_at: datetime | None = None
    expires_at: datetime | None = None
    sibling_entry_ids: tuple[str, ...] = ()
    either_group: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", str(self.entry_id))
        object.__setattr__(self, "game", str(self.game).strip().casefold())
        object.__setattr__(self, "activity", str(self.activity).strip().casefold())
        object.__setattr__(self, "queue_type", str(self.queue_type or "default").strip().casefold())
        object.__setattr__(self, "region", _optional_text(self.region))
        object.__setattr__(self, "platform", _optional_text(self.platform))
        object.__setattr__(self, "party_id", _optional_text(self.party_id))
        object.__setattr__(
            self, "sibling_entry_ids", tuple(str(item) for item in self.sibling_entry_ids)
        )
        object.__setattr__(self, "either_group", _optional_text(self.either_group))

    @property
    def party_size(self) -> int:
        return len(self.members)

    @property
    def member_ids(self) -> tuple[int, ...]:
        return tuple(member.member_id for member in self.members)


@dataclass(frozen=True)
class RosterMember:
    """A member with the role assigned by the engine."""

    entry_id: str
    member_id: int
    role: str | None
    rank: Rank | None
    preferred_role: bool
    declared_role: str | None = None
    party_id: str | None = None
    is_guest: bool = False


@dataclass(frozen=True)
class MatchProposal:
    """A proposed roster.  ``direct`` means a full pre-made party."""

    game: str
    activity: str
    queue_type: str
    region: str | None
    members: tuple[RosterMember, ...]
    entry_ids: tuple[str, ...]
    sibling_entry_ids: tuple[str, ...]
    partial: bool
    relaxation_level: int
    direct: bool
    score: tuple[int, ...]
    roster_hash: str

    @property
    def member_ids(self) -> tuple[int, ...]:
        return tuple(member.member_id for member in self.members)

    @property
    def group_size(self) -> int:
        return len(self.members)

    @property
    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.game,
            self.activity,
            self.direct,
            self.partial,
            self.score,
            self.entry_ids,
            self.roster_hash,
        )


@dataclass(frozen=True)
class _Assigned:
    entry: MatchEntry
    member: MatchMember
    role: str | None
    preferred: bool


def _role(value: str) -> str:
    return str(value).strip().casefold()


def _optional_role(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _role(value)
    return cleaned or None


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _minutes_between(earlier: datetime | None, now: datetime) -> float:
    if earlier is None:
        return 0.0
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=UTC)
    return max(0.0, (now - earlier).total_seconds() / 60.0)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _entry_wait_minutes(entry: MatchEntry, now: datetime) -> float:
    return _minutes_between(_as_utc(entry.queued_at), now)


def _entry_relaxation_level(entry: MatchEntry, config: MatchingConfig, now: datetime) -> int:
    wait = _entry_wait_minutes(entry, now)
    level = 0
    for index, step in enumerate(config.relaxation, start=1):
        if wait >= step.after_minutes:
            level = index
        else:
            break
    return level


def _step_for_level(config: MatchingConfig, level: int) -> RelaxationStep:
    if level <= 0:
        return RelaxationStep()
    return config.relaxation[min(level, len(config.relaxation)) - 1]


def _entry_compatible(entry: MatchEntry, composition: CompositionEntry, now: datetime) -> bool:
    if entry.game != composition.game.casefold():
        return False
    if entry.activity != composition.activity.casefold():
        return False
    if entry.queue_type not in {"", "default", composition.activity.casefold()}:
        return False
    if not entry.members:
        return False
    expires_at = _as_utc(entry.expires_at)
    if expires_at is not None and expires_at <= now:
        return False
    if entry.party_size > composition.group_size:
        return False
    if entry.party_size > composition.party_max_size:
        return False
    return True


def _role_keys(composition: CompositionEntry) -> tuple[str, ...]:
    return tuple(_role(role.key) for role in composition.roles)


def _member_role_options(member: MatchMember, role_keys: tuple[str, ...]) -> tuple[str, ...]:
    if not role_keys:
        return ()
    if member.declared_role is not None:
        return (member.declared_role,) if member.declared_role in role_keys else ()
    permitted: list[str] = []
    for role in (*member.preferred_roles, *member.acceptable_roles):
        if role in role_keys and role not in permitted:
            permitted.append(role)
    return tuple(permitted) if permitted else role_keys


def _flexible_roles(member: MatchMember, role_keys: tuple[str, ...]) -> tuple[str, ...]:
    permitted: list[str] = []
    for role in (*member.preferred_roles, *member.acceptable_roles):
        if (not role_keys or role in role_keys) and role not in permitted:
            permitted.append(role)
    if permitted:
        return tuple(permitted)
    if role_keys:
        return role_keys
    return ()


def _assign_roles(
    composition: CompositionEntry,
    entries: Sequence[MatchEntry],
    *,
    complete: bool,
) -> tuple[list[_Assigned], int, int] | None:
    role_keys = _role_keys(composition)
    required = composition.role_required
    if required and not role_keys:
        raise MatchingConfigError(
            f"{composition.game}/{composition.activity} requires roles but defines none"
        )

    members: list[tuple[int, MatchEntry, MatchMember, tuple[str, ...]]] = []
    for entry in entries:
        for member in entry.members:
            options = _member_role_options(member, role_keys) if required else _flexible_roles(
                member, role_keys
            )
            if required and not options:
                return None
            members.append((len(members), entry, member, options))

    if not required:
        assigned = [
            _Assigned(
                entry=entry,
                member=member,
                role=_choose_optional_role(member, options),
                preferred=_is_preferred(member, _choose_optional_role(member, options)),
            )
            for _, entry, member, options in members
        ]
        return assigned, _preferred_misses(assigned), _displaced(assigned)

    slots = Counter({_role(role.key): role.count for role in composition.roles})
    ordered = sorted(
        members,
        key=lambda item: (
            len(item[3]),
            item[1].entry_id,
            item[2].member_id,
        ),
    )
    result: list[_Assigned | None] = [None] * len(members)

    def assign(index: int) -> bool:
        if index == len(ordered):
            return not complete or not any(slots.values())
        original_index, entry, member, options = ordered[index]
        for role in options:
            if slots[role] <= 0:
                continue
            slots[role] -= 1
            result[original_index] = _Assigned(
                entry=entry,
                member=member,
                role=role,
                preferred=_is_preferred(member, role),
            )
            if assign(index + 1):
                return True
            slots[role] += 1
            result[original_index] = None
        return False

    if not assign(0):
        return None
    assigned = [item for item in result if item is not None]
    if len(assigned) != len(members):
        return None
    return assigned, _preferred_misses(assigned), _displaced(assigned)


def _choose_optional_role(member: MatchMember, options: tuple[str, ...]) -> str | None:
    if member.declared_role is not None:
        return member.declared_role
    for preferred in member.preferred_roles:
        if not options or preferred in options:
            return preferred
    for acceptable in member.acceptable_roles:
        if not options or acceptable in options:
            return acceptable
    return options[0] if options else None


def _is_preferred(member: MatchMember, role: str | None) -> bool:
    return role is not None and role in member.preferred_roles


def _preferred_misses(assigned: Sequence[_Assigned]) -> int:
    misses = 0
    for item in assigned:
        if item.member.preferred_roles and not item.preferred:
            misses += 1
    return misses


def _displaced(assigned: Sequence[_Assigned]) -> int:
    count = 0
    for item in assigned:
        if item.member.declared_role is not None:
            if item.role != item.member.declared_role:
                count += 1
        elif item.member.preferred_roles and not item.preferred:
            count += 1
    return count


def _rank_spread(members: Iterable[MatchMember]) -> int | None:
    ranks = [member.rank for member in members if member.rank is not None]
    if len(ranks) < 2:
        return 0
    try:
        return max(
            rank_gap(ranks[left], ranks[right])
            for left in range(len(ranks))
            for right in range(left + 1, len(ranks))
        )
    except RankError:
        return None


def _party_rank_spread(entry: MatchEntry) -> int | None:
    return _rank_spread(entry.members)


def _regions_compatible(
    regions: Iterable[str | None],
    step: RelaxationStep,
    config: MatchingConfig,
) -> bool:
    unique = {region.casefold() for region in regions if region}
    if len(unique) <= 1:
        return True
    if step.allow_cross_region:
        return True
    if not step.allow_adjacent_regions:
        return False
    adjacency = config.region_adjacency
    for region in unique:
        neighbors = set(adjacency.get(region, ()))
        if not unique.issubset(neighbors | {region}):
            return False
    return True


def _resolved_region(entries: Sequence[MatchEntry]) -> str | None:
    regions = {entry.region for entry in entries if entry.region}
    if len(regions) == 1:
        return next(iter(regions))
    if not regions:
        return None
    return "mixed"


def _members_overlap(left: Sequence[MatchEntry], right: MatchEntry) -> bool:
    existing = {member.member_id for entry in left for member in entry.members}
    return any(member.member_id in existing for member in right.members)


def _entries_conflict(left: Sequence[MatchEntry], right: MatchEntry) -> bool:
    existing_ids = {entry.entry_id for entry in left}
    if right.entry_id in existing_ids:
        return True
    if any(sibling in existing_ids for sibling in right.sibling_entry_ids):
        return True
    for entry in left:
        if any(sibling == right.entry_id for sibling in entry.sibling_entry_ids):
            return True
        if (
            entry.either_group is not None
            and right.either_group is not None
            and entry.either_group == right.either_group
        ):
            return True
    return _members_overlap(left, right)


def _hero_overlap(entries: Sequence[MatchEntry]) -> int:
    overlap = 0
    for index, entry in enumerate(entries):
        for other in entries[index + 1 :]:
            for member in entry.members:
                for other_member in other.members:
                    overlap += len(
                        set(member.preferred_heroes) & set(other_member.preferred_heroes)
                    )
    return overlap


def _roster_hash(
    composition: CompositionEntry,
    assignments: Sequence[_Assigned],
) -> str:
    payload = "|".join(
        [
            composition.game,
            composition.activity,
            *sorted(
                f"{item.entry.entry_id}:{item.member.member_id}:{item.role or ''}"
                for item in assignments
            ),
        ]
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _make_proposal(
    composition: CompositionEntry,
    entries: Sequence[MatchEntry],
    assignments: Sequence[_Assigned],
    *,
    partial: bool,
    relaxation_level: int,
    direct: bool,
    preferred_misses: int,
    displaced: int,
    now: datetime,
) -> MatchProposal:
    roster_members = tuple(
        sorted(
            (
                RosterMember(
                    entry_id=item.entry.entry_id,
                    member_id=item.member.member_id,
                    role=item.role,
                    rank=item.member.rank,
                    preferred_role=item.preferred,
                    declared_role=item.member.declared_role,
                    party_id=item.entry.party_id,
                    is_guest=item.member.is_guest,
                )
                for item in assignments
            ),
            key=lambda member: (member.entry_id, member.member_id),
        )
    )
    entry_ids = tuple(sorted(entry.entry_id for entry in entries))
    sibling_ids = tuple(
        sorted(
            {
                sibling
                for entry in entries
                for sibling in entry.sibling_entry_ids
                if sibling not in set(entry_ids)
            }
        )
    )
    solo_count = sum(1 for entry in entries if entry.party_size == 1)
    rank_spread = _rank_spread(
        member for entry in entries for member in entry.members
    )
    if rank_spread is None:
        rank_spread = 10**9
    oldest_wait = max((_entry_wait_minutes(entry, now) for entry in entries), default=0.0)
    total_wait = sum((_entry_wait_minutes(entry, now) for entry in entries), start=0.0)
    score = (
        0 if not partial else 1,
        solo_count,
        displaced,
        preferred_misses,
        rank_spread,
        -int(oldest_wait),
        -int(total_wait),
        _hero_overlap(entries),
        -len(roster_members),
    )
    return MatchProposal(
        game=composition.game,
        activity=composition.activity,
        queue_type=composition.activity,
        region=_resolved_region(entries),
        members=roster_members,
        entry_ids=entry_ids,
        sibling_entry_ids=sibling_ids,
        partial=partial,
        relaxation_level=relaxation_level,
        direct=direct,
        score=score,
        roster_hash=_roster_hash(composition, assignments),
    )


def _validate_candidate(
    composition: CompositionEntry,
    entries: Sequence[MatchEntry],
    *,
    complete: bool,
    step: RelaxationStep,
    config: MatchingConfig,
) -> tuple[list[_Assigned], int, int] | None:
    if complete and sum(entry.party_size for entry in entries) != composition.group_size:
        return None
    if not complete and sum(entry.party_size for entry in entries) >= composition.group_size:
        return None
    assignments = _assign_roles(composition, entries, complete=complete)
    if assignments is None:
        return None
    assigned, preferred_misses, displaced = assignments

    max_gap = composition.allowed_rank_gap + step.rank_gap_bonus
    total_spread = _rank_spread(
        member for entry in entries for member in entry.members
    )
    if total_spread is None or total_spread > max_gap:
        return None

    party_gap = composition.max_party_rank_gap + step.rank_gap_bonus
    for entry in entries:
        spread = _party_rank_spread(entry)
        if spread is None or spread > party_gap:
            return None

    if not _regions_compatible(
        (entry.region for entry in entries),
        step,
        config,
    ):
        return None
    return assigned, preferred_misses, displaced


def _expired(entry: MatchEntry, now: datetime) -> bool:
    expires_at = _as_utc(entry.expires_at)
    return expires_at is not None and expires_at <= now


def _search_exact(
    composition: CompositionEntry,
    entries: Sequence[MatchEntry],
    *,
    step: RelaxationStep,
    level: int,
    config: MatchingConfig,
    now: datetime,
    failed_hashes: Collection[str],
) -> list[MatchProposal]:
    target = composition.group_size
    results: list[MatchProposal] = []
    ordered = sorted(
        entries,
        key=lambda entry: (
            _as_utc(entry.queued_at) or now,
            entry.entry_id,
        ),
    )
    chosen: list[MatchEntry] = []
    size = 0

    def search(start: int) -> None:
        nonlocal size
        if size == target:
            if level > 0 and max(
                _entry_relaxation_level(entry, config, now) for entry in chosen
            ) < level:
                return
            validated = _validate_candidate(
                composition,
                chosen,
                complete=True,
                step=step,
                config=config,
            )
            if validated is None:
                return
            assigned, preferred_misses, displaced = validated
            proposal = _make_proposal(
                composition,
                chosen,
                assigned,
                partial=False,
                relaxation_level=level,
                direct=False,
                preferred_misses=preferred_misses,
                displaced=displaced,
                now=now,
            )
            if proposal.roster_hash not in failed_hashes:
                results.append(proposal)
            return
        if size > target:
            return
        remaining = target - size
        for index in range(start, len(ordered)):
            entry = ordered[index]
            if entry.party_size > remaining:
                continue
            if _entries_conflict(chosen, entry):
                continue
            chosen.append(entry)
            size += entry.party_size
            search(index + 1)
            size -= entry.party_size
            chosen.pop()

    search(0)
    return results


def _search_partial_at_size(
    composition: CompositionEntry,
    ordered: Sequence[MatchEntry],
    *,
    size_target: int,
    step: RelaxationStep,
    level: int,
    config: MatchingConfig,
    now: datetime,
    failed_hashes: Collection[str],
) -> list[MatchProposal]:
    results: list[MatchProposal] = []
    chosen: list[MatchEntry] = []
    size = 0

    def search(start: int) -> None:
        nonlocal size
        if size == size_target:
            if level > 0 and max(
                _entry_relaxation_level(entry, config, now) for entry in chosen
            ) < level:
                return
            validated = _validate_candidate(
                composition,
                chosen,
                complete=False,
                step=step,
                config=config,
            )
            if validated is None:
                return
            assigned, preferred_misses, displaced = validated
            proposal = _make_proposal(
                composition,
                chosen,
                assigned,
                partial=True,
                relaxation_level=level,
                direct=False,
                preferred_misses=preferred_misses,
                displaced=displaced,
                now=now,
            )
            if proposal.roster_hash not in failed_hashes:
                results.append(proposal)
            return
        if size > size_target:
            return
        remaining = size_target - size
        for index in range(start, len(ordered)):
            entry = ordered[index]
            if entry.party_size > remaining:
                continue
            if _entries_conflict(chosen, entry):
                continue
            chosen.append(entry)
            size += entry.party_size
            search(index + 1)
            size -= entry.party_size
            chosen.pop()

    search(0)
    return results


def _search_partial(
    composition: CompositionEntry,
    entries: Sequence[MatchEntry],
    *,
    step: RelaxationStep,
    level: int,
    config: MatchingConfig,
    now: datetime,
    failed_hashes: Collection[str],
    min_size: int,
) -> list[MatchProposal]:
    ordered = sorted(
        entries,
        key=lambda entry: (
            _as_utc(entry.queued_at) or now,
            entry.entry_id,
        ),
    )
    for size_target in range(composition.group_size - 1, min_size - 1, -1):
        results = _search_partial_at_size(
            composition,
            ordered,
            size_target=size_target,
            step=step,
            level=level,
            config=config,
            now=now,
            failed_hashes=failed_hashes,
        )
        if results:
            return results
    return []


def _take_non_overlapping(
    proposals: Iterable[MatchProposal],
    used_entries: set[str],
    used_members: set[int],
    *,
    limit: int | None,
) -> tuple[list[MatchProposal], set[str], set[int]]:
    selected: list[MatchProposal] = []
    for proposal in sorted(proposals, key=lambda item: item.sort_key):
        if limit is not None and len(selected) >= limit:
            break
        blocked_entries = set(proposal.entry_ids) | set(proposal.sibling_entry_ids)
        if blocked_entries & used_entries:
            continue
        member_ids = set(proposal.member_ids)
        if member_ids & used_members:
            continue
        selected.append(proposal)
        used_entries.update(blocked_entries)
        used_members.update(member_ids)
    return selected, used_entries, used_members


def _direct_proposal(
    composition: CompositionEntry,
    entry: MatchEntry,
    now: datetime,
    failed_hashes: Collection[str],
    config: MatchingConfig,
) -> MatchProposal | None:
    level = _entry_relaxation_level(entry, config, now)
    validated = _validate_candidate(
        composition,
        [entry],
        complete=True,
        step=_step_for_level(config, level),
        config=config,
    )
    if validated is None:
        return None
    assigned, preferred_misses, displaced = validated
    proposal = _make_proposal(
        composition,
        [entry],
        assigned,
        partial=False,
        relaxation_level=level,
        direct=True,
        preferred_misses=preferred_misses,
        displaced=displaced,
        now=now,
    )
    return None if proposal.roster_hash in failed_hashes else proposal


def find_matches(
    entries: Sequence[MatchEntry],
    compositions: Sequence[CompositionEntry],
    *,
    config: MatchingConfig | Mapping[str, Any] | Any | None = None,
    now: datetime | None = None,
    failed_hashes: Collection[str] = (),
    allow_partial: bool = False,
    min_partial_size: int = 2,
    max_matches: int | None = None,
) -> list[MatchProposal]:
    """Return deterministic, non-overlapping roster proposals.

    Full pre-made parties are returned as ``direct`` proposals and skip the
    search.  Other entries are matched at the lowest applicable relaxation
    level first.  Partial rosters are only returned when both the composition
    and the caller allow them, and only when no complete roster is available.
    """
    if min_partial_size < 2:
        raise MatchingValidationError("min_partial_size must be at least 2")
    if max_matches is not None and max_matches < 0:
        raise MatchingValidationError("max_matches cannot be negative")
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    reference = reference.astimezone(UTC)
    matching_config = MatchingConfig.from_mapping(config)

    seen_ids: set[str] = set()
    for entry in entries:
        if entry.entry_id in seen_ids:
            raise MatchingValidationError(f"duplicate entry_id {entry.entry_id!r}")
        seen_ids.add(entry.entry_id)
        member_ids = entry.member_ids
        if len(set(member_ids)) != len(member_ids):
            raise MatchingValidationError(
                f"entry {entry.entry_id!r} contains duplicate member ids"
            )

    result: list[MatchProposal] = []
    used_entries: set[str] = set()
    used_members: set[int] = set()

    def limit_reached() -> bool:
        return max_matches is not None and len(result) >= max_matches

    for composition in sorted(
        compositions,
        key=lambda item: (item.game.casefold(), item.activity.casefold()),
    ):
        if limit_reached():
            break
        candidate_entries = [
            entry
            for entry in entries
            if _entry_compatible(entry, composition, reference)
            and not _expired(entry, reference)
            and entry.entry_id not in used_entries
        ]
        direct: list[MatchProposal] = []
        for entry in sorted(candidate_entries, key=lambda item: (item.entry_id,)):
            if limit_reached():
                break
            if entry.party_size != composition.group_size:
                continue
            proposal = _direct_proposal(
                composition,
                entry,
                reference,
                failed_hashes,
                matching_config,
            )
            if proposal is not None:
                direct.append(proposal)
        selected, used_entries, used_members = _take_non_overlapping(
            direct,
            used_entries,
            used_members,
            limit=(None if max_matches is None else max_matches - len(result)),
        )
        result.extend(selected)

        remaining_candidates = [
            entry
            for entry in candidate_entries
            if entry.entry_id not in used_entries
            and not any(member.member_id in used_members for member in entry.members)
            and entry.party_size != composition.group_size
        ]
        for level in range(len(matching_config.relaxation) + 1):
            if limit_reached():
                break
            step = _step_for_level(matching_config, level)
            eligible = [
                entry
                for entry in remaining_candidates
                if entry.entry_id not in used_entries
                and not any(member.member_id in used_members for member in entry.members)
            ]
            if level > 0 and not any(
                _entry_relaxation_level(entry, matching_config, reference) >= level
                for entry in eligible
            ):
                continue
            proposals = _search_exact(
                composition,
                eligible,
                step=step,
                level=level,
                config=matching_config,
                now=reference,
                failed_hashes=failed_hashes,
            )
            selected, used_entries, used_members = _take_non_overlapping(
                proposals,
                used_entries,
                used_members,
                limit=(None if max_matches is None else max_matches - len(result)),
            )
            result.extend(selected)
            if limit_reached():
                break

        if limit_reached():
            break
        if not allow_partial or not composition.partial_fill_allowed:
            continue

        remaining_candidates = [
            entry
            for entry in remaining_candidates
            if entry.entry_id not in used_entries
            and not any(member.member_id in used_members for member in entry.members)
            and entry.party_size != composition.group_size
        ]
        if not remaining_candidates:
            continue
        for level in range(len(matching_config.relaxation) + 1):
            if limit_reached():
                break
            step = _step_for_level(matching_config, level)
            eligible = [
                entry
                for entry in remaining_candidates
                if entry.entry_id not in used_entries
                and not any(member.member_id in used_members for member in entry.members)
            ]
            if level > 0 and not any(
                _entry_relaxation_level(entry, matching_config, reference) >= level
                for entry in eligible
            ):
                continue
            proposals = _search_partial(
                composition,
                eligible,
                step=step,
                level=level,
                config=matching_config,
                now=reference,
                failed_hashes=failed_hashes,
                min_size=min_partial_size,
            )
            selected, used_entries, used_members = _take_non_overlapping(
                proposals,
                used_entries,
                used_members,
                limit=(None if max_matches is None else max_matches - len(result)),
            )
            result.extend(selected)
            if selected:
                break
            if limit_reached():
                break

    return sorted(result, key=lambda item: item.sort_key)


__all__ = [
    "MatchEntry",
    "MatchMember",
    "MatchProposal",
    "MatchingConfig",
    "MatchingConfigError",
    "MatchingError",
    "MatchingValidationError",
    "RelaxationStep",
    "RosterMember",
    "find_matches",
]
