"""Matchmaking UI."""
from __future__ import annotations

import uuid

import discord

from tianling.db.repositories.compositions import CompositionRepository
from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.users import UserRepository
from tianling.db.session import session_scope
from tianling.discord_ui.theme import THEME
from tianling.services.queue_service import QueueConfig, QueueService


GAMES = [
    ("Marvel Rivals", "marvel_rivals"),
    ("Overwatch 2", "overwatch_2"),
    ("Deadlock", "deadlock"),
    ("Valorant", "valorant"),
]


class LFGStart(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Start LFG", style=discord.ButtonStyle.primary, custom_id="tianling:lfg:start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(timeout=300)
        options = [discord.SelectOption(label=label, value=val) for label, val in GAMES]
        sel = discord.ui.Select(placeholder="Select game", options=options, custom_id="tianling:lfg:game")
        async def cb(i: discord.Interaction):
            game = sel.values[0]
            view2 = discord.ui.View(timeout=300)
            opts = [discord.SelectOption(label="Competitive", value="competitive"), discord.SelectOption(label="Quick Play", value="quickplay")]
            sel2 = discord.ui.Select(placeholder="Activity/Queue", options=opts, custom_id=f"tianling:lfg:act:{game}")
            async def cb2(j: discord.Interaction):
                act = sel2.values[0]
                view3 = discord.ui.View(timeout=300)
                optsr = [discord.SelectOption(label="NA", value="NA"), discord.SelectOption(label="EU", value="EU"), discord.SelectOption(label="Asia", value="Asia")]
                selr = discord.ui.Select(placeholder="Region", options=optsr, custom_id=f"tianling:lfg:reg:{game}:{act}")
                async def cb3(k: discord.Interaction):
                    region = selr.values[0]
                    embed = discord.Embed(title=f"{THEME.emoji_team} Party Setup", description="How many players in your party right now, including you?", color=THEME.color_primary)
                    embed.set_footer(text=THEME.footer)
                    view4 = discord.ui.View(timeout=300)
                    for n in [1,2,3,4,5,6]:
                        b = discord.ui.Button(label=str(n), custom_id=f"tianling:lfg:size:{game}:{act}:{region}:{n}")
                        async def bcb(m: discord.Interaction, nn=n):
                            await m.response.edit_message(content=f"Party size {nn}. Add members or proceed.", view=PartySetupView(game, act, region, nn))
                        b.callback = bcb
                        view4.add_item(b)
                    await k.response.edit_message(embed=embed, view=view4)
                selr.callback = cb3
                view3.add_item(selr)
                await j.response.edit_message(content="Select region", view=view3)
            sel2.callback = cb2
            view2.add_item(sel2)
            await i.response.edit_message(content="Select activity/queue", view=view2)
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.edit_message(content="Select game", view=view)


class PartySetupView(discord.ui.View):
    def __init__(self, game: str, activity: str, region: str, size: int):
        super().__init__(timeout=300)
        self.game = game
        self.activity = activity
        self.region = region
        self.size = size

    @discord.ui.button(label="Summary / Confirm", style=discord.ButtonStyle.success, custom_id="tianling:lfg:summary")
    async def summary(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with session_scope() as session:
            urepo = UserRepository(session)
            user = await urepo.get_or_create(interaction.guild_id, interaction.user.id)
            prep = GameProfileRepository(session)
            prof = await prep.get(interaction.guild_id, user.id, self.game)
        if not prof:
            await interaction.response.send_message("Missing profile. Use Register/Edit My Profile.", ephemeral=True)
            return
        embed = discord.Embed(title=f"{THEME.emoji_team} Party Summary", description=f"Game {self.game} | {self.activity} | {self.region}\nParty size: {self.size}\nYou", color=THEME.color_primary)
        embed.set_footer(text=THEME.footer)
        view = discord.ui.View(timeout=300)
        view.add_item(JoinQueueBtn(self.game, self.activity, self.region, self.size))
        await interaction.response.edit_message(embed=embed, view=view)


class JoinQueueBtn(discord.ui.Button):
    def __init__(self, game: str, activity: str, region: str, size: int):
        super().__init__(label="Join Queue", style=discord.ButtonStyle.success, custom_id=f"tianling:lfg:join:{uuid.uuid4()}")
        self.game = game
        self.activity = activity
        self.region = region
        self.size = size

    async def callback(self, interaction: discord.Interaction):
        async with session_scope() as session:
            urepo = UserRepository(session)
            user = await urepo.get_or_create(interaction.guild_id, interaction.user.id)
            prep = GameProfileRepository(session)
            prof = await prep.get(interaction.guild_id, user.id, self.game)
            if not prof:
                await interaction.response.send_message("Missing profile. Register first.", ephemeral=True)
                return
            qs = QueueService(session)
            await qs.join_queue(
                interaction.guild_id,
                interaction.user.id,
                self.game,
                self.activity,
                queue_type="role_queue" if self.game == "overwatch_2" else "default",
                region=self.region,
                party_size=self.size,
                leader_member_id=interaction.user.id,
                rank_snapshot={"tier": prof.current_rank_tier, "division": prof.current_division, "order": prof.current_rank_order_value},
            )
            await session.commit()
            ev = qs.pop_events()
        await interaction.response.send_message(f"Joined queue for {self.game} {self.activity} ({self.region}). Events: {len(ev)}", ephemeral=True)
