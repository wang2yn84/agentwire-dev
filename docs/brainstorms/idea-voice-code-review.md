# Voice-Driven Code Review

> Interactive voice walkthrough of worker diffs so orchestrators can review, question, and approve changes without leaving the voice workflow.

## Problem

After workers complete tasks, orchestrators read summary files to understand what happened. But these summaries have blind spots:

1. **Worker-authored bias** - Workers describe what they *intended*, not necessarily what they *actually changed*. A summary says "added rate limiting middleware" but doesn't mention the silent refactor of the error handler.
2. **No diff visibility** - Summaries list files changed but not the actual code. The orchestrator trusts the summary or drops into a terminal to `git diff`, breaking the voice-first flow.
3. **Multi-worker review debt** - Three workers finish in parallel. That's three summaries to read, potentially touching overlapping files. There's no consolidated view of what changed and no way to catch conflicts before they merge.
4. **Silent regressions** - Worker deletes a utility function it thinks is unused. Worker changes an interface signature. These are invisible in summaries but critical in review.

Currently the choice is: trust blindly (fast, risky) or read diffs manually (slow, breaks flow). There's no middle ground.

## Proposed Solution

A **voice-guided code review mode** that analyzes worker diffs, ranks changes by importance, and walks the orchestrator through them conversationally.

### Core Flow

```
[Worker 1 completes, summary received]

Orchestrator: "Review worker 1's changes"

[System analyzes git diff for worker 1's pane]

System (voice): "Worker 1 touched 4 files. Two notable changes:
  First - they added a new middleware in api/ratelimit.ts, about 40 lines.
  Second - they modified the error handler in api/errors.ts, changing the
  response format from plain text to JSON.
  Want me to walk through either one?"

Orchestrator: "Tell me about the error handler change"

System (voice): "They changed the catch block to return a JSON object with
  error code and message instead of a plain string. This affects all 12
  endpoints that use this handler. The tests were updated to match."

Orchestrator: "Looks fine. Any concerns?"

System (voice): "One thing - the rate limit middleware uses an in-memory
  store. That won't survive restarts and won't work across multiple
  instances. Might want Redis backing."

Orchestrator: "Good catch. Spawn a worker to fix that."
```

### Review Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| **Quick** | `"Review worker 1"` | Voice summary of notable changes, skip trivial diffs |
| **Thorough** | `"Review worker 1 thoroughly"` | Walk through every file change |
| **Focused** | `"Review worker 1's changes to auth/"` | Filter to specific paths |
| **Consolidated** | `"Review all worker changes"` | Merge all worker diffs, flag conflicts |

### Change Ranking

Not all diffs are equal. The system ranks changes by risk/importance:

| Signal | Risk Level | Example |
|--------|------------|---------|
| Interface/type changes | High | Changed function signature, modified exported types |
| Deleted code | High | Removed functions, deleted files |
| New dependencies | Medium | Added imports, new packages |
| Logic changes | Medium | Modified conditionals, changed algorithms |
| New files | Low-Medium | Added new modules (depends on size) |
| Formatting/comments | Low | Whitespace, renamed variables |

High-risk changes are always mentioned in voice review. Low-risk changes are skipped in Quick mode.

### CLI Integration

```bash
# Generate review for a worker's changes
agentwire review --pane 1

# Review all workers in current session
agentwire review --all

# Review specific paths
agentwire review --pane 1 --path "src/api/"

# Output formats
agentwire review --pane 1 --json     # Structured for agents
agentwire review --pane 1 --voice    # Trigger voice walkthrough
```

### MCP Tool

```python
agentwire_review(pane=1)
# Returns: structured review with ranked changes

agentwire_review(pane=1, mode="thorough", path="src/api/")
# Returns: detailed review filtered to path
```

### Review Data Structure

```python
@dataclass
class FileReview:
    path: str
    status: str              # added, modified, deleted, renamed
    risk_level: str          # high, medium, low
    summary: str             # Human-readable change description
    lines_added: int
    lines_removed: int
    concerns: list[str]      # Potential issues detected
    related_files: list[str] # Files that depend on this one

@dataclass
class WorkerReview:
    pane: int
    task_summary: str
    files: list[FileReview]
    conflicts: list[str]     # Conflicts with other workers
    overall_risk: str
    voice_script: str        # Pre-generated voice walkthrough
```

### Conflict Detection (Multi-Worker)

When reviewing consolidated changes across workers:

```
[System]: "Workers 1 and 2 both modified api/middleware.ts.
  Worker 1 added rate limiting at line 45.
  Worker 2 added CORS handling at line 42.
  These changes are in the same block and may conflict.
  Want me to show both changes?"
```

Detection works by:
1. Collecting diffs from all worker panes
2. Checking for overlapping file paths
3. Within shared files, checking for overlapping line ranges
4. Flagging semantic conflicts (e.g., one worker adds a field, another removes it)

### Voice Interaction Patterns

**Drill down:**
```
"Tell me more about the changes to auth.ts"
"What exactly did they change in the login function?"
"Read me the new rate limit config"
```

**Quick actions:**
```
"Approve worker 1's changes"        → git add + mark reviewed
"Revert worker 2's error handler"   → git checkout specific file
"Spawn a worker to fix the Redis issue"  → new worker with context
```

**Comparisons:**
```
"How does worker 1's approach differ from worker 2's?"
"Did any workers duplicate work?"
```

## Implementation Considerations

### Diff Analysis Engine

The review system needs to understand code, not just text diffs. Two approaches:

**Option A: LLM-powered analysis** - Send the diff to a fast model (Haiku) for summarization and risk assessment. Pros: semantic understanding, catches subtle issues. Cons: token cost per review, latency.

**Option B: Heuristic + AST** - Parse diffs with tree-sitter for structural analysis, use regex patterns for risk signals (deleted exports, changed signatures). Pros: fast, free, deterministic. Cons: misses semantic issues.

**Recommendation: Hybrid.** Use heuristics for ranking and conflict detection (fast, cheap), LLM for voice script generation and concern identification (smart, worth the cost for review quality).

### Git Integration

Workers share the orchestrator's working directory. To isolate worker changes for review:

1. **Worktree-based workers** - Each worker on a branch. `git diff main..worker-branch` gives clean diff. Cleanest but requires worktree setup.
2. **Timestamp-based** - Track which files changed during the worker's active period. Less precise but works without worktrees.
3. **Summary-based** - Worker's summary lists files changed. Cross-reference with `git diff` for those specific files. Pragmatic middle ground.

Option 3 is most compatible with current architecture.

### Voice Script Generation

Pre-generate the voice walkthrough so `agentwire_say` calls are fast:

```python
def generate_voice_script(review: WorkerReview) -> str:
    """Generate natural voice walkthrough from structured review."""
    parts = []

    # Overview
    file_count = len(review.files)
    high_risk = [f for f in review.files if f.risk_level == "high"]
    parts.append(f"Worker {review.pane} touched {file_count} files.")

    if high_risk:
        parts.append(f"{len(high_risk)} changes worth attention:")
        for f in high_risk:
            parts.append(f"{f.path}: {f.summary}")

    if review.conflicts:
        parts.append(f"Also found {len(review.conflicts)} potential conflicts.")

    return " ".join(parts)
```

### Review State Tracking

Track which changes have been reviewed:

```yaml
# .agentwire/reviews/
worker-1-review.yaml:
  status: reviewed       # pending, reviewing, reviewed, approved
  reviewed_at: 2024-01-15T10:30:00
  concerns_addressed: [0, 1]  # Index into concerns list
  follow_up_worker: 3         # Spawned to fix issue
```

This prevents re-reviewing the same changes and tracks the approval chain.

## Potential Challenges

1. **Large diffs overwhelm voice** - A worker that touches 20 files with 500 lines changed can't be meaningfully reviewed via voice alone. Solution: aggressive ranking. Voice covers the top 3-5 changes, offer "see full diff in portal" for the rest.

2. **Review fatigue** - If every worker completion triggers a review, orchestrators will start skipping them. Solution: auto-approve low-risk changes (formatting, comments, test-only), only prompt for medium/high-risk. Configurable threshold.

3. **Stale diffs** - By the time review happens, another worker may have changed the same files. Solution: re-analyze diff at review time, not at completion time. Flag if files have been modified since worker finished.

4. **LLM hallucination in analysis** - The analysis model might invent concerns that don't exist. Solution: ground analysis strictly in the actual diff text. Include line numbers so orchestrator can verify.

5. **Token cost at scale** - Reviewing every worker's diff through an LLM adds up. Solution: tiered approach. Heuristic analysis is free and always runs. LLM analysis only for high-risk changes or when explicitly requested.

6. **Cross-language complexity** - Workers might change Python, TypeScript, and YAML in the same task. The review engine needs to understand risk signals across languages. Solution: language-specific heuristic plugins with a shared risk framework.

## Success Metrics

- Catch rate: % of worker issues found during voice review vs. discovered later
- Review time: average time from worker completion to orchestrator approval
- False concern rate: % of flagged concerns that turn out to be non-issues
- Adoption: % of worker completions that trigger a review (should stabilize around 60-80%, not 100%)
