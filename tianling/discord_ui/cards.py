"""Pure rendering of proposals/announcements into :class:`Card` objects."""

from __future__ import annotations

from datetime import datetime

from tianling.core.timeutil import discord_ts
from tianling.discord_ui.gateway import Card
from tianling.discord_ui.theme import THEME

_STATUS_ICON = {"accepted": "✅", "declined": "❌", "timeout": "⌛", None: "⏳"}
_REASON_TEXT = {
    "declined": "A player declined.",
    "timeout": "A player did not respond in time.",
    "left_server": "A player left the server.",
    "banned": "A player is no longer eligible.",
    "withdrew": "A player left the queue.",
    "in_other_group": "A player joined another group.",
    "composition_invalid": "The roster was no longer valid.",
    "admin": "An administrator cancelled this group.",
    "party_removed": "A party was disbanded.",
}


def pretty(value: str | None) -> str:
    return (value or "-").replace("_", " ").title()


def member_label(m: dict, names: dict[int, str]) -> str:
    """Mention for real members, 'Guest (rank X)' for guests, a flagged label for test players."""
    if m.get("is_guest"):
        return f"Guest ({m.get('rank_label') or 'unranked'})"
    if m.get("is_test"):
        return f"🧪 {names.get(m['member_id'], 'Test player')} (test)"
    return f"<@{m['member_id']}>"


def _roster_lines(roster: list[dict], names: dict[int, str], *, show_status: bool) -> str:
    lines = []
    for m in roster:
        icon = _STATUS_ICON.get(m.get("response"), "⏳") if show_status else "•"
        rank = f" · {m['rank_label']}" if m.get("rank_label") and not m.get("is_guest") else ""
        pre = (
            " (pre-confirmed)"
            if show_status and m.get("response") == "accepted" and m.get("party_id")
            else ""
        )
        lines.append(f"{icon} {member_label(m, names)} — {pretty(m.get('role'))}{rank}{pre}")
    return "\n".join(lines) or "-"


def proposal_card(
    *,
    group_uuid: str,
    status: str,
    game: str,
    activity: str,
    region: str | None,
    deadline: datetime | None,
    roster: list[dict],
    names: dict[int, str],
    cancel_reason: str | None = None,
    voice_channel_id: int | None = None,
) -> Card:
    """The single message that is edited as the proposal moves through its life."""
    pending = [m for m in roster if m.get("response") is None]
    if status == "proposed":
        title = f"{THEME.emoji_star} Proposed Match — not confirmed yet"
        color = THEME.color_primary
        description = (
            f"**{pretty(game)}** · {pretty(activity)} · {region or 'Any region'}\n"
            f"Everyone must accept {discord_ts(deadline)}. Waiting on {len(pending)} player(s)."
        )
    elif status == "finalized":
        title = f"{THEME.emoji_sun} Heavenly Match Confirmed"
        color = THEME.color_success
        description = f"**{pretty(game)}** · {pretty(activity)} · {region or 'Any region'}"
        if voice_channel_id:
            description += f"\nVoice: <#{voice_channel_id}>"
    else:
        title = f"{THEME.emoji_moon} Proposal Cancelled"
        color = THEME.color_danger
        description = (
            f"**{pretty(game)}** · {pretty(activity)}\n"
            f"{_REASON_TEXT.get(cancel_reason or '', 'This proposal was cancelled.')} "
            "Remaining players are being re-queued."
        )
    return Card(
        title=title,
        description=description,
        fields=[("Roster", _roster_lines(roster, names, show_status=status == "proposed"), False)],
        color=color,
        footer=THEME.footer,
        proposal_group=group_uuid,
        buttons_enabled=status == "proposed",
    )


def final_announcement(
    *,
    game: str,
    activity: str,
    region: str | None,
    roster: list[dict],
    names: dict[int, str],
    voice_text: str,
) -> Card:
    return Card(
        title=f"{THEME.emoji_sun} Heavenly Match Found",
        description=f"**Game:** {pretty(game)}\n**Activity:** {pretty(activity)}\n"
        f"**Region:** {region or 'Any'}\n**Voice:** {voice_text}",
        fields=[("Roster", _roster_lines(roster, names, show_status=False), False)],
        color=THEME.color_success,
        footer=THEME.footer,
    )
