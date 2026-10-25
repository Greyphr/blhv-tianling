"""Discord adapter interface for registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class Embed:
    title: str | None = None
    description: str | None = None
    color: int | None = None
    fields: list[tuple[str, str, bool]] | None = None
    footer: str | None = None


class DiscordAdapter(Protocol):
    async def get_channel(self, channel_id: int): ...

    async def send_embed(self, channel, embed: Embed) -> int: ...

    async def edit_embed(self, channel, message_id: int, embed: Embed) -> None: ...

    async def delete_message(self, channel, message_id: int) -> None: ...
