---
name: openai-delegation
description: Guide for delegating tasks to ChatGPT/OpenAI workers via OpenCode
model: inherit
---

# OpenAI/ChatGPT Task Delegation

**This role defines your worker type: ChatGPT workers via OpenCode ONLY.**

**ChatGPT 5.1 is a conversational collaborator with efficient reasoning.** It has adaptive reasoning that saves tokens on simple tasks while remaining persistent on complex ones. It excels at following instructions, parallel tool calling, and has native `apply_patch` and `shell` tools for reliable code editing.

This role supplements `leader` with OpenAI-specific spawn patterns, task templates, and communication techniques.

---

## Quick Reference

For detailed exit summary format, see `worker` role. Delegation roles focus on task communication, not summary format.

### Spawn Command (ALWAYS use this exact pattern)
```
agentwire_pane_spawn(pane_type="opencode-bypass", roles="openai-worker")
```
**Never omit `pane_type` - it defaults to wrong agent/permissions.**

### Task Pattern (goal-oriented)
```
agentwire_pane_send(pane=1, message="Add JWT authentication to the API.

Goal: Secure the API with token-based auth
Files: Focus on /src/auth/ directory
Constraints: Use existing User model, don't modify database schema

Success: Login endpoint returns JWT, protected routes reject invalid tokens")
```

### Verify Before Sending
- [ ] Goal is clear and specific
- [ ] Relevant files/directories mentioned
- [ ] Constraints are explicit
- [ ] Success criteria are testable

### Check Completion

Workers write exit summaries. Check for:
```
─── DONE ───      (success)
─── BLOCKED ───   (needs help)
─── ERROR ───     (failed)
```

### API Concurrency Limits

**OpenAI has no hard concurrency limit** - spawn as many workers as you need.

| Workers | Quality | Recommendation |
|---------|---------|----------------|
| 1-2 | Best | Complex multi-step tasks |
| 3-5 | Good | Parallel independent tasks |
| 6+ | Variable | Depends on task complexity |

ChatGPT's adaptive reasoning makes it efficient even with multiple workers.

---

## Core Philosophy

### ChatGPT vs Claude vs GLM

| ChatGPT 5.1 | Claude | GLM |
|-------------|--------|-----|
| Goal-oriented, instruction-following | Infers intent from context | Executes literally |
| Adaptive reasoning (token-efficient) | Deep reasoning always | Consistent reasoning |
| Excellent tool calling | Good tool use | Good tool use |
| Clear success criteria help | Handles ambiguity | Fails on ambiguity |

### Treat ChatGPT Like a Capable Engineer

- Describe goals and success criteria clearly
- Mention relevant files/directories
- State constraints explicitly
- Let it figure out the implementation details

### Workers Are Efficient

ChatGPT's adaptive reasoning means:
- Simple tasks complete quickly with minimal token usage
- Complex tasks get the reasoning they need
- You can spawn more workers without cost explosion

---

## Task Communication

### Goal-Oriented Instructions

ChatGPT responds well to goal-oriented tasks with clear success criteria.

**Template:**
```
[Brief description of task]

Goal: [What needs to be accomplished]
Files: [Relevant files or directories]
Constraints: [What NOT to do, requirements]

Success: [Testable outcome]
```

**Example:**
```
agentwire_pane_send(pane=1, message="Implement user profile page.

Goal: Create a profile page showing user info with edit capability
Files: /src/pages/profile/, /src/components/user/
Constraints: Use existing UserContext, don't create new API endpoints

Success: Profile page loads user data, edit form saves changes")
```

### When to Be More Explicit

Be more explicit about:

1. **File locations:** ChatGPT benefits from knowing where to look
2. **Existing patterns:** Point to similar implementations
3. **Non-negotiables:** What must not change
4. **Success criteria:** How to know the task is done

### ChatGPT vs Claude vs GLM Examples

**ChatGPT (goal-oriented):**
```
Add error handling to the checkout flow.

Goal: Graceful error handling for payment failures
Files: /src/pages/checkout.tsx, /src/lib/payments.ts
Constraints: Use existing toast system, don't change API contract

Success: Payment errors show user-friendly message, form stays populated
```

**Claude (collaborative):**
```
Add error handling to the checkout flow.
We need to handle payment failures gracefully - show a toast and keep the form populated.
Check the existing error handling in /src/lib/api.ts for patterns.
```

**GLM (explicit steps):**
```
TASK: Add error handling to checkout
FILES: /src/pages/checkout.tsx
STEPS:
1. Read /src/pages/checkout.tsx
2. Wrap payment call in try/catch
3. On error, call showToast with error message
4. Keep form state on failure
```

---

## Task Decomposition

### Parallel Tasks (Spawn All At Once)

```
# These can run in parallel - no dependencies
agentwire_pane_spawn(pane_type="opencode-bypass", roles="openai-worker")  # pane 1
agentwire_pane_spawn(pane_type="opencode-bypass", roles="openai-worker")  # pane 2
agentwire_pane_spawn(pane_type="opencode-bypass", roles="openai-worker")  # pane 3

agentwire_pane_send(pane=1, message="Add input validation to registration form...")
agentwire_pane_send(pane=2, message="Create unit tests for auth module...")
agentwire_pane_send(pane=3, message="Add TypeScript types for API responses...")
```

### Sequential Tasks (Wait Between)

```
# Worker 1 creates the foundation
agentwire_pane_spawn(pane_type="opencode-bypass", roles="openai-worker")
agentwire_pane_send(pane=1, message="Create base Button component with variants...")

# Wait for completion, then spawn dependent task
agentwire_pane_spawn(pane_type="opencode-bypass", roles="openai-worker")
agentwire_pane_send(pane=2, message="Use the Button component in all form pages...")
```

---

## Common Patterns

### Feature Implementation

```
agentwire_pane_send(pane=1, message="Implement dark mode toggle.

Goal: Add theme switching between light and dark modes
Files: /src/components/settings/, /src/styles/
Constraints: Use CSS variables, persist to localStorage

Success: Toggle switches theme immediately, preference persists across sessions")
```

### Refactoring

```
agentwire_pane_send(pane=1, message="Refactor auth module into smaller files.

Goal: Split monolithic auth.ts into logical modules
Files: /src/lib/auth.ts → /src/lib/auth/
Constraints: Maintain all exports from index.ts, no breaking changes

Success: Code split into jwt.ts, session.ts, password.ts with clean imports")
```

### Bug Fixes

```
agentwire_pane_send(pane=1, message="Fix cart persistence bug.

Goal: Cart items should survive page refresh
Files: /src/store/cart.ts
Constraints: Use existing localStorage helpers

Success: Add items, refresh page, items still in cart")
```

### Testing

```
agentwire_pane_send(pane=1, message="Add integration tests for checkout.

Goal: Full test coverage for checkout flow
Files: /tests/checkout/
Constraints: Use existing test fixtures, mock payment API

Success: Tests cover happy path, empty cart, payment failure, stock issues")
```

---

## Worker Tracking

### Maintain a Task Map

| Pane | Task | Status |
|------|------|--------|
| 1 | "Auth endpoints" | In progress |
| 2 | "Unit tests" | In progress |
| 3 | "Type definitions" | In progress |

### Check Completion

Workers write exit summaries. Check for:

```bash
cat .agentwire/ses_*.md
```

Look for:
- **Status:** ── DONE ── → proceed, ── BLOCKED ── / ── ERROR ── → address issues
- **Files Changed:** Review what was modified
- **What Didn't Work:** Issues to fix

---

## Failure Patterns & Recovery

### Unclear Success Criteria

**Symptom:** Worker finishes but output doesn't match expectations

**Fix:** Re-send with explicit success criteria:
```
agentwire_pane_send(pane=1, message="The auth implementation needs adjustment.

Goal: JWT tokens should expire after 1 hour, refresh tokens after 7 days
Constraints: Don't change the token format, just the expiry

Success: Login returns both tokens, refresh endpoint works, expired tokens rejected")
```

### Missing Context

**Symptom:** Worker uses wrong patterns or creates incompatible code

**Fix:** Point to existing examples:
```
agentwire_pane_send(pane=1, message="Please follow the existing patterns.
Check /src/components/Button.tsx for component structure.
Check /src/lib/api.ts for API call patterns.
Use the same error handling approach.")
```

### Scope Creep

**Symptom:** Worker changes more than requested

**Fix:** Add explicit constraints:
```
agentwire_pane_send(pane=1, message="Please focus only on the specified files.
Constraints:
- ONLY modify files in /src/auth/
- Don't touch the database schema
- Don't change API contracts")
```

### Recovery Patterns

**Worker Failed or Blocked:**

```bash
# Read summary to understand what went wrong
cat .agentwire/ses_*.md

# Spawn new worker with clarified task
agentwire_pane_spawn(pane_type="opencode-bypass", roles="openai-worker")
agentwire_pane_send(pane=2, message="[Clarified task based on failure]")
```

**Code Is Wrong:**

```bash
git diff  # Review changes
git checkout -- /path/to/file.tsx  # Revert if needed
# Spawn worker with better instructions
```

---

## Success Patterns

### Clear Goals + Success Criteria

```
# Good - goal-oriented with testable success
agentwire_pane_send(pane=1, message="Optimize dashboard queries.

Goal: Reduce dashboard load time from 500ms to under 100ms
Files: /src/api/dashboard.ts, /src/lib/db.ts
Constraints: Don't change the response format

Success: Dashboard loads in <100ms, all existing functionality works")

# Bad - vague, no success criteria
agentwire_pane_send(pane=1, message="Make the dashboard faster")
```

### Leverage Adaptive Reasoning

ChatGPT's adaptive reasoning means simple tasks don't burn tokens:

```
# Simple task - ChatGPT handles quickly
agentwire_pane_send(pane=1, message="Add TypeScript types for the User model.
File: /src/types/user.ts
Success: Types match the database schema in /prisma/schema.prisma")

# Complex task - ChatGPT takes time to reason
agentwire_pane_send(pane=1, message="Implement rate limiting with sliding window.
Goal: Prevent API abuse without blocking legitimate users
Files: /src/middleware/rateLimit.ts
Constraints: Use Redis, support per-user and per-IP limits
Success: Rate limits work per spec, tests pass, no false positives")
```

### Success Checklist

Before reporting completion to main orchestrator:

- [ ] All workers output exit summary (─── DONE ───)
- [ ] `npm run build` passes (or equivalent)
- [ ] Chrome screenshot looks correct (web projects)
- [ ] No console errors
- [ ] Interactive elements work
- [ ] Edge cases handled
- [ ] Git committed

Only then:
```
agentwire_say(text="Feature complete, tested in Chrome")
```

---

## Remember

**ChatGPT workers are efficient goal-achievers.**

Your job:
1. Describe goals clearly with success criteria
2. Point to relevant files and patterns
3. State constraints explicitly
4. Let adaptive reasoning handle the complexity

The quality of output = clarity of goal + relevance of context + explicitness of constraints.

Vague goals → wasted reasoning tokens → poor results

Clear goals, good context → efficient execution → fast iteration

---

## Sources

- [Introducing GPT-5.1 for developers | OpenAI](https://openai.com/index/gpt-5-1-for-developers/)
- [GPT-5.1: A smarter, more conversational ChatGPT | OpenAI](https://openai.com/index/gpt-5-1/)
