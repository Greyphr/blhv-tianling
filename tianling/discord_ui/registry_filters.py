"""Registry filters and pagination views."""
from __future__ import annotations

import math

import discord

from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.users import UserRepository
from tianling.db.session import session_scope
from tianling.discord_ui.theme import THEME


class RegistryFilterView(discord.ui.View):
    def __init__(self, guild_id: int, channel_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.channel_id = channel_id

    @discord.ui.select(
        placeholder="Filter by game",
        options=[
            discord.SelectOption(label="All", value="all"),
            discord.SelectOption(label="Marvel Rivals", value="marvel_rivals"),
            discord.SelectOption(label="Overwatch 2", value="overwatch_2"),
            discord.SelectOption(label="Deadlock", value="deadlock"),
            discord.SelectOption(label="Valorant", value="valorant"),
        ],
        custom_id="tianling:regf:game",
    )
    async def game_filter(self, interaction: discord.Interaction, select: discord.ui.Select):
        game = select.values[0] if select.values else "all"
        await interaction.response.defer(ephemeral=True)
        await self._show_results(interaction, game=game)

    async def _show_results(self, interaction: discord.Interaction, game: str = "all", page: int = 0):
        async with session_scope() as session:
            prep = GameProfileRepository(session)
            urepo = UserRepository(session)
            profiles = await prep.list_for_guild(self.guild_id)
        if game != "all":
            profiles = [p for p in profiles if p.game == game]
        per_page = 5
        total = len(profiles)
        pages = max(1, math.ceil(total / per_page))
        start = page * per_page
        items = profiles[start : start + per_page]
        embed = discord.Embed(title=f"{THEME.emoji_registry} Registry Results ({total})", color=THEME.color_info)
        for p in items:
            user = await urepo.get_by_id(p.user_id) if 'urepo' in locals() else None
            mention = f"<@{user.member_id}>" if user else ""
            embed.add_field(name=f"{p.game}", value=f"{mention} {p.current_rank_tier or 'Unranked'} {p.current_division or ''}", inline=False)
        embed.set_footer(text=f"{THEME.footer} • Page {page+1}/{pages}")
        view = RegistryPager(guild_id=self.guild_id, channel_id=self.channel_id, game=game, page=page, pages=pages)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class RegistryPager(discord.ui.View):
    def __init__(self, guild_id: int, channel_id: int, game: str, page: int, pages: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.game = game
        self.page = page
        self.pages = pages
        if page > 0:
            self.add_item(discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary, custom_id=f"tianling:regp:prev:{game}:{page-1}"))
        if page < pages - 1:
            self.add_item(discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, custom_id=f"tianling:regp:next:{game}:{page+1}"))
