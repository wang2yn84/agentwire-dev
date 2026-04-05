> Living document. Update this, don't create new versions.

# Mission: Channels Polish (Pre-1.17.0)

Pre-release review findings from the channels refactor. Fix before or shortly after version bump.

## Critical (Must Fix) — DONE

- [x] **Duplicate `cmd_notify` in __main__.py** — Renamed to `cmd_notify_parent`, updated argparse wiring.

- [x] **Missing `dm_session_prefix` on DiscordConfig** — Added `dm_session_prefix: str = "discord-dm"` field.

## High (Should Fix) — DONE

- [x] **Shared MessageQueueManager** — Extracted `SessionQueueManager` to `base.py` as `MessageQueueManager` with injectable reaction callbacks. Both Discord and Slack now use it. Shared session helpers (`session_exists`, `ensure_session`, `wait_for_session_ready`) also extracted to base.py.

- [x] **Output truncation** — Added `max_message_length` class attribute on `ServiceChannel` (default 2000). Discord overrides to 1800, Slack to 2800. `truncate_output()` helper on base class. Module-level constants `DISCORD_MAX_MSG` / `SLACK_MAX_MSG` for command handlers.

## Medium (Post-Release OK) — DONE

- [x] **Silent exception swallowing in WS listeners** — Added descriptive logging to all silent `except Exception: pass` in Discord/Slack WS listeners and event handlers. ImportError now logged too.

- [x] **`voices_available()` is sync** — Made async to match `tts()` and `stt()` for consistent primitives API.

- [x] **Test coverage gaps** — Added 12 happy-path send tests with mocks (email, telegram, quo, sms, webhook). Added config fixture for safe state management. Fixed stale "6 channels" comments to "7". 100 unit tests + 16 integration = 116 total, all passing.

## Low (Cleanup)

- [ ] Telegram `_get_telegram_config()` does manual YAML parsing instead of using registry config
- [ ] Quo `__post_init__` loads dotenv unnecessarily
- [ ] Email `import resend`/`import jinja2` at module level without guard (differs from SMS pattern)
