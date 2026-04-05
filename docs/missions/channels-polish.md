> Living document. Update this, don't create new versions.

# Mission: Channels Polish (Pre-1.17.0)

Pre-release review findings from the channels refactor. Fix before or shortly after version bump.

## Critical (Must Fix) — DONE

- [x] **Duplicate `cmd_notify` in __main__.py** — Renamed to `cmd_notify_parent`, updated argparse wiring.

- [x] **Missing `dm_session_prefix` on DiscordConfig** — Added `dm_session_prefix: str = "discord-dm"` field.

## High (Should Fix) — DONE

- [x] **Shared MessageQueueManager** — Extracted `SessionQueueManager` to `base.py` as `MessageQueueManager` with injectable reaction callbacks. Both Discord and Slack now use it. Shared session helpers (`session_exists`, `ensure_session`, `wait_for_session_ready`) also extracted to base.py.

- [x] **Output truncation** — Added `max_message_length` class attribute on `ServiceChannel` (default 2000). Discord overrides to 1800, Slack to 2800. `truncate_output()` helper on base class. Module-level constants `DISCORD_MAX_MSG` / `SLACK_MAX_MSG` for command handlers.

## Medium (Post-Release OK)

- [ ] **Silent exception swallowing in WS listeners** — discord.py:821 and slack.py:349 have bare `except Exception: pass`. Add logging.

- [ ] **`voices_available()` is sync** — base.py TTS/STT primitives: `tts()` and `stt()` are async but `voices_available()` is sync with blocking I/O.

- [ ] **Test coverage gaps** — No happy-path send tests with mocks. Config state uses manual save/restore instead of fixtures. Comment says "6 channels" but checks 7.

## Low (Cleanup)

- [ ] Telegram `_get_telegram_config()` does manual YAML parsing instead of using registry config
- [ ] Quo `__post_init__` loads dotenv unnecessarily
- [ ] Email `import resend`/`import jinja2` at module level without guard (differs from SMS pattern)
