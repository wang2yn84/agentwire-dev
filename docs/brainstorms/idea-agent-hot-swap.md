# Agent Hot-Swap

**One-liner:** Seamlessly switch the underlying agent (Claude Code ↔ others) mid-session without losing context or conversation history.

## Problem It Solves

Currently, an agentwire session is locked to a single agent type for its lifetime. This creates friction in several scenarios:

1. **Rate limits** - Claude hits rate limits during intense work; you want to continue with another agent but lose all context
2. **Cost optimization** - Started exploration with Claude but now doing simple edits; want cheaper GLM but can't switch
3. **Agent strengths** - Different agents excel at different tasks; can't leverage both in one workflow
4. **Outages** - If one provider goes down, work stops entirely
5. **A/B testing** - Want to compare how different agents handle the same task; requires separate sessions
6. **Capability access** - Need computer use for browser testing, then back to code editing

The voice-first philosophy makes this worse - you're speaking to "your session" but the session is really just a wrapper around one agent. Switching agents means starting over, re-explaining context, losing momentum.

## Proposed Solution

**Agent Hot-Swap** - A protocol that enables switching the backing agent while preserving conversation context and working state.

### Core Concept

```
┌─────────────────────────────────────────────────┐
│ AgentWire Session                               │
│                                                 │
│  Context Layer (persistent)                     │
│  ├─ Conversation history                        │
│  ├─ Working directory                           │
│  ├─ Active files/state                          │
│  └─ Task progress                               │
│                                                 │
│  Agent Layer (swappable)                        │
│  └─ [Claude Code] ←→ [GLM] ←→ [Custom]           │
│                                                 │
└─────────────────────────────────────────────────┘
```

### Swap Triggers

**Manual swap:**
```bash
agentwire swap -s myproject --to glm
agentwire swap -s myproject --to claude
agentwire swap -s myproject --to "custom-agent-cmd"
```

**Voice swap:**
```
[You]: "Switch to GLM"
[System]: "Switching to GLM. Context preserved."
[You]: "Continue where we left off"
[GLM]: "I see we were working on the auth middleware..."
```

**Automatic swap (rule-based):**
```yaml
# .agentwire.yml
swap_rules:
  - when: rate_limited
    to: glm
    announce: true

  - when: task_type == "simple_edit"
    to: glm-worker

  - when: cost_threshold > 5.00
    to: cheaper_model

  - when: agent_idle > 30s
    to: standby  # Pause billing
```

### Context Handoff Protocol

When swapping agents, agentwire:

1. **Captures current state:**
   ```yaml
   handoff:
     conversation_summary: |
       Working on API server authentication.
       Created auth middleware in src/middleware/auth.ts.
       Currently debugging test failure in auth.test.ts.

     working_files:
       - src/middleware/auth.ts (modified)
       - src/middleware/auth.test.ts (modified)

     pending_tasks:
       - Fix test assertion for token validation
       - Add refresh token support

     environment:
       cwd: /Users/dev/projects/api-server
       branch: feature/auth
       recent_commands: ["npm test", "git diff"]
   ```

2. **Injects into new agent:**
   ```
   [System prompt for new agent]

   You are continuing a session from another agent. Context:

   ## Conversation Summary
   {conversation_summary}

   ## Working Files
   {working_files}

   ## Pending Tasks
   {pending_tasks}

   The user will continue where they left off.
   Read the relevant files if you need more context.
   ```

3. **Announces transition:**
   ```
   [TTS]: "Switched to GLM. Conversation context preserved.
          Ready to continue with the auth middleware."
   ```

### Agent Registry

Define available agents:

```yaml
# ~/.agentwire/config.yaml
agents:
  claude:
    command: "claude --dangerously-skip-permissions"
    resume_flag: "--resume"
    context_window: 200000
    cost_tier: high
    capabilities: [code, vision, computer_use]

  glm:
    command: "glm-cli"
    context_window: 128000
    cost_tier: low
    capabilities: [code]

  cursor:
    command: "cursor-agent"
    context_window: 128000
    cost_tier: medium
    capabilities: [code, vision]
```

### Swap Modes

**1. Hot Swap (Immediate)**
- Kills current agent process
- Injects context into new agent
- Sub-second transition

**2. Warm Swap (Graceful)**
- Asks current agent to summarize state
- Waits for "good stopping point"
- New agent starts with rich context

**3. Parallel Swap (A/B)**
- Runs both agents simultaneously
- Routes same input to both
- Compare outputs side-by-side

### CLI Commands

```bash
# Basic swap
agentwire swap -s myproject --to glm

# Swap with mode
agentwire swap -s myproject --to claude --mode warm

# A/B comparison
agentwire swap -s myproject --parallel claude,glm
agentwire swap -s myproject --compare  # Show diff of responses

# Auto-swap rules
agentwire swap rules list
agentwire swap rules add "rate_limited → glm"
agentwire swap rules remove 1

# Check swap history
agentwire swap history -s myproject
```

### MCP Tools

```python
@mcp.tool()
def agent_swap(
    agent: str,  # "claude", "glm", or custom
    mode: Literal["hot", "warm", "parallel"] = "hot",
    reason: str | None = None
) -> str:
    """Switch to a different backing agent.

    Preserves conversation context and working state.
    Use 'warm' mode when current task should complete first.
    """

@mcp.tool()
def agent_status() -> str:
    """Get current agent info and swap history.

    Returns active agent, uptime, swap count, and available agents.
    """

@mcp.tool()
def agent_compare(
    prompt: str,
    agents: list[str] = ["claude", "glm"]
) -> str:
    """Send prompt to multiple agents and compare responses.

    Useful for A/B testing agent quality on specific tasks.
    """
```

### Voice Integration

Natural swap commands:

| Voice Command | Action |
|---------------|--------|
| "Switch to GLM" | Hot swap to GLM |
| "Use Claude for this" | Swap to Claude |
| "Finish this then switch" | Warm swap after task |
| "Try both agents" | Parallel comparison mode |
| "Which agent am I using?" | Report current agent |
| "Go back to previous agent" | Swap to last agent |

### Orchestrator Awareness

Orchestrators can swap worker agents based on task:

```python
# In orchestrator logic
if task.is_simple_edit:
    agentwire_pane_spawn(pane_type="claude-bypass", ...)
elif task.needs_vision:
    agentwire_pane_spawn(pane_type="claude-bypass", ...)
```

Or dynamically swap an existing worker:
```python
agentwire_agent_swap(pane=1, agent="glm")  # Cheaper for remaining work
```

## Implementation Considerations

### Context Serialization

Need portable context format that all agents can understand:

```python
@dataclass
class SwapContext:
    summary: str              # LLM-generated conversation summary
    working_dir: Path
    recent_files: list[str]   # Paths to recently touched files
    pending_tasks: list[str]  # Extracted from conversation
    environment: dict         # Branch, recent commands, etc.

    def to_prompt(self) -> str:
        """Generate system prompt injection for new agent."""

    def from_session(cls, session: Session) -> "SwapContext":
        """Extract context from current session state."""
```

### Agent Compatibility

Not all agents support the same features:

```python
def can_swap(from_agent: str, to_agent: str, context: SwapContext) -> bool:
    to_caps = AGENTS[to_agent].capabilities

    # Check if context requires capabilities target doesn't have
    if context.needs_vision and "vision" not in to_caps:
        return False
    if context.uses_computer and "computer_use" not in to_caps:
        return False

    return True
```

Warn user when swapping loses capabilities:
```
[TTS]: "Warning: GLM doesn't support browser control.
        You'll need Claude for that part."
```

### State Preservation

Some state can't be transferred:
- In-memory agent state (lost on swap)
- Tool call history (agent-specific format)
- Streaming responses in progress

Mitigate by:
- Swap at natural breakpoints
- Save important state to files
- Use warm swap mode when precision matters

### Resume vs Restart

When swapping TO an agent that supports resume:
```bash
# Claude: Resume existing conversation ID
claude --resume conv_abc123

# Claude Code: Resume session
claude --resume conv_abc123
```

When resume not available:
- Inject full context as system prompt
- Rely on summary to reconstruct understanding

## Potential Challenges

1. **Context Loss**
   - LLM summaries may miss nuances
   - Agent-specific understanding doesn't transfer
   - **Mitigation:** Rich context extraction, file-based state persistence

2. **Capability Mismatch**
   - Started task with vision, swapped to non-vision agent
   - **Mitigation:** Capability checks, user warnings, swap rules

3. **Response Style Differences**
   - Different agents have different "personalities"
   - User may find transitions jarring
   - **Mitigation:** Normalize output formatting, announce transitions

4. **Rate Limit Ping-Pong**
   - Swap to avoid rate limit, new agent also hits limit
   - **Mitigation:** Cooldown periods, rate tracking across agents

5. **Cost Tracking Complexity**
   - Session now spans multiple billing sources
   - **Mitigation:** Unified cost tracking layer, per-agent attribution

6. **Parallel Mode Costs**
   - A/B comparison doubles token usage
   - **Mitigation:** Only run in parallel when explicitly requested, time-limit

## Success Metrics

- Swap latency < 2 seconds for hot swap
- Context preservation rated >4/5 by users
- Zero failed swaps due to compatibility issues (caught preemptively)
- Rate limit recovery time drops from "start new session" to < 5 seconds
- Users report continuity across swaps (doesn't feel like new conversation)

## Future Extensions

- **Ensemble Mode** - Multiple agents collaborate on same task
- **Agent Routing** - Automatically route requests to best agent for query type
- **Cross-Provider Federation** - Swap to agents running on different machines
- **Fine-Tuned Swap** - Train models specifically for context handoff
- **Swap Replay** - Re-run conversation prefix on new agent to build shared understanding
