# Session Handshake: Intelligent Context Restoration

> When you return to a session after time away, the agent proactively briefs you on state rather than waiting for you to ask.

## Problem

Resuming work after stepping away is friction-heavy:

```
[User opens session after 2 hours away]
[Agent]: ...waiting for input...
[User]: "What were we working on?"
[Agent]: [reads files, reconstructs context, provides summary]
[User]: "What's left to do?"
[Agent]: [another round trip]
[User]: "Were there any issues?"
[Agent]: [another round trip]
```

This back-and-forth wastes time and breaks flow. The agent has all the information but waits passively for the human to extract it question by question.

**Real scenarios:**
- Lunch break → forgot what the next step was
- Morning standup → need to remember yesterday's progress
- Context switch between projects → which one was blocked on what?
- Handing off to another person → they need the full picture fast

The voice interface makes this worse: reading a scroll-back buffer isn't practical when your interface is audio.

## Proposed Solution

**Automatic handshake on session resume** - when a session detects you've returned after inactivity, it proactively provides a brief.

### Handshake Flow

```
[User returns to session after 45+ minutes]
[System detects presence: portal reconnect, voice activity, or terminal focus]

[Agent TTS, unprompted]:
"Welcome back. Quick brief:
 I finished the auth endpoints, all tests passing.
 I'm blocked on the email service - need the SMTP config.
 There's one lint warning I'd fix if you want.
 Ready when you are."

[User]: "What's the lint warning?"
[Agent]: [dives into detail]
```

The handshake is:
1. **Automatic** - triggered by presence, not by asking
2. **Concise** - 15-30 seconds of audio max
3. **Prioritized** - blockers first, wins second, nice-to-haves last
4. **Actionable** - ends with clear next options

### Presence Detection

Detect "user returned" via multiple signals:

| Signal | Detection Method |
|--------|-----------------|
| Portal reconnect | WebSocket connection from known device |
| Voice activity | Push-to-talk pressed after silence |
| Terminal focus | tmux attach or window focus event |
| Activity after idle | Any input after 30+ min of none |

Configurable threshold for what counts as "away":

```yaml
handshake:
  away_threshold_minutes: 30  # Don't handshake for quick breaks
  max_brief_seconds: 30       # Keep it short
```

### Brief Structure

The agent assembles the brief from structured sources:

```python
@dataclass
class HandshakeBrief:
    # What was being worked on
    task_summary: str        # "Adding user auth endpoints"

    # Current state
    status: Literal["completed", "in_progress", "blocked", "failed"]
    progress: str            # "3 of 5 endpoints done"

    # What needs attention
    blockers: list[str]      # ["Need SMTP config for email"]
    decisions_needed: list[str]  # ["OAuth vs session auth?"]

    # Recent events
    completions: list[str]   # ["Login endpoint working"]
    issues: list[str]        # ["One flaky test"]

    # Suggested next action
    suggested_action: str    # "Want me to continue with signup?"
```

Voice template:

```
"Welcome back. [Quick brief]:
 [status of main task].
 [blockers if any].
 [key completions if recent].
 [decision if pending].
 [suggested next action]."
```

### State Persistence

For the handshake to work, sessions need persistent state:

```yaml
# .agentwire/session-state.yml (auto-maintained by agent)
current_task:
  description: "Add authentication endpoints"
  started_at: "2024-01-15T10:30:00Z"

status: in_progress

progress:
  - item: "POST /login"
    done: true
  - item: "POST /signup"
    done: true
  - item: "POST /logout"
    done: false

blockers:
  - "Need SMTP credentials for password reset emails"

recent_completions:
  - what: "Login endpoint with JWT"
    when: "2024-01-15T11:45:00Z"

pending_decisions:
  - question: "Should signup require email verification?"
    options: ["Yes, verify first", "No, verify later", "Make it configurable"]

last_handshake_at: "2024-01-15T10:30:00Z"
```

Agents update this file as they work (part of worker role instructions).

### Multi-Worker Rollup

When orchestrator has workers, handshake includes their state:

```
"Welcome back. Brief:
 Worker one finished the API routes, tests passing.
 Worker two is still on the frontend forms - 60% done.
 I'm ready to review and merge when worker two completes.
 ETA about 5 minutes."
```

The orchestrator polls worker states before speaking.

### Handshake Suppression

Don't handshake when:
- User was away less than threshold (just a quick break)
- User immediately starts typing/talking (they know what they want)
- Handshake was given recently (don't repeat)
- Session is fresh (nothing to report yet)

```python
def should_handshake(session: Session) -> bool:
    if session.age < timedelta(minutes=5):
        return False  # Fresh session
    if session.last_handshake and
       (now() - session.last_handshake) < timedelta(minutes=15):
        return False  # Too recent
    if session.idle_duration < config.away_threshold:
        return False  # Quick break
    return True
```

### Grace Period

After detecting return, wait 2-3 seconds before speaking. User might:
- Start talking immediately (they know what they want)
- Start typing a command

If activity happens in grace period, skip the handshake.

```
[User reconnects to portal]
[System]: (waits 3 seconds)
[User]: "Continue with the signup endpoint"
[System]: (skips handshake, agent processes command)
```

## Implementation

### Phase 1: State Tracking

Add state file maintenance to worker/leader roles:

```markdown
## Session State

Maintain `.agentwire/session-state.yml` with:
- Current task description
- Progress tracking
- Blockers encountered
- Recent completions
- Pending decisions

Update after significant events (task completion, blocker hit, decision needed).
```

### Phase 2: Presence Detection

Extend portal to detect user return:

```python
async def on_websocket_connect(ws: WebSocket, session: str):
    session_data = await get_session_data(session)

    if should_handshake(session_data):
        # Wait for grace period
        await asyncio.sleep(3.0)

        # Check if user started talking/typing
        if not session_data.recent_activity:
            await deliver_handshake(session)
```

### Phase 3: Brief Generation

Template-based brief generation from state file:

```python
def generate_brief(state: SessionState) -> str:
    parts = []

    # Status
    if state.status == "completed":
        parts.append(f"I finished {state.current_task.description}.")
    elif state.status == "blocked":
        parts.append(f"I'm blocked on {state.current_task.description}.")
    else:
        parts.append(f"I'm working on {state.current_task.description}.")

    # Blockers (priority)
    for blocker in state.blockers[:2]:
        parts.append(f"Blocked on: {blocker}.")

    # Completions (recent only)
    recent = [c for c in state.completions
              if c.when > now() - timedelta(hours=2)]
    if recent:
        parts.append(f"Recently completed: {recent[0].what}.")

    # Suggested action
    parts.append(state.suggested_action or "Ready for next steps.")

    return " ".join(parts)
```

### Phase 4: Voice Delivery

Use existing `agentwire say` with conversational pacing:

```python
async def deliver_handshake(session: str):
    state = load_session_state(session)
    brief = generate_brief(state)

    # Mark as delivered
    state.last_handshake_at = now()
    save_session_state(session, state)

    # Speak
    await say(brief, session=session)
```

### CLI Commands

```bash
# Manually trigger a handshake
agentwire handshake -s session

# View current session state
agentwire state -s session

# Clear/reset session state
agentwire state -s session --reset
```

### MCP Tools

```python
@mcp.tool()
def session_state_update(
    current_task: str | None = None,
    status: str | None = None,
    add_blocker: str | None = None,
    remove_blocker: str | None = None,
    add_completion: str | None = None,
    suggested_action: str | None = None
) -> str:
    """Update session state for handshake briefs."""
```

Workers call this as they work to keep state current.

## Configuration

```yaml
# ~/.agentwire/config.yaml
handshake:
  enabled: true

  # Minimum time away before triggering
  away_threshold_minutes: 30

  # Grace period before speaking (let user initiate first)
  grace_period_seconds: 3

  # Maximum brief length
  max_duration_seconds: 30

  # Cooldown between handshakes
  cooldown_minutes: 15

  # What to include
  include:
    blockers: true
    completions: true
    pending_decisions: true
    worker_status: true    # For orchestrators
    suggested_action: true
```

## Potential Challenges

1. **State file consistency** - Workers may not update state reliably
   - Solution: Periodic state inference from conversation history
   - Solution: Make state updates part of core worker loop, not optional

2. **Brief too long/short** - Hard to calibrate information density
   - Solution: User-configurable verbosity level
   - Solution: Learn from user behavior (do they ask follow-ups?)

3. **Stale state after crashes** - Session dies, state file reflects pre-crash
   - Solution: Include "last_activity" timestamp, warn if stale
   - Solution: Infer current state from git diff on resume

4. **Handshake interrupts urgent action** - User returns with urgent need
   - Solution: Short grace period, easy interrupt ("skip")
   - Solution: Learn patterns (this user always skips handshakes)

5. **Multi-session overwhelm** - Open 5 sessions, get 5 handshakes
   - Solution: Only handshake the focused session
   - Solution: Aggregate multi-session brief in parent orchestrator

6. **Privacy with shared devices** - Handshake reveals work to others
   - Solution: Only handshake with known voice/device
   - Solution: Configurable "private mode" that requires explicit ask

## Success Criteria

1. Zero-question resume: User can continue work without asking "where were we?"
2. Brief is under 30 seconds in 90% of cases
3. Key blockers surfaced immediately
4. Users report faster context recovery
5. Handshakes feel helpful, not annoying (suppression works)

## Future Extensions

### Handshake Styles

Different personalities for the brief:

```yaml
handshake:
  style: professional  # professional | casual | minimal | detailed
```

- **Professional**: "Status update: Login endpoint complete. Blocked on SMTP config."
- **Casual**: "Hey! Finished login, stuck waiting on that SMTP info."
- **Minimal**: "Login done. Need SMTP."
- **Detailed**: Full context with options and reasoning.

### Cross-Session Digest

Morning digest across all sessions:

```
"Good morning. Across your sessions:
 agentwire-dev: Auth done, ready for review.
 website: Blocked on copy from marketing.
 internal-tools: Tests failing since yesterday.
 Want the details on any of these?"
```

### Handshake Memory

Remember what you cared about last time:

```
[Previous session, user asked a lot about test coverage]

[Handshake]:
"Welcome back. Tests are at 85% now, up from 72%.
 Main gaps are in the email module.
 Continue?"
```

The handshake emphasizes what the user historically focuses on.

### Proactive Suggestions

Based on time of day / context:

```
[Friday 4pm handshake]:
"Before the weekend: three PRs ready for review.
 The deploy is green if you want to ship.
 Or I can clean up the TODOs - your call."
```
