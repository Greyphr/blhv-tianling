"""Fake Discord adapter for tests."""

from __future__ import annotations

from tianling.discord_ui.registry_adapter import DiscordAdapter, Embed


class FakeChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id


class FakeDiscordAdapter(DiscordAdapter):
    def __init__(self):
        self.channels = {}
        self.messages = {}
        self.next_id = 1

    async def get_channel(self, channel_id: int):
        if channel_id not in self.channels:
            self.channels[channel_id] = FakeChannel(channel_id)
        return self.channels[channel_id]

    async def send_embed(self, channel, embed: Embed) -> int:
        mid = self.next_id
        self.next_id += 1
        self.messages[mid] = {"channel": channel.id, "embed": embed}
        return mid

    async def edit_embed(self, channel, message_id: int, embed: Embed) -> None:
        if message_id not in self.messages:
            raise ValueError("message not found")
        self.messages[message_id]["embed"] = embed

    async def delete_message(self, channel, message_id: int) -> None:
        if message_id in self.messages:
            del self.messages[message_id]
        else:
            raise ValueError("message not found")
