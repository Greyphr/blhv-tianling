# Tianling — Blue Heaven Sect Discord Bot
## Complete Project Specification: Player Profiles, Role-Balanced LFG & Automated Matchmaking

**Bot name:** Tianling (天灵)  
**Server:** Blue Heaven Sect  
**Theme:** Celestial Chinese fantasy / cultivation  
**Primary purpose:** Player registry and automated team formation for Marvel Rivals, Overwatch 2, Apex Legends, and Deadlock.

> **Project vision:** One Sect. Many Paths. One Call to Battle.

---

## 1. Project Overview

Tianling is a custom Discord bot for Blue Heaven Sect. It allows members to create game-specific profiles, browse a public player registry, find compatible teammates, and join automatically assembled groups with private temporary voice channels.

The system should be configurable, persistent, secure, and easy to use through Discord's interactive interface. Ordinary members should not need to memorize or type commands for routine registration and matchmaking.

### Core objectives

1. Let members create, edit, and delete profiles for supported games.
2. Maintain a public player registry synchronized with saved profile data.
3. Match members by game, activity, region, rank, role composition, preferences, and availability.
4. Fill remaining team-role slots when a member starts matchmaking.
5. Let participants review and accept or decline a proposed roster.
6. Create private temporary voice channels for finalized groups.
7. Provide configurable notifications and matchmaking preferences.
8. Give administrators control over ranks, team compositions, queues, channels, and behavior.
9. Preserve profiles, queues, matches, and channel state across bot restarts.

### Supported games

- Marvel Rivals
- Overwatch 2
- Apex Legends
- Deadlock

Members can maintain separate profiles for multiple games under one Discord account.

---

## 2. Discord Server Structure

Use one dedicated category for registration, the player registry, and LFG.

### Category: `『 📜 』 THE HEAVENLY REGISTRY`

Recommended channels:

- `📜・profile-registration` — persistent registration and profile-management panel.
- `👥・player-registry` — public game-profile listings.
- `⚔️・find-a-team` — interactive LFG and matchmaking panel.
- `📣・matchmaking-alerts` — proposed matches, confirmed groups, and updates.
- `🔔・matchmaking-preferences` — optional panel for notification preferences.
- `📖・tianling-guide` — optional usage guide and troubleshooting information.

Temporary voice channels should be created dynamically in a designated voice-channel category, not permanently pre-created.

Channel names and category names should be configurable by administrators.

---

## 3. Interactive Onboarding — No Command-First Experience

### 3.1 Design principle

Members should interact with Tianling primarily through buttons, select menus, and modal pop-up forms. Slash commands may exist for administration, troubleshooting, and optional power-user access, but they should not be required for normal onboarding or matchmaking.

### 3.2 Persistent Tianling panel

Post a persistent panel in `#profile-registration` with buttons such as:

- **Register / Edit My Profile**
- **Find My Team**
- **View My Profile**
- **Matchmaking Preferences**
- **Help**

The LFG channel should have a similar panel with a direct “Find My Team” action.

### 3.3 Registration flow

1. Member clicks **Register / Edit My Profile**.
2. Tianling presents supported games.
3. Member selects a game.
4. Tianling opens a guided form or sequence of menus for the fields required by that game.
5. Member chooses their region, current and peak rank, queue type, role, heroes/Legends, and other applicable preferences.
6. Member reviews and confirms the profile.
7. Tianling saves the profile and creates or updates its public registry entry.
8. Member can repeat the process for other games.

Only show fields relevant to the selected game. For example, do not ask Deadlock players to choose Overwatch-style roles.

### 3.4 Discord interaction constraints

- Use buttons and select menus for game choices, ranks, divisions, roles, activities, and confirmations.
- Use Discord modals for text input and short forms.
- Do not try to fit every possible field into one enormous modal.
- Use a persistent panel and re-register interactive handlers after bot restarts.
- Discord bots cannot arbitrarily open a modal just because someone joined the server. Instead, direct members to a welcome panel/button, optionally linked from Discord's server onboarding or welcome setup.
- Make all interactions ephemeral where appropriate, especially private profile editing and confirmation responses.

---

## 4. Player Profiles

Each member can have one profile per supported game. A profile must be independently editable and removable.

### 4.1 Shared profile fields

- Discord member ID, linked automatically.
- Game.
- In-game username.
- Region: EMEA, North America (NA), or Asia by default.
- Current rank and division/subrank, where applicable.
- Peak rank and division/subrank, where applicable.
- Game-specific preferences.
- Queue/activity preferences, where applicable.
- Matchmaking notification preference.
- Optional platform preference where useful.

### 4.2 Profile behavior

- Validate selections against the configured rank and preference lists.
- Save profile data in persistent storage.
- Create or update the member's public registry entry.
- Edit existing registry messages in place where possible.
- Prevent duplicate profiles for the same member and game unless multiple profiles are explicitly supported later.
- Allow a member to delete one game's profile without affecting other game profiles.
- Do not automatically queue a member simply because they created a profile.
- Recreate deleted registry messages from saved data when possible.
- Keep public and private information separate. Do not expose notification settings or LFG availability unless the member has opted to display them.

---

## 5. Game-Specific Profiles and Ranked Systems

**Important:** The rank lists below are the intended starting configuration, not a guarantee that every label is current in every live game season. Verify the current in-game ranked system before launch. Store rank definitions in configuration/database records so administrators can update them without changing application code.

### 5.1 Marvel Rivals

#### Intended rank order, lowest to highest

1. Bronze III, II, I
2. Silver III, II, I
3. Gold III, II, I
4. Platinum III, II, I
5. Diamond III, II, I
6. Grandmaster III, II, I
7. Celestial III, II, I
8. Eternity
9. One Above All

#### Additional profile fields

- Preferred role: Vanguard, Duelist (DPS), or Strategist (Support).
- Preferred heroes; allow multiple selections.
- Current competitive rank and division.
- Peak competitive rank and division.
- Activity preference, such as competitive or casual.

Eternity and One Above All should be stored without a normal division if the current game system treats them that way.

#### Default competitive role composition

A standard six-player Marvel Rivals team should be configured as:

- 2 Vanguards
- 2 Duelists
- 2 Strategists

This composition is a configurable default for the activities where it applies, not a universal rule for every game mode.

### 5.2 Overwatch 2

#### Intended rank order, lowest to highest

1. Bronze 5–1
2. Silver 5–1
3. Gold 5–1
4. Platinum 5–1
5. Emerald 5–1
6. Diamond 5–1
7. Master 5–1
8. Grandmaster 5–1
9. Champion 5–1
10. Top 500 — leaderboard distinction, not an ordinary division

#### Additional profile fields

- Competitive queue: Role Queue or Open Queue.
- Preferred role: Tank, Damage (DPS), or Support.
- Preferred heroes; allow multiple selections.
- Current and peak rank for the relevant queue.
- Activity preference, such as competitive or casual.

Track Role Queue and Open Queue ranks separately. Do not treat Top 500 as a normal numbered division.

#### Default Role Queue team composition

A standard five-player Role Queue team should be configured as:

- 1 Tank
- 2 Damage
- 2 Supports

Do not enforce this composition in Open Queue. Open Queue composition should be configurable and must not be forced into Role Queue rules.

### 5.3 Apex Legends

#### Intended rank order, lowest to highest

1. Rookie
2. Bronze IV–I
3. Silver IV–I
4. Gold IV–I
5. Platinum IV–I
6. Diamond IV–I
7. Master
8. Apex Predator — leaderboard distinction

#### Additional profile fields

- Preferred Legends; allow multiple selections.
- Preferred playstyle, such as aggressive, recon-oriented, or support-oriented.
- Platform, if needed for platform-specific grouping.
- Current and peak rank/division, where applicable.
- Activity preference, such as ranked or casual.

Treat Apex Predator as a leaderboard status rather than a normal numbered division. Verify the current rank structure and platform rules before deployment.

### 5.4 Deadlock

#### Intended rank order, lowest to highest

1. Obscurus — unranked or uncalibrated status, depending on the current system
2. Initiate I–VI
3. Seeker I–VI
4. Acolyte I–VI
5. Sentinel I–VI
6. Mystic I–VI
7. Ritualist I–VI
8. Emissary I–VI
9. Oracle I–VI
10. Phantom I–VI
11. Ascendant I–VI
12. Eternus I–VI

#### Additional profile fields

- Steam/in-game username.
- Preferred heroes; allow multiple selections.
- Queue preference: Ranked, Standard, or Either.
- Current and peak rank and subrank.
- Region.
- Matchmaking notification preference.

Deadlock is actively developed, so the rank system and queue options must be configurable. Do not force conventional Tank, Damage, and Support categories onto Deadlock.

### 5.5 Structured rank data

Do not store rank as a single unvalidated free-form string. Store structured fields such as:

- `game`
- `rank_tier`
- `division_or_subrank`
- `queue_type`, if applicable
- `season_or_version`, where useful
- `is_unranked`
- `is_leaderboard_status`
- `rank_order_value`, or a reference to a configured ordering table

Rank comparisons must only occur within the same game and compatible ranked pool. The rank order should be defined per game and queue type.

---

## 6. Public Player Registry

`#player-registry` is the Sect's directory of registered players.

### Profile entry should display

- Discord mention/display name.
- Game and in-game username.
- Region.
- Current rank.
- Peak rank.
- Game-specific role, hero, Legend, or playstyle preferences.
- Optional availability indicator, only if the member opts to show it.

### Registry behavior

- Organize entries by game.
- Provide filtering or selection menus where practical.
- Support multiple game profiles per Discord member.
- Edit existing registry messages in place where possible.
- Prevent duplicate entries on repeated registration or updates.
- Remove the relevant entry when a member deletes a game profile.
- Rebuild missing registry posts from stored data.
- Use pagination or separate game views as the registry grows.
- Do not publicly expose private notification settings.

---

## 7. LFG and Role-Balanced Matchmaking

### 7.1 Core principle

Tianling must be a **role-aware team builder**, not simply a random grouping tool. When a member selects **Find My Team**, the bot should identify the required remaining role slots and look for compatible players to fill them.

A member's saved public profile alone must never place them in a queue. Members must explicitly initiate an LFG request or join a queue.

### 7.2 Example: Marvel Rivals

Default six-player competitive composition:

| Role | Required |
|---|---:|
| Vanguard | 2 |
| Duelist / DPS | 2 |
| Strategist / Support | 2 |
| **Total** | **6** |

If a Duelist initiates matchmaking, Tianling needs:

- 1 additional Duelist
- 2 Vanguards
- 2 Strategists

The requester counts toward their selected role slot. The team is complete when all six slots are filled.

### 7.3 Example: Overwatch 2 Role Queue

Default five-player composition:

| Role | Required |
|---|---:|
| Tank | 1 |
| Damage / DPS | 2 |
| Support | 2 |
| **Total** | **5** |

If a Damage player initiates matchmaking, Tianling needs:

- 1 additional Damage player
- 1 Tank
- 2 Supports

The requester counts toward the selected role slot.

### 7.4 Role preferences and flexibility

- Ask the member for their preferred role.
- Allow members to select multiple roles they are willing to play.
- Keep the preferred role distinct from the list of acceptable roles.
- Do not assign someone to a role they have not agreed to play.
- Use flexibility to fill difficult or scarce slots when possible.
- If several compositions are possible, prioritize member preferences while completing the required composition.

### 7.5 Apex Legends

Apex does not use the same fixed Tank/DPS/Support composition as Marvel Rivals or Overwatch 2. Use a configurable squad size, region, activity, rank, platform rules if applicable, and preferred Legends/playstyles. Do not invent mandatory class counts unless the server explicitly configures them.

### 7.6 Deadlock

Deadlock's matchmaking should use its own configurable group-size rules, region, activity, and rank compatibility. Use preferred heroes as a secondary factor. Do not impose a conventional Tank/DPS/Support composition.

### 7.7 Matching priorities

Use the following priorities, subject to configurable exceptions:

1. Same game.
2. Compatible activity and queue type.
3. Same region by default.
4. Complete role composition, where the activity requires one.
5. Compatible current competitive ranks for ranked activities.
6. Role/hero/Legend preferences.
7. Availability and queue age.
8. Configured group size.

Role composition should be configurable by game and activity. Do not enforce Role Queue composition in Overwatch Open Queue. Casual queues may relax rank and role constraints according to server settings.

### 7.8 Queue rules

- Separate queues by game and incompatible activity.
- Overwatch 2 Role Queue and Open Queue should be tracked separately.
- Deadlock Ranked and Standard should be separate by default.
- Marvel Rivals and Apex activities should be configurable.
- An `Either` preference can make a member eligible for more than one compatible queue if the configuration permits it.
- Members may cancel an unfinalized request.
- Members should not be assigned to overlapping active groups for the same activity.
- Queue reservations and group creation must be atomic or otherwise protected from race conditions.
- Recheck eligibility immediately before finalization.
- Avoid repeatedly generating the same failed group.

### 7.9 Configurable group sizes

Group size must be configurable per game and activity. Do not hardcode the same number for every game.

The bot must distinguish a Discord LFG group from the game's actual party or match limits. If the proposed LFG group is larger than the game's party limit, warn the user or split it into valid parties according to configured rules.

---

## 8. Match Proposal, Confirmation, and Finalization

Tianling should normally present a proposed roster before finalizing a group.

### Workflow

1. A member starts LFG and chooses game, activity, region/role preferences as needed.
2. Tianling calculates the remaining team slots.
3. The matching engine finds eligible players and assembles a proposed roster.
4. The bot presents the roster, roles, relevant ranks, and activity.
5. Participants can accept or decline using buttons.
6. A configurable response deadline applies.
7. The bot rechecks eligibility and composition.
8. If accepted and valid, the group is finalized.
9. Tianling announces the group and creates its temporary voice channel.

### Confirmation rules

- Provide Accept and Decline buttons.
- Let administrators disable confirmation if they prefer automatic grouping.
- Handle declines and timeouts by cancelling or trying to replace the unavailable member.
- Revalidate group size, role composition, and eligibility before finalization.
- Do not announce a proposed roster as confirmed.
- Prevent the same member from accepting conflicting groups.
- If replacement fails, notify participants and release or preserve queue entries according to the configured policy.

---

## 9. Matchmaking Notifications

Members should be able to control whether they receive matchmaking notifications.

### Features

- Opt in or out of matchmaking notifications.
- Configure permitted role/member pings where appropriate.
- Receive a prompt when included in a proposed roster.
- Receive a message when the group is finalized.
- Show game, activity, region, and relevant rank range.
- Mention or link to the group's temporary voice channel.
- Receive cancellation or replacement updates.
- Rate-limit repeated alerts and respect Discord permissions.

### Example announcement

**⚔️ Heavenly Match Found**

- **Game:** Marvel Rivals
- **Activity:** Competitive
- **Region:** EMEA
- **Roster:** Confirmed members and their assigned roles
- **Ranks:** Actual current ranks from confirmed profiles
- **Voice:** Link to the group's private temporary channel

This is an example format. All values must be populated from real match data.

---

## 10. Temporary Voice Channels

After a group is finalized, Tianling creates a dedicated voice channel.

### Channel requirements

- Create it under an administrator-designated category.
- Use a configurable naming format, for example `⚔️・marvel-rivals-01`.
- Grant access to confirmed participants.
- Grant access to designated moderators and administrators.
- Deny unrelated members access by default.
- Include the voice-channel link in the group announcement.
- Store the channel ID and its associated group ID.

### Cleanup behavior

1. Monitor the temporary channel.
2. When all members leave, start a configurable grace period.
3. If a participant rejoins before the grace period ends, cancel deletion.
4. If the channel remains empty, delete it.
5. Log failures and handle missing channels or permission changes gracefully.
6. Never delete permanent channels or channels Tianling did not create.

### Failure fallback

If Tianling cannot create the channel, retain the finalized group, alert participants and administrators, log the error, and use a configured fallback such as an existing shared voice channel.

---

## 11. Administrator Configuration

Routine changes should be possible through a restricted admin panel or administrative commands.

Configurable settings should include:

- Supported games.
- Rank tiers, divisions, subranks, and rank ordering.
- Regions.
- Game-specific roles, heroes, Legends, and playstyles.
- Queue types and activities.
- Role composition by game and activity.
- Required group sizes.
- Allowed rank gaps.
- Cross-region matching and fallback rules.
- Queue expiration and timeout behavior.
- Confirmation prompts and response deadlines.
- Partially filled group behavior.
- Registry, alerts, and voice-channel destinations.
- Temporary voice-channel cleanup grace period.
- Notification roles, mentions, and rate limits.
- Logging destinations and retention policy.

### Suggested admin commands

- `/tianling setup`
- `/tianling config`
- `/tianling ranks`
- `/tianling queue`
- `/tianling groups`
- `/tianling profile`
- `/tianling logs`

Commands are optional for ordinary users, but may be useful for administrators and troubleshooting. Restrict all sensitive settings and actions to authorized roles.

---

## 12. Database and Persistence

Use persistent storage; do not keep profiles and queues only in memory.

### Suggested data entities

- `users`
- `game_profiles`
- `rank_definitions`
- `game_preferences`
- `notification_settings`
- `queue_entries`
- `match_groups`
- `match_members`
- `temporary_channels`
- `guild_config`
- `audit_logs`

### Data to persist

- Discord member IDs.
- Game usernames and profile fields.
- Current and peak ranks.
- Rank divisions/subranks and queue type.
- Game-specific preferences.
- Regions and notification preferences.
- Queue entries and timestamps.
- Proposed and confirmed groups.
- Member responses and confirmation deadlines.
- Temporary voice-channel IDs.
- Administrator configuration.
- Important audit and error records.

### Reliability requirements

- Recover active state after restarts.
- Prevent duplicate profiles, queue entries, and groups.
- Handle simultaneous queue joins safely.
- Reconcile stored voice-channel IDs with actual Discord channels.
- Retry recoverable API operations with sensible limits.
- Log unexpected errors without exposing secrets.
- Provide safe recovery procedures for stuck or inconsistent groups.

---

## 13. Security and Permissions

- Store bot tokens in environment variables or a secret manager.
- Never commit tokens or credentials to source control.
- Restrict administrative actions by Discord role/permission.
- Validate all user input.
- Prevent unauthorized profile edits/deletions.
- Configure temporary voice-channel permissions carefully.
- Avoid excessive mentions and notification spam.
- Keep logs free of credentials and unnecessary personal data.
- Handle Discord rate limits and API failures safely.

---

## 14. Commands and Interactive Controls

The user experience should be click-first. Suggested commands are a fallback and administrative toolset, not the main onboarding method.

| Suggested command | Function |
|---|---|
| `/profile create` | Create a profile for a selected game |
| `/profile view` | View a saved profile |
| `/profile edit` | Edit a profile |
| `/profile delete` | Delete a selected game profile |
| `/profile list` | List the member's registered games |
| `/lfg join` | Join a matchmaking queue |
| `/lfg leave` | Leave a queue |
| `/lfg status` | View queue/match status |
| `/lfg preferences` | Configure preferences |
| `/lfg cancel` | Cancel an eligible pending request/proposal |
| `/tianling help` | View instructions |

### Preferred buttons and menus

- Register / Edit My Profile
- Find My Team
- View My Profile
- Matchmaking Preferences
- Select Game
- Select Activity
- Select Region
- Select Rank and Division
- Select Preferred Role
- Select Acceptable Roles
- Select Heroes/Legends
- Join Queue
- Leave Queue
- Accept Match
- Decline Match
- Cancel Request
- Help

Tianling should only ask for information relevant to the selected game and activity.

---

## 15. Error Handling and Edge Cases

Handle these cases explicitly:

- A member leaves the server or deletes their account.
- A member edits a profile while queued.
- A member goes offline or becomes unavailable during confirmation.
- A participant declines or times out.
- Not enough compatible players are available.
- A queue is stale or contains duplicate entries.
- A temporary voice channel is manually deleted.
- Tianling loses channel-management permissions.
- Discord rate limits delay an operation.
- The bot restarts while a match is pending.
- A game adds, removes, or renames a rank.
- A rank or preference is no longer valid.
- A member attempts incompatible simultaneous queue entries.

The bot should recover safely where possible and provide a clear explanation when user or administrator action is needed.

---

## 16. Suggested Technical Architecture

### Recommended starting stack

- **Language:** Python.
- **Discord library:** `discord.py` or another maintained Discord library.
- **Database:** SQLite for a small initial deployment; PostgreSQL if the bot grows or needs stronger multi-process support.
- **Secrets:** Environment variables or a secret manager.
- **Hosting:** A persistent server or VM with restart supervision.

Separate the core matchmaking logic from Discord command/event handlers so the matching engine can be tested independently.

### Logical components

1. **Discord interface:** Buttons, menus, modals, embeds, alerts, and voice channels.
2. **Profile service:** Validation, profile CRUD, and registry synchronization.
3. **Rank service:** Configurable rank definitions and rank comparisons.
4. **Queue manager:** Queue entry, cancellation, eligibility, and reservations.
5. **Matchmaking engine:** Role-slot calculation, rank/region filters, and group composition.
6. **Confirmation service:** Accept/Decline, timeouts, and replacements.
7. **Voice-channel manager:** Creation, permission overwrites, cleanup, and recovery.
8. **Notification service:** Opt-in alerts, rate limiting, and announcements.
9. **Admin configuration:** Rank lists, group compositions, channels, and policies.
10. **Persistence and logs:** Database transactions, recovery, and audit records.

---

## 17. Development Phases

### Phase 1 — Foundation and Profiles

- Set up the bot and database.
- Implement interactive onboarding.
- Add the four game profiles and configurable rank lists.
- Implement profile creation, editing, deletion, and public registry synchronization.

### Phase 2 — Queue and Matchmaking Engine

- Add explicit queue entry and cancellation.
- Implement game/activity separation.
- Implement role-aware composition for Marvel Rivals and Overwatch 2 Role Queue.
- Add configurable group sizes and rules for Apex Legends and Deadlock.
- Implement region/rank matching and duplicate prevention.

### Phase 3 — Confirmation and Notifications

- Add proposed rosters and Accept/Decline buttons.
- Implement deadlines, cancellations, replacement attempts, and group announcements.
- Add notification preferences and rate limiting.

### Phase 4 — Temporary Voice Channels

- Create private channels for finalized groups.
- Configure access permissions.
- Implement empty-channel cleanup and restart recovery.
- Add fallback behavior for channel-creation failures.

### Phase 5 — Administration, Testing, and Deployment

- Add the admin panel and configuration controls.
- Test rank updates, queues, role composition, and simultaneous joins.
- Test restart recovery, permission errors, and Discord rate limits.
- Document installation, configuration, backups, and maintenance.

---

## 18. Acceptance Checklist

Tianling is ready for use when all of the following work:

- [ ] Members can create, edit, and delete separate profiles for all four games.
- [ ] Rank tiers and divisions/subranks are configurable.
- [ ] Registry entries stay synchronized with saved profiles.
- [ ] Ordinary onboarding and matchmaking can be completed through buttons, menus, and modals.
- [ ] Members must explicitly enter LFG before they can be matched.
- [ ] Marvel Rivals teams use the configured 2 Vanguard / 2 Duelist / 2 Strategist composition where applicable.
- [ ] Overwatch 2 Role Queue teams use the configured 1 Tank / 2 Damage / 2 Support composition.
- [ ] Members can indicate preferred and acceptable roles.
- [ ] Tianling fills remaining role slots without assigning players to roles they have not accepted.
- [ ] Apex Legends and Deadlock use their own configurable matching rules.
- [ ] Ranked and casual activities follow their configured policies.
- [ ] Members cannot be assigned to conflicting active groups.
- [ ] Match confirmation handles acceptance, declines, timeouts, and cancellation.
- [ ] Finalized groups receive announcements and private temporary voice channels.
- [ ] Empty temporary channels are cleaned up without deleting permanent channels.
- [ ] Profiles, queues, and active group state survive bot restarts.
- [ ] Administrators can update ranks, compositions, channels, and matching rules.
- [ ] Errors, permission failures, and Discord API rate limits are handled safely.

---

## 19. Final Project Definition

**Tianling is Blue Heaven Sect's centralized player-profile and automated LFG matchmaking bot.** It supports Marvel Rivals, Overwatch 2, Apex Legends, and Deadlock. Members use interactive Discord panels to register their gaming identities, maintain ranks and preferences, and find compatible teammates.

For games with fixed role compositions, Tianling calculates the remaining slots when a member starts matchmaking and searches for players who can fill those roles. It presents the proposed roster for review and, once finalized, announces the group and creates a private temporary voice channel.

The system must remain configurable, persistent, secure, and maintainable. Rank lists and game-specific rules should be verified before deployment and stored as editable configuration rather than permanently hardcoded assumptions.
