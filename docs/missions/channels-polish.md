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

## Low (Cleanup) — DONE

- [x] Telegram `_get_telegram_config()` — Replaced manual YAML/env parsing with `get_config().channels.get("telegram")`, matching all other channels.
- [x] Quo `__post_init__` — Removed `load_dotenv()` call, now uses `os.environ.get()` like every other channel.
- [x] Email imports — `import resend` guarded with `try/except` at module level (sets `resend = None` on failure). `jinja2` guarded inside `_render_email_template()` with HTML fallback.
