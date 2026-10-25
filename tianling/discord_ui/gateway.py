"""The narrow seam between Tianling's logic and Discord.

Everything the dispatcher, voice manager and scheduler need from Discord goes
through :class:`Gateway`.  ``DiscordGateway`` (discord_gateway.py) implements it
with discord.py; ``FakeGateway`` (fake_gateway.py) implements it for tests.
This module imports nothing from discord.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class GatewayError(RuntimeError):
    """A Discord operation failed (permissions, caps, API error)."""


@dataclass
class Card:
    """A renderable message: becomes an embed (plus buttons) in Discord."""

    title: str
    description: str = ""
    fields: list[tuple[str, str, bool]] = field(default_factory=list)
    color: int = 0x7C9BFF
    footer: str = ""
    #: Group whose Accept/Decline buttons should be attached (None = no buttons).
    proposal_group: str | None = None
    buttons_enabled: bool = True
    #: Party invite buttons: (party_id, invited_member_id).
    party_invite: tuple[str, int] | None = None
    #: "Still looking?" buttons: (game, activity).
    still_looking: tuple[str, str] | None = None
    #: Plain-text content (used for mentions that should actually ping).
    content: str = ""


class Gateway(Protocol):
    async def post_card(self, channel_id: int, card: Card) -> int | None: ...

    async def edit_card(self, channel_id: int, message_id: int, card: Card) -> bool: ...

    async def send_dm(self, member_id: int, card: Card) -> bool: ...

    async def create_voice(
        self,
        guild_id: int,
        name: str,
        category_id: int | None,
        member_ids: list[int],
        mod_role_ids: list[int],
    ) -> int: ...

    async def delete_channel(self, guild_id: int, channel_id: int) -> bool: ...

    async def voice_member_count(self, guild_id: int, channel_id: int) -> int | None:
        """Members in the voice channel, or None if the channel no longer exists."""
        ...

    async def present_member_ids(self, guild_id: int, member_ids: list[int]) -> set[int]: ...

    async def display_name(self, guild_id: int, member_id: int) -> str: ...
