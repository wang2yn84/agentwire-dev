# Delegation Replay & What-If Analysis

**Record delegation decisions and their outcomes, then replay with different parameters to discover optimal orchestration patterns.**

## Problem

Orchestrators make delegation decisions in the dark. When you spawn 2 GLM workers for a feature, you have no way to know if 3 workers would have been faster, if Claude workers would have produced fewer errors, or if handling part of it directly would have saved a retry cycle. Every delegation is a blind bet with no feedback loop on the decision itself - only on the final output.

Over time, orchestrators repeat the same suboptimal patterns: over-splitting simple tasks, under-splitting complex ones, sending vague prompts that cause retries, or delegating work that would have been faster to do directly. There's no mechanism to learn from these patterns.

## Proposed Solution

### 1. Delegation Event Log

Record every delegation decision as a structured event:

```yaml
# ~/.agentwire/delegation-log/{session}/{timestamp}.yaml
event: delegation
timestamp: "2026-02-08T14:23:00Z"
session: my-project
task_description: "Implement auth middleware + update 4 route files"
decision:
  strategy: parallel-workers
  worker_count: 2
  worker_type: opencode-bypass
  instructions:
    - pane: 1
      prompt: "Implement auth middleware in src/middleware/auth.ts..."
      files_targeted: ["src/middleware/auth.ts", "src/types/auth.ts"]
    - pane: 2
      prompt: "Update route files to use auth middleware..."
      files_targeted: ["src/routes/users.ts", "src/routes/posts.ts", "src/routes/admin.ts", "src/routes/settings.ts"]
outcome:
  wall_time_seconds: 180
  worker_results:
    - pane: 1
      status: done
      duration_seconds: 95
      files_changed: 2
      retries: 0
    - pane: 2
      status: done
      duration_seconds: 170
      files_changed: 4
      retries: 1
      retry_reason: "Missed import in admin.ts"
  qa_passed: true
  total_cost_estimate: "$0.42"
```

### 2. Pattern Analysis CLI

Analyze delegation history to surface insights:

```bash
# Show delegation stats for a project
agentwire delegation stats -s my-project

# Output:
# Delegations: 47 total, 38 succeeded first try (81%)
# Avg workers per task: 2.1
# Avg wall time: 142s (direct estimate: 95s for simple, 300s+ for complex)
# Top retry reasons:
#   - Missing imports (12x) → consider adding "check imports" to prompts
#   - File conflicts (5x) → consider sequential for shared files
#   - Vague instructions (4x) → prompts under 50 words fail 3x more

# Show tasks that would have been faster done directly
agentwire delegation review --inefficient

# Output:
# 8 delegations took longer than estimated direct time:
#   - "Fix typo in README" → spawned worker, 45s. Direct: ~5s
#   - "Add one CSS class" → spawned worker, 60s. Direct: ~10s
#   Recommendation: tasks touching ≤1 file with ≤10 line changes → do directly
```

### 3. What-If Replay

Replay past delegations with modified parameters to estimate alternate outcomes:

```bash
# What if we used 3 workers instead of 2?
agentwire delegation whatif <event-id> --workers 3

# Analysis:
# Original: 2 workers, 170s wall time
# What-if: 3 workers
#   - Worker 2 had 4 route files. Split to 2+2 would likely save ~40s
#   - But spawn overhead adds ~15s
#   - Estimated: ~145s wall time (15% faster)
#   - File conflict risk: LOW (independent files)

# What if we used Claude instead of GLM?
agentwire delegation whatif <event-id> --worker-type claude-bypass

# Analysis:
# GLM retry rate for this task type: 1/2 workers
# Claude retry rate for similar tasks: 0.1/worker (historical)
# Estimated savings: ~30s from avoided retry
# Additional cost: ~$0.35 more
```

### 4. Delegation Advisor (Real-Time)

Before spawning workers, optionally consult the advisor:

```bash
# In orchestrator prompt or as MCP tool
agentwire delegation advise "Implement auth middleware + update 4 route files"

# Based on 47 past delegations:
# Recommended: 2 workers (parallel)
#   Worker 1: middleware + types (historically self-contained)
#   Worker 2: route files (group by dependency)
# Worker type: opencode-bypass (sufficient complexity, cost-effective)
# Prompt tips:
#   - Include "verify imports after changes" (prevents top retry cause)
#   - List exact file paths (reduces ambiguity retries by 60%)
# Confidence: 78% first-try success
```

This could also be exposed as an MCP tool so orchestrators can query it mid-conversation:

```
agentwire_delegation_advise(task="Refactor the payment module into 3 services")
```

## Implementation Considerations

**Storage**: YAML files in `~/.agentwire/delegation-log/` organized by session. Lightweight, git-friendly, easy to inspect manually. Rotate logs older than 30 days.

**Cost estimation**: Use token counts from worker summaries (Claude) or approximate from output length (GLM). Doesn't need to be exact - relative comparisons are what matter.

**What-if engine**: Start simple with heuristic rules derived from the log data (avg time per file changed, retry probability by task type, spawn overhead constants). Can evolve to use LLM analysis for more nuanced estimates.

**Advisor integration**: The real-time advisor should be opt-in and fast (<2s response). It reads the local log, applies pattern matching, and returns a recommendation. No external API calls needed.

**Privacy**: All data stays local. Delegation logs contain task descriptions and file paths but not file contents or full conversation history.

## Potential Challenges

**Cold start**: The advisor is useless without history. Need 20-30 delegations before patterns emerge. Could seed with general best practices ("tasks under 10 lines → do directly") and refine with real data.

**Task similarity matching**: "What-if" analysis assumes past tasks predict future ones. Need good heuristics for matching task types - file count, description keywords, complexity signals. Exact matching won't work; fuzzy categorization is needed.

**Overhead vs value**: Logging every delegation adds I/O. Keep it append-only and async so it never blocks the orchestrator. The analysis commands are offline - run them between sessions, not during active work.

**Changing codebase dynamics**: What worked for a simple project may not apply as complexity grows. The advisor should weight recent delegations more heavily and decay old data. A sliding window of the last 100 delegations per project is probably sufficient.

**Worker capability drift**: Model updates change what GLM vs Claude can handle. Historical retry rates may not reflect current capabilities. Flag when a model version changes and reset confidence scores for that worker type.
