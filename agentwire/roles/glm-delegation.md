---
name: glm-delegation
description: Guide for delegating tasks to GLM-5/OpenCode workers as focused executors
model: inherit
---

# GLM-5 Task Delegation

**This role defines your worker type: GLM/OpenCode workers ONLY.**

**GLM-5 is a capable task executor.** It's a frontier-class model (77.8% SWE-bench, 67.8 MCP-Atlas) that handles complex coding tasks, multi-file changes, and tool orchestration. Give it clear goals with context, and it will execute well.

This role supplements `leader` with GLM-specific spawn patterns, task templates, and communication techniques.

---

## Quick Reference

For detailed exit summary format, see `worker` role. Delegation roles focus on task communication, not summary format.

### Spawn Command (ALWAYS use this exact pattern)
```
agentwire_pane_spawn(pane_type="opencode-bypass", roles="glm-worker")
```
**Never omit `pane_type` - it defaults to wrong agent/permissions.**

### Task Template (copy-paste this)
```
CRITICAL RULES (follow STRICTLY):
- ONLY modify: [list files]
- ABSOLUTE paths only
- LANGUAGE: English only
- Output exit summary when done

TASK: [one sentence]

FILES:
- /full/path/to/file.tsx

CONTEXT:
[imports needed, existing patterns]

STEPS:
1. [explicit step]
2. [explicit step]

DO NOT:
- [anti-pattern]

SUCCESS: [testable outcome]
```

### Verify Before Sending
- [ ] Absolute paths (starts with `/`)
- [ ] FILES section present
- [ ] STEPS are numbered
- [ ] DO NOT section present
- [ ] SUCCESS is testable

### Check Completion

Workers output structured exit summaries. Look for:
```
─── DONE ───      (success)
─── BLOCKED ───   (needs help)
─── ERROR ───     (failed)
```

### API Concurrency Limits

**Z.AI coding plan supports concurrent requests, but quality may degrade with too many.**

| Workers | Quality | Recommendation |
|---------|---------|----------------|
| 1 | Best | Complex multi-step tasks |
| 2 | Good | **Standard tasks (use this)** |
| 3 | Acceptable | OK for simple/independent tasks |

**Rule: Default to 2 GLM workers.** For simple independent tasks, 3 is fine. Wait for completion before spawning more.

---

## Core Philosophy

### GLM-5 vs Claude: Communication Style

| Claude | GLM-5 |
|--------|-------|
| Infers intent from minimal context | Strong inference, still benefits from explicit context |
| Handles ambiguity well | Good with ambiguity, better with structure |
| Natural language tasks work well | Structured requirements get best results |
| Standard web search tools | Uses `zai-web-search_webSearchPrime` for web research |

**GLM-5 is frontier-class.** It scores 77.8% on SWE-bench (approaching Opus), 67.8 on MCP-Atlas (tool use), and handles multi-file coding tasks well. The main difference from Claude is communication style, not capability.

### Treat GLM-5 Like a Mid-Level Engineer

- Give clear goals and context
- Provide file paths (absolute preferred)
- Define success criteria
- Let it figure out the implementation details
- Trust its tool use and codebase exploration

### Workers Are Disposable

Workers auto-exit when idle. If a worker's summary shows failure or blocking issues, just spawn a new one with improved instructions.

```
# Read failed worker's summary (Bash)
cat .agentwire/ses_*.md

# Spawn new worker with better instructions
agentwire_pane_spawn(pane_type="opencode-bypass", roles="glm-worker")
agentwire_pane_send(pane=1, message="[improved task based on what failed]")
```

---

## Task Communication

### Structured Instructions Get Best Results

GLM-5 can infer intent, but structured instructions consistently produce better output. Use the task template from Quick Reference.

**Key principles:**
- Front-load critical rules
- Use firm language: "MUST", "STRICTLY"
- Absolute paths preferred
- Explicit steps for multi-file tasks
- Define success criteria

**GLM-5 task example:**
```
TASK: Add JWT authentication to the API

FILES:
- /absolute/path/to/auth/jwt.py (create)
- /absolute/path/to/routes/auth.py (modify)

CONTEXT: Check models/user.py for the User model.

REQUIREMENTS:
- Token generation with 24h expiry
- Login/logout endpoints
- Verify middleware

SUCCESS: Login returns JWT, protected routes reject invalid tokens
```

GLM-5 handles the implementation details — you provide goals and constraints.

---

## Task Decomposition

### The Rule: One Concern Per Worker

**Bad - too much for one worker:**
```
Build a login page with form, validation, API call, and redirect
```

**Good - atomic tasks:**
```
Worker 1: Create LoginForm.tsx with email/password inputs (UI only)
Worker 2: Add validation to LoginForm (error messages)
Worker 3: Add API call to LoginForm (submit handler)
```

### Sizing Tasks

GLM-5 handles larger tasks than GLM-4.7 did. Give it multi-file work confidently.

| Task Size | Workers | Strategy |
|-----------|---------|----------|
| Single function/fix | 1 | Single worker |
| Feature (2-5 files) | 1-2 | Single worker or parallel split |
| Large feature (5+ files) | 2-3 | Parallel workers by concern |
| Full module | 2-3 waves | Sequential waves |

**Example - Building 6 components:**
```
Wave 1: 2 workers (Hero + Features, Pricing + Footer)
Wave 2: 1 worker (Nav + CTA — simple enough for one)
```

### Dependencies

**Parallel (spawn all at once):**
- Components that don't import each other
- Utilities that don't share state
- Separate files with no interaction

**Sequential (wait between):**
- Component B imports Component A
- Page imports multiple components
- Tests that need the code first

---

## Common Patterns

### Creating a Component

```
agentwire_pane_send(pane=1, message="CRITICAL RULES:
- ONLY create: /path/to/Component.tsx
- ABSOLUTE paths only
- Output exit summary when done

TASK: Create [Component] component

FILE: /absolute/path/to/Component.tsx

PROPS:
- propName: type (description)
- propName: type (description)

BEHAVIOR:
- [What it renders]
- [How it responds to props]

STYLING:
- Use Tailwind
- Follow existing patterns in /path/to/similar/Component.tsx

DO NOT:
- Add state (stateless component)
- Import non-existent files
- Create extra files")
```

### Modifying Existing Code

```
agentwire_pane_send(pane=1, message="CRITICAL RULES:
- ONLY modify: /path/to/file.tsx
- Do NOT change other files
- Output exit summary when done

TASK: Add error handling to [function]

FILE: /absolute/path/to/file.tsx

CURRENT STATE:
[Paste relevant code snippet]

CHANGE TO:
- Wrap API call in try/catch
- Show error toast on failure
- Return null on error

KEEP UNCHANGED:
- Function signature
- Success path behavior
- Existing imports")
```

### Creating Multiple Related Files

```
agentwire_pane_send(pane=1, message="CRITICAL RULES:
- ONLY create files listed below
- ABSOLUTE paths only
- Output exit summary when done

TASK: Create auth utilities

FILES (create all):
- /path/to/auth/token.ts
- /path/to/auth/session.ts
- /path/to/auth/index.ts

FILE 1 - token.ts:
- generateToken(userId: string): string
- verifyToken(token: string): { userId: string } | null

FILE 2 - session.ts:
- createSession(userId: string): Session
- getSession(sessionId: string): Session | null

FILE 3 - index.ts:
- Re-export all from token.ts and session.ts")
```

---

## Worker Tracking

### Maintain a Task Map

| Pane | Task | Status |
|------|------|--------|
| 1 | "Auth endpoints" | In progress |
| 2 | "Docs update" | In progress |

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

### 1. Worker Modifies Wrong Files

**Symptom:** Creates files you didn't ask for

**Fix:** Add explicit constraint
```
CRITICAL: ONLY modify files listed in FILES section.
Do NOT create any other files.
```

### 2. Uses Relative Paths

**Symptom:** `import { X } from './utils'` instead of correct path

**Fix:** Provide explicit import statements
```
CONTEXT:
Use these exact imports:
import { formatTime } from '@/utils/formatTime'
import { Button } from '@/components/ui/Button'
```

### 3. Incomplete Implementation

**Symptom:** Function exists but is stubbed with TODO

**Fix:** Add requirement
```
REQUIREMENTS:
- NO placeholder code
- NO TODO comments
- Implement ALL functionality fully
```

### 4. Wrong Styling Approach

**Symptom:** Uses inline styles or wrong CSS framework

**Fix:** Be explicit about styling
```
STYLING:
- Tailwind CSS classes ONLY
- NO inline style={{}}
- NO CSS modules
- Use existing color variables: text-primary, bg-muted, etc.
```

### 5. Doesn't Signal Completion

**Symptom:** Worker finishes but no clear signal

**Fix:** Worker roles now include exit summary format. If still missing, add:
```
When done, output your exit summary (see worker role for ─── DONE ─── format)
```

### Recovery Patterns

**Worker Failed or Blocked:**

```bash
cat .agentwire/ses_*.md
# Check "What Didn't Work" section
# Spawn fresh worker with clearer instructions
```

**Code Is Wrong:**

```bash
git diff path/to/file.tsx  # See what changed
git checkout -- path/to/file.tsx  # Revert
# Spawn new worker with better instructions
```

**Multiple Files Broken:**

```bash
git stash  # Save current state
# Or: git reset --hard HEAD
# Start over with smaller tasks
```

---

## Success Checklist

Before reporting completion to main orchestrator:

- [ ] All workers output exit summary (─── DONE ───)
- [ ] `npm run build` passes (or equivalent)
- [ ] Chrome screenshot looks correct (web projects)
- [ ] No console errors
- [ ] Interactive elements work
- [ ] Edge cases handled (empty states, errors)
- [ ] Git committed

Only then:
```
agentwire_say(text="Feature complete, tested in Chrome")
```

---

## Remember

**GLM-5 workers are capable executors.**

Your job:
1. Break work into clear, scoped tasks
2. Provide structured instructions with context
3. Verify output and iterate if needed
4. Trust GLM-5 to handle implementation details

GLM-5 is frontier-class — give it real tasks, not micro-steps.
