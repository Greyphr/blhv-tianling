"""Catch-all for wizard menus that outlived the bot process.

discord.py dispatches a component interaction to a matching ``DynamicItem`` *in
addition to* any live view that owns the message.  So the catch-all waits a moment and
only answers if nobody else has responded (live wizard callbacks always defer or send a
modal immediately).  This turns "This interaction failed" after a restart into a clear
message.
"""

from __future__ import annotations

import asyncio
import re

import discord

# Live wizard menus use these prefixes; the persistent panels/proposals do not.
FLOW_ID_RE = r"tianling:(?:reg|lfg|prefs):.*"
# Must differ textually: discord.py keys dynamic items by compiled pattern.
FLOW_SELECT_ID_RE = r"tianling:(?:reg|lfg|prefs):.+"
YIELD_SECONDS = 0.7

EXPIRED_TEXT = (
    "This menu expired (the bot may have restarted). Please press the button again to start over."
)


async def _reply_if_orphaned(interaction: discord.Interaction) -> None:
    await asyncio.sleep(YIELD_SECONDS)
    if interaction.response.is_done():
        return  # a live view handled it
    await interaction.response.send_message(EXPIRED_TEXT, ephemeral=True)


class ExpiredButtonItem(discord.ui.DynamicItem[discord.ui.Button], template=FLOW_ID_RE):
    def __init__(self, custom_id: str = "tianling:reg:expired") -> None:
        super().__init__(discord.ui.Button(custom_id=custom_id))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match: re.Match, /):
        return cls(match.string)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _reply_if_orphaned(interaction)


class ExpiredSelectItem(discord.ui.DynamicItem[discord.ui.Select], template=FLOW_SELECT_ID_RE):
    def __init__(self, custom_id: str = "tianling:reg:expired") -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=custom_id, options=[discord.SelectOption(label="-", value="-")]
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match: re.Match, /):
        return cls(match.string)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _reply_if_orphaned(interaction)


EXPIRED_ITEMS = (ExpiredButtonItem, ExpiredSelectItem)
