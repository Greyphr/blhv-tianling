"""Registration flow (multi-step)."""
from __future__ import annotations

import discord

from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.users import UserRepository
from tianling.db.session import session_scope
from tianling.discord_ui.theme import THEME

GAMES = [
    ("Marvel Rivals", "marvel_rivals"),
    ("Overwatch 2", "overwatch_2"),
    ("Deadlock", "deadlock"),
    ("Valorant", "valorant"),
]


class GameSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=val) for label, val in GAMES]
        super().__init__(placeholder="Choose a game...", min_values=1, max_values=1, options=options, custom_id="tianling:reg:game")

    async def callback(self, interaction: discord.Interaction):
        game = self.values[0]
        view = discord.ui.View(timeout=300)
        if game == "overwatch_2":
            view.add_item(QueueTypeSelect(game))
        else:
            view.add_item(TierSelect(game, queue_type="default"))
        await interaction.response.edit_message(content=f"{THEME.emoji_registry} {game} — Select queue/rank", view=view)


class QueueTypeSelect(discord.ui.Select):
    def __init__(self, game: str):
        options = [
            discord.SelectOption(label="Role Queue", value="role_queue"),
            discord.SelectOption(label="Open Queue", value="open_queue"),
        ]
        super().__init__(placeholder="Select queue type...", min_values=1, max_values=1, options=options, custom_id="tianling:reg:ow_qt")
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        qt = self.values[0]
        view = discord.ui.View(timeout=300)
        view.add_item(TierSelect(self.game, queue_type=qt))
        await interaction.response.edit_message(content=f"{THEME.emoji_registry} Overwatch 2 — {qt.replace('_',' ').title()} — Tier", view=view)


class TierSelect(discord.ui.Select):
    def __init__(self, game: str, queue_type: str):
        # common tiers placeholder; could load from ranks
        tiers = ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Master", "Grandmaster", "Champion"]
        options = [discord.SelectOption(label=t, value=t.lower()) for t in tiers]
        super().__init__(placeholder="Select tier...", min_values=1, max_values=1, options=options, custom_id=f"tianling:reg:tier:{game}:{queue_type}")
        self.game = game
        self.queue_type = queue_type

    async def callback(self, interaction: discord.Interaction):
        tier = self.values[0]
        view = discord.ui.View(timeout=300)
        # divisions
        divs = ["1", "2", "3", "4", "5"]
        options = [discord.SelectOption(label=f"Division {d}", value=d) for d in divs]
        select = discord.ui.Select(placeholder="Select division...", min_values=1, max_values=1, options=options, custom_id=f"tianling:reg:div:{self.game}:{self.queue_type}:{tier}")
        async def div_cb(i: discord.Interaction):
            div = select.values[0]
            embed = discord.Embed(title=f"{THEME.emoji_profile} Review (self-reported)", description=f"Game: {self.game}\nQueue: {self.queue_type}\nRank: {tier.title()} {div}", color=THEME.color_primary)
            embed.set_footer(text=THEME.footer)
            view2 = discord.ui.View(timeout=300)
            view2.add_item(ConfirmProfile(self.game, self.queue_type, tier, div))
            await i.response.edit_message(embed=embed, content=None, view=view2)
        select.callback = div_cb
        view.add_item(select)
        await interaction.response.edit_message(content=f"{THEME.emoji_registry} Division", view=view)


class ConfirmProfile(discord.ui.Button):
    def __init__(self, game: str, queue_type: str, tier: str, div: str):
        super().__init__(label="Confirm", style=discord.ButtonStyle.success, custom_id=f"tianling:reg:confirm:{game}:{queue_type}:{tier}:{div}")
        self.game = game
        self.queue_type = queue_type
        self.tier = tier
        self.div = div

    async def callback(self, interaction: discord.Interaction):
        async with session_scope() as session:
            urepo = UserRepository(session)
            user = await urepo.get_or_create(interaction.guild_id, interaction.user.id)
            prep = GameProfileRepository(session)
            existing = await prep.get_by_user_game(user.id, self.game)
            if existing:
                existing.current_queue_type = self.queue_type
                existing.current_rank_tier = self.tier.title()
                existing.current_division = self.div
                existing.rank_source = "self_reported"
                existing.needs_update = False
            else:
                from tianling.db.models import GameProfile
                p = GameProfile(
                    guild_id=interaction.guild_id,
                    user_id=user.id,
                    game=self.game,
                    current_queue_type=self.queue_type,
                    current_rank_tier=self.tier.title(),
                    current_division=self.div,
                    rank_source="self_reported",
                    needs_update=False,
                )
                await prep.create(p)
            await session.commit()
        embed = discord.Embed(title=f"{THEME.emoji_success if hasattr(THEME,'emoji_success') else '✅'} Profile saved", description="Rank saved as self-reported.", color=THEME.color_success)
        embed.set_footer(text=THEME.footer)
        await interaction.response.edit_message(embed=embed, view=None)


async def start_registration(interaction: discord.Interaction):
    view = discord.ui.View(timeout=300)
    view.add_item(GameSelect())
    embed = discord.Embed(title=f"{THEME.emoji_registry} Profile Registration", description="Choose your game. Only relevant fields shown.", color=THEME.color_primary)
    embed.set_footer(text=THEME.footer)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
