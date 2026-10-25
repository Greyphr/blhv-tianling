"""In-memory Gateway used by tests (and by anyone wanting to dry-run the flow)."""

from __future__ import annotations

from dataclasses import dataclass, field

from tianling.discord_ui.gateway import Card, GatewayError


@dataclass
class FakeVoice:
    channel_id: int
    name: str
    category_id: int | None
    member_ids: list[int]
    mod_role_ids: list[int]
    occupants: set[int] = field(default_factory=set)


class FakeGateway:
    def __init__(self) -> None:
        self.messages: dict[tuple[int, int], Card] = {}
        self.sent: list[tuple[int, Card]] = []  # (channel_id, card) in post order
        self.dms: list[tuple[int, Card]] = []
        self.voices: dict[int, FakeVoice] = {}
        self.deleted_channels: list[int] = []
        self.absent_members: set[int] = set()
        self.names: dict[int, str] = {}
        self.fail_voice_creation = False
        self.fail_next_posts = 0
        self._next_id = 1000

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def post_card(self, channel_id: int, card: Card) -> int | None:
        if self.fail_next_posts:
            self.fail_next_posts -= 1
            raise GatewayError("simulated post failure")
        message_id = self._id()
        self.messages[(channel_id, message_id)] = card
        self.sent.append((channel_id, card))
        return message_id

    async def edit_card(self, channel_id: int, message_id: int, card: Card) -> bool:
        if (channel_id, message_id) not in self.messages:
            return False
        self.messages[(channel_id, message_id)] = card
        return True

    async def send_dm(self, member_id: int, card: Card) -> bool:
        self.dms.append((member_id, card))
        return True

    async def create_voice(self, guild_id, name, category_id, member_ids, mod_role_ids) -> int:
        if self.fail_voice_creation:
            raise GatewayError("Missing Permissions")
        channel_id = self._id()
        self.voices[channel_id] = FakeVoice(
            channel_id, name, category_id, list(member_ids), list(mod_role_ids)
        )
        return channel_id

    async def delete_channel(self, guild_id: int, channel_id: int) -> bool:
        if channel_id in self.voices:
            del self.voices[channel_id]
            self.deleted_channels.append(channel_id)
            return True
        return False

    async def voice_member_count(self, guild_id: int, channel_id: int) -> int | None:
        voice = self.voices.get(channel_id)
        return None if voice is None else len(voice.occupants)

    async def present_member_ids(self, guild_id: int, member_ids: list[int]) -> set[int]:
        return {m for m in member_ids if m not in self.absent_members}

    async def display_name(self, guild_id: int, member_id: int) -> str:
        return self.names.get(member_id, f"Member {member_id}")

    # --- test conveniences
    def latest_card(self, channel_id: int | None = None) -> Card | None:
        for chan, card in reversed(self.sent):
            if channel_id is None or chan == channel_id:
                return card
        return None
