# Pending Code Changes (Not Yet Pushed)

These changes were made locally and patched to remote machines but need to be pushed to git.

## 1. `default_session` Config Support

**File:** `agentwire/__main__.py`

**Change:** Added `config.get("default_session")` to session detection priority.

**Before:**
```python
session = args.session or _get_current_tmux_session() or _infer_session_from_path()
```

**After:**
```python
session = args.session or config.get("default_session") or _get_current_tmux_session() or _infer_session_from_path()
```

**Purpose:** Allows remote machines to specify a default target session for audio routing in their config:

```yaml
# ~/.agentwire/config.yaml on remote machine
default_session: "agentwire"
```

**Commit ready:** Yes, already committed locally as `7a1eb76`

---

## 2. `machine_id` Support in Portal Connection Check

**File:** `agentwire/__main__.py`

**Change:** Added `machine_id` config lookup when building session variants for portal connection check.

**Location:** `_check_portal_connections()` function

**Code added:**
```python
config = load_config()
machine_id = config.get("machine_id")
if machine_id:
    session_variants.append(f"{session}@{machine_id}")
```

**Purpose:** Allows the CLI to try `{session}@{machine_id}` when checking portal connections, useful when the remote hostname differs from the machine ID in the portal.

---

## 3. Issue Documentation

**File:** `docs/issues/remote-tts-session-detection.md`

**Purpose:** Full documentation of the remote TTS session detection problem and solutions.

---

## How to Apply

1. Push the local commit:
   ```bash
   git push origin main
   ```

2. Update remote machines:
   ```bash
   # eric-devbox
   ssh dev@138.197.145.5 "~/.local/bin/uv tool install --force git+https://github.com/dotdevdotdev/agentwire"

   # jordan-devbox (has local dev install, just pull)
   ssh dev@134.122.35.134 "cd ~/projects/agentwire-dev && git pull"
   ```

## Temporary Patches Applied

Both machines were patched directly in their installed copies:

- **eric-devbox:** `/home/dev/.local/share/uv/tools/agentwire-dev/lib/python3.12/site-packages/agentwire/__main__.py`
- **jordan-devbox:** `/home/dev/projects/agentwire-dev/agentwire/__main__.py`

These patches will be overwritten when reinstalling from git after the push.
