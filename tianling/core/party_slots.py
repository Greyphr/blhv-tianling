"""Pure open-slot arithmetic for parties (no I/O, no Discord)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from tianling.core.seed_loader import CompositionEntry


def role_capacity(composition: CompositionEntry) -> dict[str, int]:
    """How many players each role slot holds in a full group."""
    return {slot.key: slot.count for slot in composition.roles}


def role_overflow(
    composition: CompositionEntry, declared_roles: Iterable[str | None]
) -> dict[str, int]:
    """Roles a party over-fills, as ``{role: excess}``. Unknown roles count in full."""
    capacity = role_capacity(composition)
    used = Counter(r for r in declared_roles if r)
    return {
        role: n - capacity.get(role, 0) for role, n in used.items() if n > capacity.get(role, 0)
    }


def open_slots(
    composition: CompositionEntry, declared_roles: Iterable[str | None]
) -> dict[str, int]:
    """Role slots still to fill once the party's declared roles are placed."""
    capacity = role_capacity(composition)
    for role in declared_roles:
        if role in capacity and capacity[role] > 0:
            capacity[role] -= 1
    return capacity


def slots_text(slots: Mapping[str, int], names: Mapping[str, str] | None = None) -> str:
    names = names or {}
    parts = [
        f"{count} {names.get(role, role.replace('_', ' ').title())}"
        for role, count in slots.items()
        if count > 0
    ]
    return ", ".join(parts) or "none (party fills the group)"


def free_seats(composition: CompositionEntry, party_size: int) -> int:
    return max(0, composition.group_size - party_size)
