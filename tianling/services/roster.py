"""Pure helpers that turn match rows into a roster view for the UI."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def is_real_member(member_id: int, *, is_guest: bool = False, is_test: bool = False) -> bool:
    """True for a real Discord account (not a guest slot or a test player)."""
    return member_id > 0 and not is_guest and not is_test


def roster_view(members: Iterable[Any]) -> list[dict[str, Any]]:
    """Render ``match_members`` rows as plain dicts (stable key set)."""
    view: list[dict[str, Any]] = []
    for m in members:
        snapshot = m.rank_snapshot or {}
        view.append(
            {
                "member_id": m.member_id,
                "role": m.role_assigned,
                "rank_label": snapshot.get("label"),
                "response": m.response,
                "party_id": m.party_id,
                "entry_id": m.entry_id,
                "is_guest": bool(m.is_guest),
                "is_test": bool(m.is_test),
            }
        )
    return view
