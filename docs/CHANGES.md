# Fix 1–8 change log

See the final report in the conversation for details. Highlights:

* **Fix 1** – ruff clean (`ruff check .` passes); `reserve_entries` no longer touches party fields;
  `delete_all_user_data` and purge share one code path; registry cleanup no longer swallows errors.
* **Fix 2** – `QueueService` rewritten: parties are ONE entry with every member persisted; only `queued`
  entries are matched; one SAVEPOINT per proposal; Either-queue siblings cancelled on reservation;
  real compositions/matching config; stable failed-roster hash; auto-confirm; party validation.
* **Fix 3** – `ConfirmationService` rewritten: decline/timeout release the others and re-match with
  carried-over acceptances; party rules; idempotent accept/decline/deadline; eligibility recheck.
* **Fix 4** – `Dispatcher`, `VoiceManager`, `Scheduler`, `bot.py` wired; one proposal message edited in place.
* **Fix 5** – restart-proof `DynamicItem` buttons for proposals, party invites, still-looking prompts.
* **Fix 6** – config-driven registration wizard, registry sync after commit, filters, preferences, setup.
* **Fix 7** – LFG wizard, party builder (guests, invites, roles, open slots), status, test mode.
* **Fix 8** – 174 tests incl. concurrency, restart recovery, UI component limits, migration drift.

Migration `0005_service_rewire` adds the new columns/tables. Run `alembic upgrade head` (the bot also does).
