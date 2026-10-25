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

## Rules added by the Fix 1–8 pass

- **Services never commit.** They `flush` and return events (`pop_events()`); the handler owns the
  transaction (`session_scope`) and dispatches events *after* committing.
- **Never call Discord while a transaction is open**, and never open a second `session_scope` inside
  another. SQLite takes `BEGIN IMMEDIATE`, so a nested scope would wait on the outer one forever.
- **Discord goes through `Gateway`** (`discord_ui/gateway.py`). `DiscordGateway` is production,
  `FakeGateway` is for tests. Rendering is done with pure `Card` objects.
- **State changes are status-guarded UPDATEs** (`transition`, `respond_if_pending`, `reserve_entries`),
  so double clicks and racing timeouts are no-ops.
- **Timestamps are timezone-aware UTC** (`UTCDateTime` column type, `core/timeutil.py`).
- **Wizard custom ids** start with `tianling:reg:`/`tianling:lfg:`/`tianling:prefs:`; persistent buttons
  encode their target id (`tianling:prop:accept:<group>`) and are `DynamicItem`s.
- Fake players are flagged `is_test` and are never DM'd or pinged; guests have negative synthetic ids.
