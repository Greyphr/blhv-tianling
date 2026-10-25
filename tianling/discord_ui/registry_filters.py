"""Registry browsing: a persistent game filter on the registry channel and paged results."""

from __future__ import annotations

import math
from collections.abc import Sequence

import discord

from tianling.core.seed_loader import SeedLoader
from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.users import UserRepository
from tianling.discord_ui.theme import THEME

PER_PAGE = 5


def filter_profiles(profiles: Sequence, game: str = "all", region: str = "all") -> list:
    """Pure filter used by the menus (testable without Discord)."""
    return [
        p
        for p in profiles
        if (game == "all" or p.game == game) and (region == "all" or p.region == region)
    ]


def paginate(items: Sequence, page: int, per_page: int = PER_PAGE) -> tuple[list, int, int]:
    """Return (items on the page, clamped page, total pages)."""
    pages = max(1, math.ceil(len(items) / per_page))
    page = min(max(page, 0), pages - 1)
    return list(items[page * per_page : (page + 1) * per_page]), page, pages


def game_options() -> list[discord.SelectOption]:
    games = SeedLoader().load_games().games
    return [discord.SelectOption(label="All games", value="all")] + [
        discord.SelectOption(label=g.name, value=g.key) for g in games
    ]


def region_options() -> list[discord.SelectOption]:
    return [discord.SelectOption(label="All regions", value="all")] + [
        discord.SelectOption(label=r, value=r) for r in SeedLoader().load_games().regions
    ]


async def build_results(
    scope, guild_id: int, game: str, region: str, page: int
) -> tuple[discord.Embed, int, int]:
    async with scope() as session:
        profiles = await GameProfileRepository(session).list_for_guild(guild_id)
        matching = filter_profiles(profiles, game, region)
        items, page, pages = paginate(matching, page)
        users = UserRepository(session)
        rows = []
        for p in items:
            user = await users.get_by_id(p.user_id)
            rows.append((p, user))
    embed = discord.Embed(
        title=f"{THEME.emoji_registry} Registry Results ({len(matching)})", color=THEME.color_info
    )
    for p, user in rows:
        rank = f"{p.current_rank_tier or 'Unranked'} {p.current_division or ''}".strip()
        who = f"<@{user.member_id}>" if user else "-"
        embed.add_field(
            name=f"{p.game.replace('_', ' ').title()} • {p.ingame_name or '-'}",
            value=f"{who} · {p.region or '-'} · {rank}",
            inline=False,
        )
    if not rows:
        embed.description = "No players match these filters yet."
    embed.set_footer(text=f"{THEME.footer} • Page {page + 1}/{pages}")
    return embed, page, pages


class RegistryResultsView(discord.ui.View):
    """Ephemeral results with region filter and working Prev/Next buttons."""

    def __init__(self, scope, guild_id: int, game: str, region: str, page: int, pages: int) -> None:
        super().__init__(timeout=300)
        self.scope, self.guild_id = scope, guild_id
        self.game, self.region, self.page, self.pages = game, region, page, pages
        select = discord.ui.Select(
            placeholder="Filter by region",
            options=region_options(),
            custom_id="tianling:reg:regf_region",
        )
        select.callback = self._on_region  # type: ignore[method-assign]
        self.add_item(select)
        prev_btn = discord.ui.Button(
            label="Prev", custom_id="tianling:reg:regf_prev", disabled=page <= 0
        )
        next_btn = discord.ui.Button(
            label="Next", custom_id="tianling:reg:regf_next", disabled=page >= pages - 1
        )
        prev_btn.callback = self._prev  # type: ignore[method-assign]
        next_btn.callback = self._next  # type: ignore[method-assign]
        self.add_item(prev_btn)
        self.add_item(next_btn)

    async def _show(self, interaction: discord.Interaction, region: str, page: int) -> None:
        await interaction.response.defer()
        embed, page, pages = await build_results(self.scope, self.guild_id, self.game, region, page)
        view = RegistryResultsView(self.scope, self.guild_id, self.game, region, page, pages)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _on_region(self, interaction: discord.Interaction) -> None:
        await self._show(interaction, interaction.data["values"][0], 0)  # type: ignore[index]

    async def _prev(self, interaction: discord.Interaction) -> None:
        await self._show(interaction, self.region, self.page - 1)

    async def _next(self, interaction: discord.Interaction) -> None:
        await self._show(interaction, self.region, self.page + 1)


class RegistryFilterView(discord.ui.View):
    """Persistent panel under the registry: pick a game to browse."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        select = discord.ui.Select(
            placeholder="Browse the registry by game",
            options=game_options(),
            custom_id="tianling:regf:game",
        )
        select.callback = self._on_game  # type: ignore[method-assign]
        self.add_item(select)

    async def _on_game(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        game = interaction.data["values"][0]  # type: ignore[index]
        scope = interaction.client.scope  # type: ignore[attr-defined]
        embed, page, pages = await build_results(scope, interaction.guild_id, game, "all", 0)
        view = RegistryResultsView(scope, interaction.guild_id, game, "all", page, pages)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
