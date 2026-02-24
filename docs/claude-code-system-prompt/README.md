# Claude Code System Prompt (Reference Copy)

Extracted from Claude Code **v2.1.51** (February 23, 2026).

Source: [Piebald-AI/claude-code-system-prompts](https://github.com/Piebald-AI/claude-code-system-prompts)

## Purpose

Reference copy for building a modified system prompt for claudeGLM sessions (GLM-5 via Z.AI).
The goal is to replace Claude-specific identity with GLM-5 identity while preserving all
tool usage instructions, behavioral rules, and safety policies.

## Structure

The system prompt is not a single string. It's ~60 files conditionally assembled:

- **`system-prompt-*.md`** - Core behavioral sections (identity, tools policy, safety, tone)
- **`tool-description-*.md`** - Built-in tool documentation (Bash, Read, Edit, etc.)
- **`agent-prompt-*.md`** - Sub-agent prompts (Explore, Task)

### Key files for GLM-5 adaptation

| File | Why it matters |
|------|---------------|
| `system-prompt-main-system-prompt.md` | Identity - says "You are Claude Code" |
| `system-prompt-tone-and-style.md` | References Claude-specific behavior |
| `system-prompt-doing-tasks.md` | Task execution guidelines |
| `system-prompt-tool-usage-policy.md` | How to use tools (agent-agnostic) |
| `system-prompt-executing-actions-with-care.md` | Safety rules (agent-agnostic) |

## Updating

To refresh from a newer Claude Code version:

```bash
cd /tmp
git clone https://github.com/Piebald-AI/claude-code-system-prompts
cp claude-code-system-prompts/system-prompts/system-prompt-*.md \
   ~/projects/agentwire-dev/docs/claude-code-system-prompt/
cp claude-code-system-prompts/system-prompts/tool-description-*.md \
   ~/projects/agentwire-dev/docs/claude-code-system-prompt/
cp claude-code-system-prompts/system-prompts/agent-prompt-task-tool.md \
   claude-code-system-prompts/system-prompts/agent-prompt-explore.md \
   ~/projects/agentwire-dev/docs/claude-code-system-prompt/
```
