---
description: Spawn the successor leapfrog session and send it the pre-warm prompt. Run this before or at the start of execution. Args: <next-phase> [notes for successor]
---

Spawn the successor leapfrog session and get it pre-warming in parallel. Arguments: $ARGUMENTS

Parse the next phase name/number from the arguments. Any remaining text is context notes to pass to the successor.

## Steps

1. **Determine session names.** Read `$AGENTWIRE_SESSION` for the current session name. Derive the successor by incrementing the lf number — e.g. `myproject-lf1` → `myproject-lf2`. If `$AGENTWIRE_SESSION` is not set, run `Bash: echo $AGENTWIRE_SESSION` or check the tmux session name.

2. **Get the project path:**
   ```
   Bash: pwd
   ```

3. **Create the successor session** in the same project directory:
   ```
   session_create(name="{successor}", path="{cwd}")
   ```

4. **Wait for it to be ready.** The session needs a few seconds to start Claude. Poll until output shows Claude's prompt:
   ```
   session_output(session="{successor}")
   ```
   Wait up to 15 seconds, checking every 3 seconds. Proceed once you see Claude's interface or `>` prompt.

5. **Send the pre-warm prompt:**
   ```
   session_send(session="{successor}", message="/leapfrog-prime {next_phase} {notes}")
   ```

6. **Write the state file** so `/leapfrog-prune` can find the successor without needing an explicit arg:
   ```
   Bash: printf 'successor=%s\nnext_phase=%s\n' '{successor}' '{next_phase}' > .leapfrog
   ```

7. **Confirm** via `say`: "Successor {successor} spawned and priming for {next_phase}."

Do not execute anything else. The successor will load context, plan, and go idle on its own.
