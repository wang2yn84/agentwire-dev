# Progressive Notification Escalation

**One-liner:** Notifications that escalate in urgency and channel when unacknowledged, preventing missed alerts during deep work.

## Problem

When running multiple sessions, important notifications can get lost:

1. **Attention tunneling** - You're focused on one session while another needs urgent input
2. **Notification fatigue** - Too many voice alerts make you tune them out
3. **Context switching cost** - Breaking flow for minor updates hurts productivity
4. **Missed completions** - Session finishes at 2am, you don't know until morning
5. **Channel mismatch** - Voice works at desk, but you stepped away to make coffee

Currently, notifications are fire-and-forget. The system speaks, and if you weren't listening, you miss it. There's no escalation, no acknowledgment tracking, no adaptation to your availability.

## Proposed Solution

Implement a **Progressive Escalation System** that tracks notification state and escalates through channels based on urgency and acknowledgment.

### Notification Tiers

| Tier | Trigger | Channels | Examples |
|------|---------|----------|----------|
| **Ambient** | Routine updates | Audio cue only | Worker spawned, task started |
| **Standard** | Normal completions | Voice + visual | Worker done, test passed |
| **Important** | Needs attention soon | Voice + push + visual | Worker blocked, error detected |
| **Urgent** | Needs immediate attention | All channels + escalate | Build failed, data loss risk |

### Escalation Ladder

```
1. Audio cue (immediate)
   └─ 30s no ack ─→
2. Voice announcement (full TTS)
   └─ 2m no ack ─→
3. Push notification (phone/desktop)
   └─ 5m no ack ─→
4. Email summary
   └─ 15m no ack ─→
5. Secondary contact (optional)
```

### Acknowledgment Signals

The system detects acknowledgment through:

- **Explicit** - Voice command "got it" / click notification / API call
- **Implicit** - Activity in the notifying session within 30s
- **Portal presence** - User viewing that session in browser

### Smart Batching

Related notifications get batched before escalation:

```
Instead of:
  "Worker 1 complete" (2:01pm)
  "Worker 2 complete" (2:02pm)
  "Worker 3 complete" (2:04pm)

Deliver:
  "3 workers completed. Project X ready for review." (2:05pm)
```

Batching rules:
- Same session → batch by default
- Same project → batch if within 5 minutes
- Different tiers → deliver separately (urgent never batched)

### Presence Detection

Adapt notification strategy based on detected availability:

| State | Detection | Strategy |
|-------|-----------|----------|
| **At desk** | Portal active, recent audio | Full voice |
| **Away briefly** | Portal idle < 15m | Queue → voice on return |
| **Away extended** | Portal idle > 15m | Push + email immediately |
| **DND mode** | Manual toggle | Only urgent, email only |
| **Sleeping** | Time of day + no activity | Queue until morning |

### CLI Integration

```bash
# Set personal notification preferences
agentwire notify config --tier standard --channels "voice,push"
agentwire notify config --tier urgent --channels "voice,push,email,sms"

# Acknowledge pending notifications
agentwire notify ack              # ack all
agentwire notify ack --session X  # ack for session

# Check pending
agentwire notify pending

# Snooze notifications
agentwire notify snooze 30m       # snooze all for 30 mins
agentwire notify snooze --session X 1h  # snooze specific session

# Do not disturb
agentwire notify dnd              # toggle DND
agentwire notify dnd --until 5pm  # DND until time
```

### MCP Tools

```python
agentwire_notify(
    text="Build failed on main branch",
    tier="urgent",           # ambient | standard | important | urgent
    session="website",       # source session
    ack_required=True,       # track acknowledgment
    batch_key="build-status" # group related notifications
)

agentwire_notify_ack(session="website")  # acknowledge notifications
agentwire_notify_pending()               # list unacknowledged
```

### Configuration

In `config.yaml`:

```yaml
notifications:
  escalation:
    enabled: true

  tiers:
    ambient:
      channels: [audio_cue]
      escalate: false
    standard:
      channels: [voice]
      escalate_after: 120  # seconds
      escalate_to: push
    important:
      channels: [voice, push]
      escalate_after: 300
      escalate_to: email
    urgent:
      channels: [voice, push, email]
      escalate_after: 900
      escalate_to: sms

  batching:
    enabled: true
    window: 300  # seconds
    max_batch: 10

  presence:
    idle_threshold: 900      # 15 mins
    away_threshold: 3600     # 1 hour
    quiet_hours:
      start: "22:00"
      end: "08:00"

  channels:
    push:
      service: pushover  # or ntfy, pushbullet
      user_key: "xxx"
    email:
      address: "user@example.com"
    sms:
      number: "+1234567890"
      service: twilio
```

## Implementation Considerations

### State Management

Need persistent state for:
- Pending notifications with timestamps
- Acknowledgment status per notification
- User presence/activity signals

Options:
1. **SQLite** - Simple, local, survives portal restarts
2. **Redis** - If we ever add it for other features
3. **File-based** - JSON files in `~/.agentwire/notifications/`

Recommendation: SQLite. It's lightweight, handles concurrent access, and we may want more structured queries later.

### Channel Adapters

Each notification channel needs an adapter:

```python
class NotificationChannel(Protocol):
    async def send(self, message: str, tier: str) -> bool: ...

class PushoverChannel(NotificationChannel):
    async def send(self, message, tier):
        urgency = {"urgent": 2, "important": 1}.get(tier, 0)
        return await pushover_api.send(message, priority=urgency)
```

Start with: Voice (existing), Email (existing via Resend), Push (Pushover/ntfy)

### Batching Logic

```python
class NotificationBatcher:
    def __init__(self, window=300):
        self.pending = defaultdict(list)

    def add(self, notification):
        key = notification.batch_key or notification.session
        self.pending[key].append(notification)

    async def flush_ready(self):
        now = time.time()
        for key, items in self.pending.items():
            oldest = min(n.timestamp for n in items)
            if now - oldest > self.window or len(items) >= self.max_batch:
                yield self.summarize(items)
                del self.pending[key]
```

### Presence Detection

Portal already tracks:
- WebSocket connections (browser open)
- Last activity timestamp
- Audio output (browser receiving audio)

Add:
- Keyboard/mouse activity via browser (optional)
- Mobile app presence (future)

## Potential Challenges

1. **Over-notification** - Even with batching, multi-session workflows could be noisy. Need per-session and global rate limits.

2. **Escalation storms** - If away for extended period, return to flood of escalated notifications. Need "catch-up" mode that summarizes instead.

3. **Acknowledgment accuracy** - Implicit acks (activity in session) might not mean user saw the notification. Could acknowledge something they missed.

4. **Channel reliability** - Push services can fail. Need fallback chains and retry logic.

5. **Privacy concerns** - Presence detection and activity tracking need to be opt-in and transparent. Don't log more than necessary.

6. **Time zone handling** - Quiet hours need to work correctly for remote machines in different time zones.

## Success Metrics

- Zero missed urgent notifications (all acknowledged or escalated)
- < 5 notifications per hour during active work (effective batching)
- Acknowledgment rate > 90% for important/urgent tier
- User-reported notification satisfaction (not annoying, not missing things)

## Future Extensions

- **ML-based prioritization** - Learn which notifications user cares about based on response patterns
- **Smart scheduling** - Delay non-urgent notifications until natural break points
- **Cross-device continuity** - Notification acknowledged on phone stops escalation on all devices
- **Team notifications** - Route certain notifications to team channels (Slack/Discord)
- **Notification analytics** - Dashboard showing notification patterns, response times, missed alerts
