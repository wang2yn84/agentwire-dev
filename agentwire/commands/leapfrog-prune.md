---
description: Write handoff notes, activate the pre-warmed successor, and exit. Call after your phase is committed. Args: [successor-session] [notes]
---

Your phase is committed. Hand off to the successor and exit. Arguments: $ARGUMENTS

If a successor session name is provided in arguments, use it. Otherwise read `.leapfrog` in the project root (written by `/leapfrog-scout`). Any remaining text after the session name is extra handoff notes.

## Steps

1. **Find the successor session name:**
   - If provided in arguments, use it
   - Otherwise: `Bash: cat .leapfrog` and parse the `successor=` line

2. **Get the committed sha:**
   ```
   Bash: git rev-parse --short HEAD
   ```

3. **Write `docs/leapfrog-handoff.md`:**
   ```markdown
   # Leapfrog Handoff

   ## Phase Completed
   [your phase name/number]

   ## Committed At
   [sha]

   ## What Was Done
   [concise summary — what was implemented, what changed]

   ## Deferred to Next Phase
   [anything moved out, skipped, or left incomplete]

   ## Decisions That Affect the Plan
   [architectural choices, scope changes, surprises the successor should know]

   ## New Files / Patterns Introduced
   [files created or significantly changed that the successor will likely touch]
   ```

4. **Send the successor the activation message:**
   ```
   session_send(session="{successor}", message="The prior phase is committed at {sha}. Read docs/leapfrog-handoff.md — it has what actually happened vs. what was planned. Revise your plan to match reality. Then run /leapfrog-scout for whatever phase comes after yours to keep the chain going. Then begin executing.")
   ```

5. **Clean up the state file:**
   ```
   Bash: rm -f .leapfrog
   ```

6. **Say goodbye** via `say`: "Handing off to {successor}. Exiting."

7. **Exit:**
   ```
   Bash: exit
   ```
