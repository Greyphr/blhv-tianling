"""Turns service events into Discord actions.

Services never touch Discord; they return events.  After the handler commits,
``Dispatcher.dispatch(events)`` performs the Discord side: post or edit the ONE
proposal message, announce results, create voice channels, and DM members who
have not opted out.  Every Discord call goes through the :class:`Gateway`, and
no DB session is held open while talking to Discord.
"""

from __future__ import annotations

import logging

from tianling.db.repositories.matches import MatchRepository
from tianling.discord_ui.cards import final_announcement, pretty, proposal_card
from tianling.discord_ui.gateway import Card, Gateway
from tianling.discord_ui.notifications import NotificationService
from tianling.discord_ui.theme import THEME
from tianling.discord_ui.voice import VoiceManager, VoiceResult
from tianling.services import events as ev
from tianling.services.roster import is_real_member, roster_view
from tianling.services.settings import get_channel_id, load_voice_settings

log = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        gateway: Gateway,
        scope,
        voice: VoiceManager,
        notifier: NotificationService,
    ) -> None:
        self.gateway = gateway
        self.scope = scope
        self.voice = voice
        self.notifier = notifier

    async def dispatch(self, events: list[ev.Event]) -> None:
        """Handle events in order; one failing event never blocks the rest."""
        for event in events:
            try:
                await self._handle(event)
            except Exception:
                log.exception("failed to dispatch %s", type(event).__name__)

    async def _handle(self, event: ev.Event) -> None:
        match event:
            case ev.ProposalCreated():
                await self._on_proposal_created(event)
            case ev.MemberAccepted() | ev.MemberDeclined() | ev.MemberTimedOut():
                await self.refresh_proposal(event.group_uuid)
            case ev.GroupCancelled():
                await self.refresh_proposal(event.group_uuid)
            case ev.GroupFinalized():
                await self._on_finalized(event)
            case ev.ReplacementFailed():
                await self._on_replacement_failed(event)
            case ev.ReplacementSucceeded():
                pass  # the new proposal is announced by its own ProposalCreated
            case ev.ReplacementStarted():
                pass  # the cancelled card already explains what happened
            case ev.CooldownApplied():
                await self._dm(
                    event.guild_id,
                    event.member_id,
                    Card(
                        title=f"{THEME.emoji_moon} Matchmaking cooldown",
                        description="You missed a match confirmation, so you can't queue until "
                        f"<t:{int(event.expires_at.timestamp())}:R>."
                        if event.expires_at
                        else "You missed a match confirmation.",
                        color=THEME.color_warning,
                        footer=THEME.footer,
                    ),
                    kind="cooldown",
                )
            case ev.QueueExpired():
                for mid in event.member_ids or [event.member_id]:
                    await self._dm(
                        event.guild_id,
                        mid,
                        Card(
                            title=f"{THEME.emoji_moon} Queue request expired",
                            description=f"Your {pretty(event.game)} {pretty(event.activity)} "
                            "request expired. Press Find My Team to queue again.",
                            color=THEME.color_warning,
                            footer=THEME.footer,
                        ),
                        kind="expired",
                    )
            case ev.StillLooking():
                for mid in event.member_ids or [event.member_id]:
                    await self._dm(
                        event.guild_id,
                        mid,
                        Card(
                            title=f"{THEME.emoji_star} Still looking?",
                            description=f"Your {pretty(event.game)} {pretty(event.activity)} "
                            "request is about to expire. Keep waiting?",
                            color=THEME.color_info,
                            footer=THEME.footer,
                            still_looking=(event.game, event.activity),
                        ),
                        kind="still_looking",
                        dedupe=f"{event.game}:{event.activity}",
                    )
            case ev.PartyInvite():
                await self._dm(
                    event.guild_id,
                    event.invited_member_id,
                    Card(
                        title=f"{THEME.emoji_team} Party invite",
                        description=f"<@{event.leader_member_id}> invited you to a party. "
                        "Accept to be included when they queue.",
                        color=THEME.color_primary,
                        footer=THEME.footer,
                        party_invite=(event.party_id, event.invited_member_id),
                    ),
                    kind="party_invite",
                    dedupe=event.party_id,
                )
            case ev.StaleProfiles():
                for mid, games in event.members.items():
                    await self._dm(
                        event.guild_id,
                        mid,
                        Card(
                            title=f"{THEME.emoji_registry} Is your profile up to date?",
                            description="Your profile hasn't been updated in a while for: "
                            + ", ".join(pretty(g) for g in games)
                            + ". Use Register/Edit My Profile to refresh your rank.",
                            color=THEME.color_info,
                            footer=THEME.footer,
                        ),
                        kind="stale",
                    )
            case _:
                pass  # party bookkeeping events need no Discord action

    # ------------------------------------------------------------------ helpers

    async def _dm(
        self, guild_id: int, member_id: int, card: Card, *, kind: str, dedupe: str = ""
    ) -> None:
        await self.notifier.dm(guild_id, member_id, card, kind=kind, dedupe=dedupe)

    async def _alerts_channel(self, guild_id: int) -> int | None:
        async with self.scope() as session:
            channel_id = await get_channel_id(session, guild_id, "matchmaking_alerts")
        if channel_id is None:
            log.warning("no matchmaking_alerts channel configured for guild %s", guild_id)
        return channel_id

    async def _snapshot(self, group_uuid: str) -> dict | None:
        async with self.scope() as session:
            matches = MatchRepository(session)
            group = await matches.get_group(group_uuid)
            if group is None:
                return None
            members = await matches.list_members(group.id)
            return {
                "guild_id": group.guild_id,
                "group_uuid": group.group_uuid,
                "status": group.status,
                "game": group.game,
                "activity": group.activity,
                "region": group.region,
                "deadline": group.confirmation_deadline,
                "channel_id": group.channel_id,
                "message_id": group.message_id,
                "voice_channel_id": group.voice_channel_id,
                "cancel_reason": group.cancel_reason,
                "roster": roster_view(members),
            }

    async def _names(self, guild_id: int, roster: list[dict]) -> dict[int, str]:
        names: dict[int, str] = {}
        for m in roster:
            if m.get("is_test"):
                names[m["member_id"]] = await self.gateway.display_name(guild_id, m["member_id"])
        return names

    async def _card_for(self, snap: dict) -> Card:
        names = await self._names(snap["guild_id"], snap["roster"])
        return proposal_card(
            group_uuid=snap["group_uuid"],
            status=snap["status"],
            game=snap["game"],
            activity=snap["activity"],
            region=snap["region"],
            deadline=snap["deadline"],
            roster=snap["roster"],
            names=names,
            cancel_reason=snap["cancel_reason"],
            voice_channel_id=snap["voice_channel_id"],
        )

    async def reconcile_proposals(self, guild_id: int | None = None) -> int:
        """After a restart: re-render every proposal that is still pending.

        Edits the stored message when it still exists and reposts it if it was lost,
        so the persistent Accept/Decline buttons always point at a live message.
        """
        async with self.scope() as session:
            uuids = [g.group_uuid for g in await MatchRepository(session).list_proposed(guild_id)]
        for group_uuid in uuids:
            try:
                await self.refresh_proposal(group_uuid)
            except Exception:
                log.exception("could not rebuild proposal %s", group_uuid)
        return len(uuids)

    # --------------------------------------------------------------- proposals

    async def _on_proposal_created(self, event: ev.ProposalCreated) -> None:
        snap = await self._snapshot(event.group_uuid)
        if snap is None:
            return
        if snap["status"] == "finalized":
            return  # pre-confirmed party / auto-confirm: GroupFinalized announces it
        await self.refresh_proposal(event.group_uuid, ping=event.pending_member_ids)

    async def refresh_proposal(self, group_uuid: str, *, ping: list[int] | None = None) -> None:
        """Edit the group's ONE proposal message in place (post it if it does not exist yet)."""
        snap = await self._snapshot(group_uuid)
        if snap is None:
            return
        card = await self._card_for(snap)
        channel_id, message_id = snap["channel_id"], snap["message_id"]
        if channel_id and message_id:
            if await self.gateway.edit_card(channel_id, message_id, card):
                return
            log.info("proposal message for %s is gone; reposting", group_uuid)
        channel_id = await self._alerts_channel(snap["guild_id"])
        if channel_id is None:
            return
        if ping:
            card.content = await self.notifier.mentions(snap["guild_id"], ping)
        new_id = await self.gateway.post_card(channel_id, card)
        if new_id is not None:
            async with self.scope() as session:
                await MatchRepository(session).set_message(group_uuid, channel_id, new_id)

    async def _on_replacement_failed(self, event: ev.ReplacementFailed) -> None:
        channel_id = await self._alerts_channel(event.guild_id)
        if channel_id is None or not event.released_member_ids:
            return
        mentions = await self.notifier.mentions(event.guild_id, event.released_member_ids)
        if event.released:
            text = "No replacement was found yet. You're still in the queue and keep your place."
        else:
            text = "No replacement was found, so these requests were cancelled. Please queue again."
        await self.gateway.post_card(
            channel_id,
            Card(
                title=f"{THEME.emoji_moon} No replacement found",
                description=text,
                color=THEME.color_warning,
                footer=THEME.footer,
                content=mentions,
            ),
        )

    # ------------------------------------------------------------ finalization

    async def _on_finalized(self, event: ev.GroupFinalized) -> None:
        result = await self.voice.create_for_group(event.guild_id, event.group_uuid)
        await self.refresh_proposal(event.group_uuid)
        snap = await self._snapshot(event.group_uuid)
        if snap is None:
            return
        names = await self._names(event.guild_id, snap["roster"])
        voice_text = await self._voice_text(event.guild_id, result)
        card = final_announcement(
            game=snap["game"],
            activity=snap["activity"],
            region=snap["region"],
            roster=snap["roster"],
            names=names,
            voice_text=voice_text,
        )
        humans = [
            m["member_id"]
            for m in snap["roster"]
            if is_real_member(m["member_id"], is_guest=m["is_guest"], is_test=m["is_test"])
        ]
        alerts = await self._alerts_channel(event.guild_id)
        if alerts is not None:
            card.content = await self.notifier.mentions(event.guild_id, humans)
            await self.gateway.post_card(alerts, card)
            if result.channel_id is None or result.fallback:
                admins = " ".join(f"<@&{r}>" for r in result.mod_role_ids)
                await self.gateway.post_card(
                    alerts,
                    Card(
                        title=f"{THEME.emoji_moon} Voice channel problem",
                        description="Tianling could not create a private voice channel "
                        f"(`{result.error}`). The group is still confirmed.",
                        color=THEME.color_warning,
                        footer=THEME.footer,
                        content=admins,
                    ),
                )
        card.content = ""
        for mid in humans:
            await self._dm(event.guild_id, mid, card, kind="final", dedupe=event.group_uuid)

    async def _voice_text(self, guild_id: int, result: VoiceResult) -> str:
        if result.channel_id and not result.fallback:
            return f"<#{result.channel_id}>"
        if result.channel_id and result.fallback:
            return f"Couldn't create a private channel; please use <#{result.channel_id}>."
        async with self.scope() as session:
            await load_voice_settings(session, guild_id)
        return "Couldn't create a voice channel; coordinate in the text channel."
