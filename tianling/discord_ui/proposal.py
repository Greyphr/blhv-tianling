"""Proposal Accept/Decline buttons that survive restarts.

Every button's ``custom_id`` carries the group id
(``tianling:prop:accept:<group_uuid>``).  One ``DynamicItem`` template registered in
``setup_hook`` revives *any* proposal button, including those on messages posted
before the restart.  Handlers defer first, then do DB work, then let the dispatcher
edit the single proposal message.
"""

from __future__ import annotations

import logging
import re

import discord

from tianling.discord_ui.gateway import Gateway
from tianling.services.confirmation_service import ConfirmationService, ResponseResult
from tianling.services.events import Event
from tianling.services.roster import is_real_member
from tianling.services.settings import load_match_settings

log = logging.getLogger(__name__)

PROPOSAL_ID_RE = r"tianling:prop:(?P<action>accept|decline):(?P<group>[0-9a-f-]+)"


def custom_id_for(action: str, group_uuid: str) -> str:
    return f"tianling:prop:{action}:{group_uuid}"


async def respond_to_proposal(
    scope, gateway: Gateway, guild_id: int, group_uuid: str, member_id: int, action: str
) -> tuple[ResponseResult, list[Event]]:
    """Core of the button handler, free of discord objects so it can be tested.

    Runs two short transactions so no DB lock is held while asking Discord who is
    still in the server.
    """
    present: set[int] | None = None
    if action == "accept":
        from tianling.db.repositories.matches import MatchRepository

        async with scope() as session:
            matches = MatchRepository(session)
            group = await matches.get_group(group_uuid)
            humans = (
                [
                    m.member_id
                    for m in await matches.list_members(group.id)
                    if is_real_member(m.member_id, is_guest=m.is_guest, is_test=m.is_test)
                ]
                if group is not None and group.status == "proposed"
                else []
            )
        if humans:
            present = await gateway.present_member_ids(guild_id, humans)
    async with scope() as session:
        cs = ConfirmationService(session, await load_match_settings(session, guild_id))
        if action == "accept":
            result = await cs.accept(guild_id, group_uuid, member_id, present_member_ids=present)
        else:
            result = await cs.decline(guild_id, group_uuid, member_id)
        events = cs.pop_events()
    return result, events


class ProposalButton(discord.ui.DynamicItem[discord.ui.Button], template=PROPOSAL_ID_RE):
    def __init__(self, action: str, group_uuid: str, *, disabled: bool = False) -> None:
        self.action = action
        self.group_uuid = group_uuid
        accept = action == "accept"
        super().__init__(
            discord.ui.Button(
                label="Accept" if accept else "Decline",
                style=discord.ButtonStyle.success if accept else discord.ButtonStyle.danger,
                emoji="✅" if accept else "❌",
                custom_id=custom_id_for(action, group_uuid),
                disabled=disabled,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item, match: re.Match, /):
        return cls(match["action"], match["group"])

    async def callback(self, interaction: discord.Interaction) -> None:
        # Respond within 3 seconds first; everything else happens after the defer.
        await interaction.response.defer(ephemeral=True, thinking=False)
        bot = interaction.client
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in the server.", ephemeral=True)
            return
        try:
            result, events = await respond_to_proposal(
                bot.scope,
                bot.gateway,
                interaction.guild_id,
                self.group_uuid,
                interaction.user.id,
                self.action,
            )
        except Exception:
            log.exception("proposal %s click failed", self.group_uuid)
            await interaction.followup.send(
                "Something went wrong. Please try again.", ephemeral=True
            )
            return
        await bot.dispatcher.dispatch(events)  # edits the one proposal message in place
        await interaction.followup.send(result.message, ephemeral=True)
