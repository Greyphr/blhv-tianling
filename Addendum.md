# Tianling Addendum (overrides SPEC where they conflict)

## Decisions
- Ranks are self-reported. No rank verification in v1; the UI labels ranks as self-reported and members can edit them freely.
- Hosting: local machine for now, built portable. Config and token in .env, SQLite file in data/, single start script. Pending matches and voice cleanup must resume after restart.
- Database: SQLite via SQLAlchemy + Alembic behind repository classes, so PostgreSQL is a config switch later.
- Single guild (Blue Heaven Sect) but guild_id on every record.
- Full build, in layers: data/matching engine with tests first, then Discord interface.
- Guest teammates (friends not in the server) are allowed; the party leader enters their rank.
- Admin-only test mode with simulated players is required.

## Game data
- Deadlock ladder (verify in-game before launch), lowest to highest, each with subranks I-VI:
  Obscurus, Initiate, Seeker, Alchemist, Arcanist, Ritualist, Emissary, Archon, Oracle, Phantom, Ascendant, Eternus.
  (The original spec listed Acolyte, Sentinel and Mystic and omitted Alchemist, Arcanist and Archon.)
- All rank lists, team sizes, party limits and allowed rank gaps are editable config. Anything unverified is flagged verify_in_game.
- Defaults: Apex squad size 3 with no mandatory roles; Overwatch Open Queue size 5 with no roles; Deadlock size 6 with no roles; Open Queue and casual queues relax rank/role rules per config. Hero/Legend preference is a tie-breaker and display only, not a hard filter.
- Hero and Legend lists live in seed files.

## 7.10 Pre-made Parties and Slot Filling
A member may start matchmaking alone or with players they already have. Tianling asks how many are in the party (including the requester), each member's role, and optionally links each teammate's Discord account (linked: they must accept; guest: leader enters a rank). Tianling shows a summary and the party enters the queue as ONE entry, matched, reserved, confirmed and finalized as a unit.

Slot calculation: required composition minus declared roles = open slots. Each party member fills exactly one slot. Flexible members may be reassigned only to roles they accepted.

Validation: reject over-filled roles; party size cannot exceed LFG group size or the game's real party limit; a full party skips matching and offers direct group/voice creation; party rank range is lowest-to-highest (not average) and must satisfy the game's gap rule (parties can be stricter than solos); no member in two active parties/entries for the same activity; linked members must accept.

Matching: parties match against solos and other parties; prefer parties that fill each other's slots exactly, then fill with solos. If a member cancels/leaves, recalc slots and notify the rest. Declines by outside players do not penalize the party. Edits only before a proposal exists. Partial fill is admin-configurable.

Data: queue_entries gains party_id, party_size, leader_member_id, open_slots; new party_members table; match_members references party_id.
Interface: Party Size, Set Party Roles, Link Teammate, Confirm Party, Edit Party, Leave Party; /lfg join party_size:<n>, /lfg party add.
Changes elsewhere: 7.1/7.2 "party counts toward its declared role slots"; 7.8 party entries reserve all members atomically; 8 party members accepted at creation, only matched newcomers confirm; 12 and 14 as above; 17 party support is in Phase 2 (queue built around parties from the start).

## Matching rules added
- Time-based relaxation of rank gap and region as queue time grows (config curve).
- Atomic reservation: an Either-queue entry matched in one queue cancels its sibling.
- Queue expiry with a "still looking?" prompt.
- Decline/no-show cooldown (config-driven).
- Block/avoid list table exists but is not active in v1.

## Voice
- Rejoin permission for disconnected members; button to add a late friend; handle Discord channel caps; optional group text thread later.

## Discord constraints
- Respond within 3 seconds (defer then work). Select menus 25 options; modals 5 fields; 5 buttons per row. Stable custom_ids, views re-registered at startup. Deadlines are DB-backed and enforced by a scheduler. Intents: use only what is needed (members intent likely required for leave detection and role checks; verify). Permissions: Manage Channels, Move Members, Manage Roles only if pings are used.

## Clarified inconsistency
- Section 4.2 vs 6: LFG availability is hidden unless the member explicitly opts to display it ("opt-in" = a toggle in Matchmaking Preferences, default off).

## Out of scope for v1
Rank verification, reputation score, full analytics dashboard, multi-guild support, group text channels.

## Extra acceptance checks
- Member can queue with 1 to N-1 existing players and declare roles; only remaining slots are searched.
- Over-filled roles and oversized parties are rejected with a clear message.
- Linked teammate must accept before the party queues.
- Party rank spread respects configured gap.
- Party matched, confirmed and finalized as one unit; party edits and cancellations recalculate slots.
- Member data can be deleted on request and is purged on leaving the server.