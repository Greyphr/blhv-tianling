# AGENTS.md — Standing Rules for Tianling

These rules apply to every future session working in this repository.

## Architecture

- Core logic (ranks, matching, party/slot math, queue rules) is pure Python with **zero discord imports**.
- Discord code lives only in `tianling/discord_ui/` and `tianling/bot.py`.

## Data access

- Data access only through repository classes in `tianling/db/repositories/`.
- Services never write raw SQL.
- SQLite now, PostgreSQL-ready later.
- Every table with guild data has `guild_id`.

## Game data

- All game data (ranks, roles, heroes/legends, compositions, party limits, rank gaps, regions) lives in editable seed files under `config/` and is loaded into the DB.
- Never hardcode game data in code.

## Discord interactions

- Interactions must respond or defer within 3 seconds; defer first, then do DB work.
- Component limits: select menus max 25 options, modals max 5 fields, 5 buttons per row. Use two-step menus and pagination.
- Persistent views use stable `custom_id`s and are re-registered in `setup_hook`.

## Time and secrets

- Timers and deadlines are stored in the DB and enforced by a scheduler loop, never in-memory-only timers.
- Secrets only in `.env`; never log tokens.

## Quality gate

- After every change: run `pytest` and `ruff check`; keep tests green.
