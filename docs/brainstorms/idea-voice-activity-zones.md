# Voice Activity Zones

> Contextual voice behavior that automatically adapts based on time, calendar events, and activity patterns.

## Problem

AgentWire's voice-first interface assumes you're always ready to hear spoken updates. In reality:

1. **Meeting conflicts** - Agent announces "Build complete!" while you're presenting to clients
2. **Time-inappropriate** - Loud voice alerts at 11pm wake up the household
3. **Context switching** - Driving needs verbose audio; desk work needs concise
4. **Focus time** - Deep work sessions get interrupted by routine status updates
5. **Manual mode switching** - Constantly telling agents "use text only" / "you can speak again"

The system has no awareness of when voice is appropriate vs. disruptive. Users either disable voice entirely or suffer poorly-timed interruptions.

## Proposed Solution

**Voice Activity Zones** - Define contexts (time windows, calendar events, device states) with specific voice behaviors. The system automatically applies the right behavior based on current context.

### Zone Types

| Zone | Trigger | Voice Behavior |
|------|---------|----------------|
| **Focus** | Calendar: "Focus time" | Silent - text only |
| **Meeting** | Calendar: Meeting detected | Silent + queue for later |
| **Commute** | Time: 7-8am, 5-6pm | Verbose audio, summaries |
| **Deep Work** | Manual toggle or schedule | Critical alerts only |
| **After Hours** | Time: 10pm-7am | Whisper mode + urgent only |
| **Default** | No zone active | Normal voice behavior |

### Zone Definition

```yaml
# ~/.agentwire/config.yaml
voice_zones:
  enabled: true
  calendar_integration: google  # google, apple, outlook

  zones:
    - name: meeting
      triggers:
        - type: calendar
          match: "meeting|standup|1:1|sync|call"
      behavior:
        voice: silent
        notifications: queue
        queue_release: after_event  # speak queued items when meeting ends
        urgent_override: true       # security/error alerts still speak

    - name: focus
      triggers:
        - type: calendar
          match: "focus|deep work|heads down"
        - type: schedule
          hours: "09:00-11:00"
          days: ["mon", "tue", "wed", "thu", "fri"]
      behavior:
        voice: silent
        notifications: text_only
        urgent_override: true

    - name: commute
      triggers:
        - type: schedule
          hours: "07:00-08:30"
          days: ["mon", "tue", "wed", "thu", "fri"]
        - type: schedule
          hours: "17:00-18:30"
          days: ["mon", "tue", "wed", "thu", "fri"]
      behavior:
        voice: verbose
        speak_summaries: true
        repeat_key_info: true
        pause_between_items: 2s

    - name: after_hours
      triggers:
        - type: schedule
          hours: "22:00-07:00"
      behavior:
        voice: whisper          # lower volume
        notifications: urgent_only
        urgent_keywords: ["error", "failed", "security", "critical"]

    - name: airplane
      triggers:
        - type: manual
          command: "agentwire zone enter airplane"
      behavior:
        voice: silent
        notifications: queue
        tts_backend: none
```

### Calendar Integration

Pull events from user's calendar to detect meetings/focus time:

```python
async def get_current_calendar_events() -> list[CalendarEvent]:
    """Fetch events happening now from configured calendar."""

    if config.calendar_integration == "google":
        return await google_calendar.get_current_events()
    elif config.calendar_integration == "apple":
        return await apple_calendar.get_current_events()  # via icalBuddy
    elif config.calendar_integration == "outlook":
        return await outlook_calendar.get_current_events()

def check_calendar_triggers(events: list[CalendarEvent], zone: Zone) -> bool:
    """Check if any current event matches zone's calendar trigger."""
    for event in events:
        for trigger in zone.triggers:
            if trigger.type == "calendar":
                if re.search(trigger.match, event.title, re.IGNORECASE):
                    return True
    return False
```

### Zone Resolution

When multiple zones could apply, use priority ordering:

```python
ZONE_PRIORITY = [
    "manual",      # Explicit user override always wins
    "meeting",     # Calendar meetings
    "focus",       # Calendar focus time
    "commute",     # Scheduled commute windows
    "after_hours", # Night mode
    "default",     # Fallback
]

def resolve_active_zone() -> Zone:
    """Determine which zone is currently active."""

    calendar_events = get_current_calendar_events()
    current_time = datetime.now()

    for zone_name in ZONE_PRIORITY:
        zone = zones[zone_name]

        if is_zone_triggered(zone, calendar_events, current_time):
            return zone

    return zones["default"]
```

### Notification Queuing

When voice is suppressed, queue notifications for later:

```python
class NotificationQueue:
    def __init__(self):
        self.queued: list[Notification] = []

    async def enqueue(self, notification: Notification):
        """Queue a notification for later delivery."""
        self.queued.append(notification)

        # Text notification still goes through
        await send_text_notification(notification)

    async def release(self, reason: str):
        """Release queued notifications (e.g., meeting ended)."""
        if not self.queued:
            return

        # Consolidate into summary
        summary = summarize_notifications(self.queued)

        await speak(f"You have {len(self.queued)} queued updates. {summary}")

        self.queued.clear()
```

**Queue release triggers:**
- Meeting ends (calendar event over)
- Manual release: "Release queued notifications"
- Zone transition to voice-enabled zone
- Urgent notification overrides queue

### Voice Behavior Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `normal` | Standard voice output | Default operation |
| `verbose` | Extra context, slower pace, summaries | Commute, hands-free |
| `concise` | Minimal words, facts only | Desk work |
| `whisper` | Lower volume, soft tone | After hours |
| `silent` | No voice output | Meetings, focus |

**Verbose mode example:**
```
# Normal
"Build complete."

# Verbose
"Good news - the build just finished successfully.
All 47 tests passed. No warnings.
The api-server session is ready for your next task."
```

### Zone Transitions

Announce zone transitions so user knows behavior changed:

```python
async def on_zone_change(old_zone: Zone, new_zone: Zone):
    """Handle zone transition."""

    # Only announce if transitioning TO a voice-enabled zone
    if new_zone.behavior.voice in ["normal", "verbose", "concise"]:
        await speak(f"Entering {new_zone.name} mode.")

        # Release any queued notifications
        if old_zone.behavior.notifications == "queue":
            await notification_queue.release(f"exiting {old_zone.name}")
```

### Urgent Override

Some notifications are too important to suppress:

```python
URGENT_PATTERNS = [
    r"security|breach|unauthorized",
    r"error|failed|crashed|exception",
    r"critical|emergency|urgent",
    r"disk full|out of memory|oom",
    r"build failed|deploy failed|tests failed",
]

def is_urgent(notification: Notification) -> bool:
    """Check if notification is urgent enough to override zone."""

    text = notification.text.lower()
    for pattern in URGENT_PATTERNS:
        if re.search(pattern, text):
            return True
    return False

async def deliver_notification(notification: Notification):
    """Deliver notification respecting current zone."""

    zone = resolve_active_zone()

    # Urgent always speaks (unless explicitly disabled)
    if is_urgent(notification) and zone.behavior.urgent_override:
        await speak(f"Urgent: {notification.text}")
        return

    # Apply zone behavior
    if zone.behavior.voice == "silent":
        if zone.behavior.notifications == "queue":
            await notification_queue.enqueue(notification)
        else:
            await send_text_notification(notification)
    else:
        await speak(notification.text, mode=zone.behavior.voice)
```

## CLI Commands

```bash
# View current zone
agentwire zone status
# Output: Active zone: meeting (until 3:00 PM)
#         Voice: silent, Notifications: queued (3 items)

# List all zones
agentwire zone list

# Manual zone entry
agentwire zone enter focus
agentwire zone enter airplane

# Exit manual zone (return to auto-detection)
agentwire zone exit

# Check what zone would be active at a time
agentwire zone check "tomorrow 9am"
# Output: focus (schedule: 09:00-11:00 weekdays)

# Release queued notifications now
agentwire zone release

# Temporarily override for next N minutes
agentwire zone override --voice normal --duration 5m
```

## Voice Commands

| Command | Effect |
|---------|--------|
| "What zone am I in?" | Announces current zone |
| "Enter focus mode" | Manual focus zone |
| "Exit focus mode" | Return to auto-detection |
| "You can speak now" | Override to normal voice |
| "Go silent" | Manual silent mode |
| "What's queued?" | Read queue count and summary |
| "Release notifications" | Speak all queued items |
| "Speak freely until my next meeting" | Temporary override |

## MCP Tools

```python
@mcp.tool()
def zone_status() -> str:
    """Get current voice zone status.

    Returns active zone, behavior settings, and queue status.
    """

@mcp.tool()
def zone_enter(zone_name: str, duration: str | None = None) -> str:
    """Manually enter a voice zone.

    Args:
        zone_name: Zone to enter (focus, meeting, airplane, etc.)
        duration: Optional duration (e.g., "30m", "2h")
    """

@mcp.tool()
def zone_exit() -> str:
    """Exit manual zone and return to auto-detection."""

@mcp.tool()
def zone_release_queue() -> str:
    """Release all queued notifications immediately."""
```

## Implementation Considerations

### Calendar Polling

Poll calendar every 5 minutes + on zone status checks:

```python
class CalendarWatcher:
    def __init__(self):
        self.last_fetch = None
        self.cached_events = []
        self.poll_interval = 300  # 5 minutes

    async def get_current_events(self) -> list[CalendarEvent]:
        now = time.time()

        if self.last_fetch and now - self.last_fetch < self.poll_interval:
            return self.cached_events

        self.cached_events = await fetch_calendar_events()
        self.last_fetch = now
        return self.cached_events
```

### Google Calendar Integration

```python
# Using Google Calendar API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

async def google_calendar_events() -> list[CalendarEvent]:
    creds = Credentials.from_authorized_user_file(
        '~/.agentwire/google_calendar_token.json'
    )
    service = build('calendar', 'v3', credentials=creds)

    now = datetime.utcnow().isoformat() + 'Z'
    soon = (datetime.utcnow() + timedelta(minutes=5)).isoformat() + 'Z'

    events_result = service.events().list(
        calendarId='primary',
        timeMin=now,
        timeMax=soon,
        singleEvents=True
    ).execute()

    return [CalendarEvent(e) for e in events_result.get('items', [])]
```

### Apple Calendar Integration

```bash
# Via icalBuddy (macOS)
icalBuddy -ea -n -li 5 eventsFrom:now to:'+5 min'
```

```python
async def apple_calendar_events() -> list[CalendarEvent]:
    result = await run_command([
        "icalBuddy", "-ea", "-n", "-li", "5",
        "eventsFrom:now", "to:'+5 min'"
    ])
    return parse_icalbuddy_output(result)
```

### Storage

```
~/.agentwire/
  zones/
    config.yaml       # Zone definitions
    manual_override   # Current manual zone (if any)
    queue.jsonl       # Queued notifications
  calendar/
    google_token.json
    cached_events.json
```

## Portal UI

Zone indicator in portal header:

```
┌──────────────────────────────────────────────────────────────────┐
│ AgentWire Portal          🔇 Meeting Mode (until 3:00 PM)        │
│                           [3 notifications queued]               │
├──────────────────────────────────────────────────────────────────┤
```

Zone settings panel:

```
┌─ Voice Zones ────────────────────────────────────────────────────┐
│                                                                  │
│ Current Zone: meeting                                            │
│ Source: Calendar event "Team Standup"                            │
│ Expires: 3:00 PM (12 minutes)                                    │
│                                                                  │
│ [Override: Speak Now]  [Release Queue (3)]  [Edit Zones]        │
│                                                                  │
│ ─── Today's Schedule ────────────────────────────────────────── │
│  9:00 AM - 11:00 AM  focus (schedule)                           │
│ 10:30 AM -  3:00 PM  meeting (calendar)     ← active            │
│  5:00 PM -  6:30 PM  commute (schedule)                         │
│ 10:00 PM -  7:00 AM  after_hours (schedule)                     │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Potential Challenges

1. **Calendar Access Permissions**
   - Google/Outlook require OAuth setup
   - Apple Calendar needs system permissions
   - Mitigation: Detailed setup guide, fallback to schedule-only zones

2. **Meeting Detection Accuracy**
   - Not all calendar events are meetings
   - "Lunch" or "Gym" shouldn't trigger meeting mode
   - Mitigation: Configurable match patterns, negative patterns ("lunch|gym|dentist")

3. **Zone Conflicts**
   - Calendar shows meeting but user is at desk
   - Mitigation: Priority ordering, manual override always wins

4. **Queue Growth**
   - Long meetings accumulate many notifications
   - Mitigation: Queue limits, consolidation, summary on release

5. **Time Zone Handling**
   - Schedules need to work across time zones
   - Mitigation: Store in local time, convert on comparison

6. **Latency on Zone Detection**
   - Calendar polling has delay
   - Mitigation: Event webhooks where supported, shorter poll for next 15min

## Success Criteria

1. Zero voice interruptions during calendar meetings
2. Users receive queued updates within 1 minute of meeting end
3. Urgent notifications still reach user within 10 seconds
4. 80%+ of users enable at least one zone
5. Reduction in "be quiet" / "use text" manual commands

## Future Extensions

- **Location-based zones**: Home vs office vs coffee shop
- **Device-based zones**: Phone = verbose, laptop = concise
- **Smart detection**: ML model learns when you're interruptible
- **Team awareness**: Know when teammates are in focus mode
- **Slack/Teams integration**: Status sync with messaging apps
