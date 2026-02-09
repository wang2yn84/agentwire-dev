#!/bin/bash
# Handle Claude Code notifications
# Uses agentwire alert for text-only notifications to parent (no audio)
# Worker panes queue notifications with summary file path, then auto-kill

DEBUG_LOG="/tmp/claude-hook-debug.log"
log() { echo "[$(date -Iseconds)] $*" >> "$DEBUG_LOG"; }

# Find agentwire binary (env var > which > default)
AGENTWIRE="${AGENTWIRE_BIN:-$(which agentwire 2>/dev/null || echo "$HOME/.local/bin/agentwire")}"

input=$(cat)
notification_type=$(echo "$input" | jq -r '.notification_type // ""')
log "Hook fired: type=$notification_type TMUX_PANE=$TMUX_PANE"

if [[ "$notification_type" == "idle_prompt" ]]; then
  cwd=$(echo "$input" | jq -r '.cwd // ""')
  session_id=$(echo "$input" | jq -r '.session_id // ""')

  # Get pane info
  pane_index=""
  tmux_session=""
  if [[ -n "$TMUX_PANE" ]]; then
    pane_index=$(tmux display -t "$TMUX_PANE" -p '#{pane_index}' 2>/dev/null)
    tmux_session=$(tmux display -t "$TMUX_PANE" -p '#{session_name}' 2>/dev/null)
  fi

  # Try to get config from .agentwire.yml
  session_name=""
  is_chatbot=false
  parent_session=""

  if [[ -f "$cwd/.agentwire.yml" ]]; then
    session_name=$(grep -E '^session:' "$cwd/.agentwire.yml" 2>/dev/null | sed 's/session:[[:space:]]*//' | tr -d '"' | tr -d "'")
    parent_session=$(grep -E '^parent:' "$cwd/.agentwire.yml" 2>/dev/null | sed 's/parent:[[:space:]]*//' | tr -d '"' | tr -d "'")

    if grep -qE '^-[[:space:]]*chatbot' "$cwd/.agentwire.yml" 2>/dev/null; then
      is_chatbot=true
    fi
  fi

  # Skip for chatbot sessions
  if [[ "$is_chatbot" == true ]]; then
    exit 0
  fi

  # Fallback to directory name
  if [[ -z "$session_name" ]]; then
    session_name=$(basename "$cwd")
  fi

  log "pane_index=$pane_index tmux_session=$tmux_session session_name=$session_name session_id=$session_id"

  # Check if this is a worker pane (pane_index > 0)
  if [[ "$pane_index" != "0" && -n "$pane_index" && -n "$tmux_session" ]]; then
    log "Worker pane detected, starting background job"
    # Worker pane: implement two-pass idle system
    (
      dlog="/tmp/claude-hook-debug.log"
      echo "[$(date -Iseconds)] BG: started pane=$pane_index session=$tmux_session session_id=$session_id" >> "$dlog"

      # Wait 2s for Claude Code to settle
      sleep 2
      echo "[$(date -Iseconds)] BG: after sleep" >> "$dlog"

      # Check if summary file exists
      summary_path="${cwd}/.agentwire/${session_id}.md"
      summary_exists=0
      if [[ -f "$summary_path" ]]; then
        summary_exists=1
      fi
      echo "[$(date -Iseconds)] BG: summary_exists=$summary_exists path=$summary_path" >> "$dlog"

      if [[ "$summary_exists" == "0" ]]; then
        # First idle: No summary file yet, instruct agent to create one
        echo "[$(date -Iseconds)] BG: sending instruction to create summary" >> "$dlog"

        instruction='Please write an exit summary to '"$summary_path"' with these sections:

# Worker Summary

## Task
[What you were asked to do]

## Status
─── DONE ─── (success) | ─── BLOCKED ─── (needs help) | ─── ERROR ─── (failed)

## What I Did
[Actions taken]

## Files Changed
List files you modified or created with brief descriptions

## What Worked
[Successes]

## What Didn'"'"'t Work
[Issues and why]

## Notes for Orchestrator
[Context for follow-up]'

        $AGENTWIRE send --pane "$pane_index" "$instruction" >/dev/null 2>&1 &
        echo "[$(date -Iseconds)] BG: instruction sent" >> "$dlog"
      else
        # Second idle: Summary file exists, read it and notify parent
        echo "[$(date -Iseconds)] BG: reading summary file" >> "$dlog"

        # Read summary content
        summary_content=""
        if command -v cat >/dev/null 2>&1; then
          summary_content=$(cat "$summary_path" 2>/dev/null || echo "")
        fi

        if [[ -n "$summary_content" ]]; then
          message="[WORKER SUMMARY pane ${pane_index}]

${summary_content}"
          echo "[$(date -Iseconds)] BG: message built, queuing notification" >> "$dlog"

          # Queue the notification
          queue_dir="$HOME/.agentwire/queues"
          queue_file="${queue_dir}/${tmux_session}.jsonl"
          mkdir -p "$queue_dir"

          # Append to queue as JSON line
          escaped_message=$(printf '%s' "$message" | jq -Rs .)
          timestamp=$(date +%s)000
          echo "{\"timestamp\":${timestamp},\"message\":${escaped_message}}" >> "$queue_file"
          echo "[$(date -Iseconds)] BG: queued notification" >> "$dlog"

          # Start queue processor if not running
          pid_file="${queue_dir}/${tmux_session}.pid"
          if [[ ! -f "$pid_file" ]] || ! kill -0 "$(cat "$pid_file" 2>/dev/null)" 2>/dev/null; then
            nohup "$HOME/.agentwire/queue-processor.sh" "$tmux_session" >/dev/null 2>&1 &
            echo "[$(date -Iseconds)] BG: started queue processor" >> "$dlog"
          fi

          # Wait 1s then kill the pane (kill command has its own 3s internal wait)
          sleep 1
          echo "[$(date -Iseconds)] BG: killing pane" >> "$dlog"
          $AGENTWIRE kill --pane "$pane_index" >/dev/null 2>&1 &
        else
          echo "[$(date -Iseconds)] BG: failed to read summary, killing pane anyway" >> "$dlog"
          # Failed to read summary, just kill the pane
          $AGENTWIRE kill --pane "$pane_index" >/dev/null 2>&1 &
        fi
      fi
    ) &
  elif [[ "$pane_index" == "0" && -n "$tmux_session" ]]; then
    # Pane 0: Check for scheduled task context
    task_context_file="$HOME/.agentwire/tasks/${tmux_session}.json"

    if [[ -f "$task_context_file" ]]; then
      log "Scheduled task detected, starting background job"
      # Scheduled task: handle completion
      (
        dlog="/tmp/claude-hook-debug.log"
        echo "[$(date -Iseconds)] TASK: started session=$tmux_session" >> "$dlog"

        # Wait 2s for Claude to settle
        sleep 2

        # Read task context
        task_name=$(jq -r '.task // ""' "$task_context_file" 2>/dev/null)
        summary_file=$(jq -r '.summary_file // ""' "$task_context_file" 2>/dev/null)
        idle_count=$(jq -r '.idle_count // 0' "$task_context_file" 2>/dev/null)
        exit_on_complete=$(jq -r '.exit_on_complete // true' "$task_context_file" 2>/dev/null)

        echo "[$(date -Iseconds)] TASK: task=$task_name idle_count=$idle_count exit_on_complete=$exit_on_complete" >> "$dlog"

        # Increment idle count
        new_idle_count=$((idle_count + 1))
        jq ".idle_count = $new_idle_count" "$task_context_file" > "${task_context_file}.tmp" && mv "${task_context_file}.tmp" "$task_context_file"

        if [[ "$new_idle_count" == "1" ]]; then
          # First idle: send summary prompt
          echo "[$(date -Iseconds)] TASK: first idle, sending summary prompt" >> "$dlog"
          summary_path="${cwd}/${summary_file}"

          instruction="Task complete. Write a brief summary to ${summary_path} with:
# Task Summary
## Status
complete | incomplete | error
## What Was Done
[Brief description]
## Notes
[Any important context]"

          $AGENTWIRE send -s "$tmux_session" "$instruction" >/dev/null 2>&1 &
          echo "[$(date -Iseconds)] TASK: summary prompt sent" >> "$dlog"
        else
          # Second+ idle: optionally exit session (ensure polls summary file directly)
          echo "[$(date -Iseconds)] TASK: second idle" >> "$dlog"

          if [[ "$exit_on_complete" == "true" ]]; then
            echo "[$(date -Iseconds)] TASK: exit_on_complete=true, sending /exit" >> "$dlog"
            sleep 1
            $AGENTWIRE send -s "$tmux_session" "/exit" >/dev/null 2>&1

            # Wait for Claude to exit, then kill the tmux session
            sleep 3
            echo "[$(date -Iseconds)] TASK: killing tmux session" >> "$dlog"
            tmux kill-session -t "$tmux_session" 2>/dev/null &
          fi
        fi
      ) &
    else
      # No task context file - check if this might be a scheduled task that lost its context
      # Look for recent task summary files scoped to THIS session (avoids false matches
      # when multiple sessions share the same project directory)
      recent_summary=$(find "${cwd}/.agentwire" -name "task-summary-${tmux_session}-*.md" -mmin -5 2>/dev/null | head -1)

      if [[ -n "$recent_summary" ]]; then
        log "No task context but found recent summary file, cleaning up session"
        # Task appears to have completed but context was cleared - try to exit gracefully
        (
          dlog="/tmp/claude-hook-debug.log"
          echo "[$(date -Iseconds)] TASK-ORPHAN: found summary at $recent_summary, exiting session" >> "$dlog"
          sleep 1
          $AGENTWIRE send -s "$tmux_session" "/exit" >/dev/null 2>&1
          sleep 3
          echo "[$(date -Iseconds)] TASK-ORPHAN: killing tmux session" >> "$dlog"
          tmux kill-session -t "$tmux_session" 2>/dev/null &
        ) &
      elif [[ -n "$parent_session" ]]; then
        # Orchestrator pane with parent: notify parent
        message="${session_name} is idle"
        $AGENTWIRE alert -q --to "$parent_session" "$message" 2>/dev/null &
      fi
    fi
  fi
fi

exit 0
