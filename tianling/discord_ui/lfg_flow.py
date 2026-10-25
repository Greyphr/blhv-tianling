"""LFG/party logic with no discord.py imports: profiles -> queue/party requests.

Each function opens its own short transaction, commits, and returns events for the
caller to dispatch afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass

from tianling.core.party_slots import open_slots, slots_text
from tianling.core.seed_loader import CompositionEntry, GameEntry, SeedLoader
from tianling.db.repositories.parties import PartyRepository
from tianling.discord_ui.registration_flow import RankOptions
from tianling.services.events import Event
from tianling.services.profile_service import ProfileService
from tianling.services.queue_service import InvalidQueueRequest, QueueService, new_guest_id
from tianling.services.settings import load_match_settings


@dataclass
class MemberSpec:
    """One person in a party being built."""

    member_id: int
    role: str | None = None
    snapshot: dict | None = None
    accepted_roles: list[str] | None = None
    is_guest: bool = False
    label: str = ""


def snapshot_from_profile(profile) -> dict:
    return {
        "tier": profile.current_rank_tier,
        "division": profile.current_division,
        "queue_type": profile.current_queue_type or "default",
        "order": profile.current_rank_order_value,
        "label": " ".join(x for x in (profile.current_rank_tier, profile.current_division) if x),
    }


def spec_from_profile(member_id: int, profile, *, label: str = "") -> MemberSpec:
    roles = list(profile.preferred_roles or [])
    return MemberSpec(
        member_id=member_id,
        role=roles[0] if roles else None,
        snapshot=snapshot_from_profile(profile),
        accepted_roles=list(profile.acceptable_roles or []) or None,
        label=label,
    )


def resolve_activity(game: GameEntry, activity: str) -> tuple[str, list[str]]:
    """'either' becomes the first activity plus the others as 'Either' siblings."""
    if activity == "either":
        return game.activities[0], list(game.activities[1:])
    return activity, []


def guest_spec(
    options: RankOptions,
    roles: list[dict],
    *,
    tier: str,
    division: str | None,
    role: str | None,
) -> MemberSpec:
    """Build a guest from typed text, validating against the rank ladder and roles."""
    tiers = {t.lower(): t for t in options.tiers}
    canonical = tiers.get(tier.strip().lower())
    if canonical is None:
        raise InvalidQueueRequest(
            f"Unknown rank tier '{tier}'. Options: {', '.join(options.tiers)}"
        )
    divs = options.divisions.get(canonical, [])
    chosen: str | None = None
    if divs:
        wanted = {d.lower(): d for d in divs}
        chosen = wanted.get((division or "").strip().lower())
        if chosen is None:
            raise InvalidQueueRequest(f"{canonical} needs a division: {', '.join(divs)}")
    chosen_role = None
    if roles:
        keyed = {r["key"].lower(): r["key"] for r in roles} | {
            r["name"].lower(): r["key"] for r in roles
        }
        chosen_role = keyed.get((role or "").strip().lower())
        if chosen_role is None:
            raise InvalidQueueRequest(
                f"Unknown role '{role}'. Options: {', '.join(r['name'] for r in roles)}"
            )
    label = options.label(canonical, chosen)
    return MemberSpec(
        member_id=new_guest_id(),
        role=chosen_role,
        snapshot={"tier": canonical, "division": chosen, "label": label},
        is_guest=True,
        label=f"Guest ({label})",
    )


def composition_for(game: str, activity: str) -> CompositionEntry | None:
    for comp in SeedLoader().load_compositions().compositions:
        if comp.game == game and comp.activity == activity:
            return comp
    return None


def party_slot_summary(game: str, activity: str, specs: list[MemberSpec]) -> str:
    comp = composition_for(game, activity)
    if comp is None:
        return "-"
    names = {r.key: r.name for r in comp.roles}
    remaining = open_slots(comp, [s.role for s in specs]) if comp.roles else {}
    seats = comp.group_size - len(specs)
    text = slots_text(remaining, names) if comp.roles else f"{seats} player(s)"
    return f"{seats} open seat(s) — needs: {text}"


async def create_party_from_specs(
    scope, guild_id: int, leader_id: int, game: str, specs: list[MemberSpec]
) -> tuple[str, list[Event]]:
    """Persist the party; linked Discord members receive invites they must accept."""
    ordered = sorted(specs, key=lambda s: s.member_id != leader_id)  # leader first
    comp_max = None
    async with scope() as session:
        qs = QueueService(session, await load_match_settings(session, guild_id))
        pending = {s.member_id for s in ordered if not s.is_guest and s.member_id != leader_id}
        party_id = await qs.create_party(
            guild_id,
            leader_id,
            [s.member_id for s in ordered],
            declared_roles={s.member_id: s.role for s in ordered if s.role},
            guest_flags={s.member_id: True for s in ordered if s.is_guest},
            rank_snapshots={s.member_id: s.snapshot for s in ordered if s.snapshot},
            accepted_roles={s.member_id: s.accepted_roles for s in ordered if s.accepted_roles},
            pending_ids=pending,
            game=game,
        )
        events = qs.pop_events()
    _ = comp_max
    return party_id, events


async def queue_solo(
    scope, guild_id: int, member_id: int, game: str, activity: str, region: str | None
) -> tuple[str, list[Event]]:
    game_entry = next(g for g in SeedLoader().load_games().games if g.key == game)
    real_activity, either_with = resolve_activity(game_entry, activity)
    async with scope() as session:
        profile = await ProfileService(session, guild_id).get_profile(
            member_id, game, validate=False
        )
        if profile is None:
            raise InvalidQueueRequest(
                f"You need a {game_entry.name} profile first. Use Register/Edit My Profile."
            )
        qs = QueueService(session, await load_match_settings(session, guild_id))
        roles = list(profile.preferred_roles or [])
        entry = await qs.join_queue(
            guild_id,
            member_id,
            game,
            real_activity,
            queue_type=profile.current_queue_type or "default",
            region=region or profile.region,
            role_preference=roles[0] if roles else None,
            acceptable_roles=list(profile.acceptable_roles or []) or None,
            rank_snapshot=snapshot_from_profile(profile),
            either_with=either_with,
        )
        status = entry.status
        events = qs.pop_events()
    return status, events


async def queue_party(
    scope,
    guild_id: int,
    leader_id: int,
    party_id: str,
    game: str,
    activity: str,
    region: str | None,
) -> tuple[str, list[Event]]:
    game_entry = next(g for g in SeedLoader().load_games().games if g.key == game)
    real_activity, either_with = resolve_activity(game_entry, activity)
    async with scope() as session:
        qs = QueueService(session, await load_match_settings(session, guild_id))
        profile = await ProfileService(session, guild_id).get_profile(
            leader_id, game, validate=False
        )
        entry = await qs.join_queue(
            guild_id,
            leader_id,
            game,
            real_activity,
            queue_type=(profile.current_queue_type if profile else None) or "default",
            region=region or (profile.region if profile else None),
            party_id=party_id,
            leader_member_id=leader_id,
            rank_snapshot=snapshot_from_profile(profile) if profile else None,
            either_with=either_with,
        )
        status = entry.status
        events = qs.pop_events()
    return status, events


async def members_missing_profiles(
    scope, guild_id: int, game: str, member_ids: list[int]
) -> list[int]:
    missing: list[int] = []
    async with scope() as session:
        svc = ProfileService(session, guild_id)
        for mid in member_ids:
            if await svc.get_profile(mid, game, validate=False) is None:
                missing.append(mid)
    return missing


async def my_party(scope, guild_id: int, member_id: int):
    """(party_id, members) for the party this member leads (first member row), if any."""
    async with scope() as session:
        repo = PartyRepository(session)
        for pid in await repo.party_ids_for_member(guild_id, member_id):
            members = await repo.list_for_party(guild_id, pid)
            if members and members[0].member_id == member_id:
                return pid, list(members)
    return None, []
