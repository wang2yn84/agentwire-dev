# Smart Session Routing

> Voice commands auto-route to the right session based on context and intent.

## Problem

With multiple sessions running, you constantly specify which one to talk to:

```
"Tell website fix the nav links"
"Tell api-server add rate limiting"
"Check on the docs session"
```

This is tedious and breaks flow. You know what you want done - the system should know where to send it.

Worse, you sometimes forget session names. "What was that session called? The one working on auth?" Then you list sessions, find the name, and finally issue the command. Three steps when it should be one.

## Proposed Solution

**Context-aware routing** - the system infers which session should handle a command based on:

1. What each session is currently working on
2. The semantic content of your command
3. Recent interaction history
4. Project/file associations

### How It Works

```
[User]: "Fix the login redirect bug"

[System internally]:
  - Session "website": working on nav components, last touched Header.tsx
  - Session "auth-api": working on JWT validation, last touched auth/login.ts
  - Session "docs": writing API documentation

  → Route to "auth-api" (highest relevance to "login")

[System]: "Sending to auth-api"
```

### Routing Signals

| Signal | Weight | Example |
|--------|--------|---------|
| Recent file edits | High | Session touched `auth/login.ts` → matches "login" |
| Active task context | High | Task description mentions "authentication" |
| Session name | Medium | "api-server" matches "API rate limiting" |
| Project keywords | Medium | `.agentwire.yml` tags: `["auth", "security"]` |
| Recent commands | Low | You just talked to this session |

### Explicit Override

When routing is wrong or ambiguous:

```
[User]: "Fix the login page"
[System]: "Did you mean auth-api or website? Both have login-related files."
[User]: "Website"
[System]: "Got it. Sending to website."
```

Or explicit targeting still works:

```
[User]: "Tell website fix the login page"
[System]: "Sending to website" (explicit override)
```

### Configuration

```yaml
# ~/.agentwire/config.yaml
routing:
  enabled: true
  confidence_threshold: 0.7  # Below this, ask for clarification
  prefer_recent: true        # Bias toward recently active sessions
  learning: true             # Improve routing from corrections

# Project-level routing hints
# .agentwire.yml
keywords:
  - auth
  - security
  - login
  - JWT
```

### Context Building

Each session maintains a context vector:

```python
class SessionContext:
    recent_files: list[str]      # Files touched in last N minutes
    task_summary: str            # Current task description
    keywords: set[str]           # Extracted from activity + config
    last_interaction: datetime   # When user last spoke to this session
    project_type: str            # "web", "api", "cli", "docs", etc.
```

Updated continuously from:
- File system events (via worker summaries)
- Task descriptions
- Voice commands sent to the session
- Project configuration

### Routing Algorithm

```python
def route_command(command: str, sessions: list[Session]) -> Session | list[Session]:
    scores = {}

    for session in sessions:
        # Semantic similarity
        semantic = embed(command).similarity(session.context_embedding)

        # Keyword matching
        keywords = extract_keywords(command)
        keyword_match = len(keywords & session.keywords) / len(keywords)

        # Recency bonus
        recency = time_decay(session.last_interaction)

        scores[session] = (
            0.5 * semantic +
            0.3 * keyword_match +
            0.2 * recency
        )

    top_score = max(scores.values())

    if top_score < CONFIDENCE_THRESHOLD:
        # Return top candidates for clarification
        return [s for s, score in scores.items() if score > top_score * 0.8]

    return max(scores, key=scores.get)
```

### Learning from Corrections

When the user overrides routing, learn from it:

```
[System]: "Sending to auth-api"
[User]: "No, website"
[System]: "Got it. I'll remember 'login page' relates to website."
```

Store association: `"login page" → website` with decay over time.

## Implementation Considerations

### Embedding Model

For semantic matching, need a lightweight embedding model:
- **Option A**: Use STT's backend (already running)
- **Option B**: Small local model (e.g., sentence-transformers)
- **Option C**: API call to embedding service (adds latency)

Recommend Option B - run once at routing time, ~50ms overhead.

### Context Freshness

Context must stay current:
- Update on file changes (from worker summaries)
- Update on voice commands
- Decay old context (files touched 2 hours ago matter less)
- Refresh on session focus

### Multi-Session Broadcasts

Some commands apply to all sessions:

```
[User]: "Status check"
[System]: "Website: idle. Auth-api: working on tests. Docs: writing endpoints section."
```

Detect broadcast intent from command phrasing ("all sessions", "everyone", "status").

### Disambiguation UI

When clarification needed, make it fast:

```
[System via TTS]: "Website or auth-api?"
[Portal UI]: Shows two large buttons, touch-friendly
[User]: Taps "website" OR says "website"
```

Sub-second resolution for common ambiguities.

### Session Aliasing

Users can add memorable aliases:

```bash
agentwire alias auth-api "the auth one"
agentwire alias website "frontend"
```

Then: "Tell the auth one to check tokens" routes correctly.

## Potential Challenges

1. **Semantic ambiguity**: "Fix the button" - which button? Many sessions have buttons. Solution: Require more specific phrasing when confidence is low.

2. **Context staleness**: Session was working on auth yesterday, now working on profiles. Solution: Aggressive time decay, prioritize recent activity.

3. **New sessions**: Fresh session has no context. Solution: Use project config keywords, session name matching, or ask explicitly.

4. **Embedding latency**: Can't add 500ms to every voice command. Solution: Pre-compute session embeddings, only compute command embedding on-demand.

5. **Learning pollution**: Bad corrections degrade routing. Solution: Decay learned associations, require multiple corrections to establish pattern.

6. **Privacy of context**: Session context might contain sensitive info. Solution: Keep context local, embeddings are lossy by nature.

## Example Scenarios

### Scenario 1: Clear Routing

```
[User]: "Add rate limiting to the API endpoints"
[System]: "Sending to api-server" (only session with API code)
```

### Scenario 2: Ambiguous, Clarified

```
[User]: "Fix the header"
[System]: "Website has Header.tsx, docs has header.md. Which one?"
[User]: "Website"
[System]: "Sending to website"
```

### Scenario 3: Learned Preference

```
# After several corrections...
[User]: "Update the landing page"
[System]: "Sending to website" (learned: "landing page" → website)
```

### Scenario 4: Broadcast

```
[User]: "Everyone stop and check in"
[System]: "Requesting status from all 3 sessions..."
```

## Success Metrics

- Reduced explicit session targeting (fewer "tell X" commands)
- First-try routing accuracy >85%
- Clarification conversations resolved in one turn
- User satisfaction: "it just knows where to send things"

## Related Ideas

- Combines well with voice-macros (macros could include routing hints)
- Session-templates could define default routing keywords
- Voice-transcript-logs could power the learning algorithm
