/**
 * AgentWire Notification Plugin for OpenCode
 *
 * Event-bus-aware idle handler that tracks session activity
 * before deciding how to handle idle events.
 *
 * Subscribes to the actual OpenCode event bus:
 *   - session.idle         → Trigger idle handling (gated)
 *   - session.status       → Track busy/idle/retry transitions
 *   - message.updated      → Count completed assistant responses
 *   - session.diff         → Detect file changes
 *   - session.deleted      → Clean up state
 *
 * Gate logic prevents spurious idle handling:
 *   Gate A — Skip if in retry/rate-limit state
 *   Gate B — Require meaningful work (completed responses) before summary prompts
 *
 * Supports scheduled tasks (agentwire ensure) for pane 0.
 */

import { execSync, spawn } from "child_process"
import { readFileSync, writeFileSync, appendFileSync, existsSync, unlinkSync } from "fs"
import { basename, join } from "path"
import { homedir } from "os"

// =============================================================================
// Debug logging
// =============================================================================

const DEBUG_LOG = "/tmp/opencode-plugin-debug.log"

function log(msg: string): void {
  try {
    appendFileSync(DEBUG_LOG, `[${new Date().toISOString()}] ${msg}\n`)
  } catch {
    // Ignore logging errors
  }
}

// =============================================================================
// Helpers
// =============================================================================

function getAgentwirePath(): string {
  if (process.env.AGENTWIRE_BIN) return process.env.AGENTWIRE_BIN
  try {
    return execSync("which agentwire", { encoding: "utf-8", timeout: 1000 }).trim()
  } catch {
    return join(homedir(), ".local", "bin", "agentwire")
  }
}

interface AgentWireConfig {
  session?: string
  parent?: string
  voice?: string
  roles?: string[]
}

function parseAgentWireYml(cwd: string): AgentWireConfig {
  const configPath = join(cwd, ".agentwire.yml")
  if (!existsSync(configPath)) return {}
  try {
    const content = readFileSync(configPath, "utf-8")
    const config: AgentWireConfig = {}
    for (const line of content.split("\n")) {
      const sessionMatch = line.match(/^session:\s*["']?([^"'\n]+)["']?/)
      if (sessionMatch) config.session = sessionMatch[1].trim()
      const parentMatch = line.match(/^parent:\s*["']?([^"'\n]+)["']?/)
      if (parentMatch) config.parent = parentMatch[1].trim()
      const voiceMatch = line.match(/^voice:\s*["']?([^"'\n]+)["']?/)
      if (voiceMatch) config.voice = voiceMatch[1].trim()
      if (line.match(/^-\s*chatbot/)) {
        config.roles = config.roles || []
        config.roles.push("chatbot")
      }
    }
    return config
  } catch {
    return {}
  }
}

function getPaneIndex(): number | null {
  const tmuxPane = process.env.TMUX_PANE
  if (!tmuxPane) return null
  try {
    const output = execSync(`tmux display -t "${tmuxPane}" -p '#{pane_index}'`, { encoding: "utf-8", timeout: 1000 })
    return parseInt(output.trim(), 10)
  } catch {
    return null
  }
}

function getTmuxSessionName(): string | null {
  const tmuxPane = process.env.TMUX_PANE
  if (!tmuxPane) return null
  try {
    return execSync(`tmux display -t "${tmuxPane}" -p '#{session_name}'`, { encoding: "utf-8", timeout: 1000 }).trim()
  } catch {
    return null
  }
}

function isQueueProcessorRunning(session: string): boolean {
  const pidFile = join(homedir(), ".agentwire", "queues", `${session}.pid`)
  if (!existsSync(pidFile)) return false
  try {
    const pid = readFileSync(pidFile, "utf-8").trim()
    process.kill(parseInt(pid, 10), 0)
    return true
  } catch {
    return false
  }
}

function queueNotification(session: string, message: string): void {
  const queueDir = join(homedir(), ".agentwire", "queues")
  const queueFile = join(queueDir, `${session}.jsonl`)
  appendFileSync(queueFile, JSON.stringify({ timestamp: Date.now(), message }) + "\n")
  if (!isQueueProcessorRunning(session)) {
    const processor = spawn(
      join(homedir(), ".agentwire", "queue-processor.sh"),
      [session],
      { detached: true, stdio: "ignore" }
    )
    processor.unref()
  }
}

// =============================================================================
// Per-session activity state
// =============================================================================

interface SessionState {
  completedResponses: number  // from message.updated with time.completed
  busyCount: number           // from session.status {type: "busy"}
  hasDiffs: boolean           // from session.diff with non-empty diff array
  retryCount: number          // from session.status {type: "retry"}
  lastRetryAt: number | null
  inRetryState: boolean
  idlePassCount: number
  summaryRequested: boolean
}

function newSessionState(): SessionState {
  return {
    completedResponses: 0,
    busyCount: 0,
    hasDiffs: false,
    retryCount: 0,
    lastRetryAt: null,
    inRetryState: false,
    idlePassCount: 0,
    summaryRequested: false,
  }
}

function getState(sid: string | undefined, states: Map<string, SessionState>): SessionState {
  const key = sid || "__default__"
  if (!states.has(key)) states.set(key, newSessionState())
  return states.get(key)!
}

function activitySummary(state: SessionState): string {
  const parts: string[] = []
  if (state.completedResponses > 0) parts.push(`${state.completedResponses} responses`)
  if (state.hasDiffs) parts.push("files changed")
  if (state.busyCount > 0) parts.push(`${state.busyCount} busy cycles`)
  return parts.length > 0 ? `after work: ${parts.join(", ")}` : "no activity detected"
}

function hasMeaningfulWork(state: SessionState): boolean {
  // At least one completed assistant response = real work happened
  return state.completedResponses >= 1
}

// =============================================================================
// Scheduled task support (ported from bash hook)
// =============================================================================

interface TaskContext {
  task: string
  summary_file: string
  started_at: string
  attempt: number
  idle_count: number
  exit_on_complete: boolean
}

function readTaskContext(tmuxSession: string): TaskContext | null {
  const contextFile = join(homedir(), ".agentwire", "tasks", `${tmuxSession}.json`)
  if (!existsSync(contextFile)) return null
  try {
    return JSON.parse(readFileSync(contextFile, "utf-8"))
  } catch {
    return null
  }
}

function updateTaskContext(tmuxSession: string, updates: Partial<TaskContext>): void {
  const contextFile = join(homedir(), ".agentwire", "tasks", `${tmuxSession}.json`)
  try {
    const context = JSON.parse(readFileSync(contextFile, "utf-8"))
    Object.assign(context, updates)
    writeFileSync(contextFile, JSON.stringify(context, null, 2))
  } catch {
    // Ignore errors
  }
}

function clearTaskContext(tmuxSession: string): void {
  const contextFile = join(homedir(), ".agentwire", "tasks", `${tmuxSession}.json`)
  try { unlinkSync(contextFile) } catch { /* ignore */ }
}

function handleScheduledTask(tmuxSession: string, cwd: string): void {
  const ctx = readTaskContext(tmuxSession)
  if (!ctx) return

  const newIdleCount = ctx.idle_count + 1
  updateTaskContext(tmuxSession, { idle_count: newIdleCount })

  log(`TASK: session=${tmuxSession} task=${ctx.task} idle_count=${newIdleCount} exit_on_complete=${ctx.exit_on_complete}`)

  if (newIdleCount === 1) {
    // First idle: send summary prompt
    const summaryPath = join(cwd, ctx.summary_file)
    const instruction = `Task complete. Write a brief summary to ${summaryPath} with:
# Task Summary
## Status
complete | incomplete | error
## What Was Done
[Brief description]
## Notes
[Any important context]`

    log("TASK: sending summary prompt")
    try {
      const child = spawn(
        getAgentwirePath(),
        ["send", "-s", tmuxSession, instruction],
        { detached: true, stdio: "ignore" }
      )
      child.unref()
    } catch {
      // Ignore errors
    }
  } else {
    // Second+ idle: exit if configured
    if (ctx.exit_on_complete) {
      log("TASK: exit_on_complete=true, sending /exit")
      setTimeout(() => {
        try {
          const child = spawn(
            getAgentwirePath(),
            ["send", "-s", tmuxSession, "/exit"],
            { detached: true, stdio: "ignore" }
          )
          child.unref()
        } catch {
          // Ignore errors
        }

        // Clean up task context
        clearTaskContext(tmuxSession)
        log("TASK: cleaned up task context")

        // Kill tmux session after grace period
        setTimeout(() => {
          log("TASK: killing tmux session")
          try {
            execSync(`tmux kill-session -t "${tmuxSession}"`, { timeout: 5000 })
          } catch {
            // Ignore errors
          }
        }, 3000)
      }, 1000)
    }
  }
}

// =============================================================================
// session.status handler
// =============================================================================

function handleStatus(event: any, states: Map<string, SessionState>): void {
  const sid = event.properties?.sessionID
  const state = getState(sid, states)
  const statusType = event.properties?.status?.type

  if (statusType === "retry") {
    state.retryCount++
    state.lastRetryAt = Date.now()
    state.inRetryState = true
    const attempt = event.properties?.status?.attempt || state.retryCount
    const message = event.properties?.status?.message || ""
    log(`STATUS: retry sid=${sid} attempt=${attempt} msg=${message}`)
  } else if (statusType === "busy") {
    state.inRetryState = false
    state.busyCount++
  } else if (statusType === "idle") {
    // session.status {type: "idle"} fires right before session.idle
    // Don't handle here, wait for session.idle event
    state.inRetryState = false
  }
}

// =============================================================================
// message.updated handler — track completed assistant responses
// =============================================================================

function handleMessageUpdated(event: any, states: Map<string, SessionState>): void {
  const info = event.properties?.info
  if (!info) return

  const sid = info.sessionID
  if (!sid) return

  // Only count completed assistant responses (has time.completed)
  if (info.role === "assistant" && info.time?.completed) {
    const state = getState(sid, states)
    state.completedResponses++
    log(`MSG: completed response sid=${sid} total=${state.completedResponses}`)
  }
}

// =============================================================================
// session.diff handler — detect file changes
// =============================================================================

function handleDiff(event: any, states: Map<string, SessionState>): void {
  const sid = event.properties?.sessionID
  const diff = event.properties?.diff
  if (sid && Array.isArray(diff) && diff.length > 0) {
    const state = getState(sid, states)
    state.hasDiffs = true
    log(`DIFF: file changes detected sid=${sid}`)
  }
}

// =============================================================================
// Main idle handler with gate logic
// =============================================================================

async function handleIdle(event: any, states: Map<string, SessionState>): Promise<void> {
  const sid = event.properties?.sessionID
  const state = getState(sid, states)
  state.idlePassCount++

  const cwd = process.cwd()
  const config = parseAgentWireYml(cwd)

  // Skip chatbot sessions
  if (config.roles?.includes("chatbot")) {
    log(`IDLE: skipping chatbot session sid=${sid}`)
    return
  }

  const sessionName = config.session || basename(cwd)
  const paneIndex = getPaneIndex()
  const isWorker = paneIndex !== null && paneIndex > 0
  const tmuxSession = getTmuxSessionName()

  log(`IDLE: sid=${sid} pane=${paneIndex} session=${tmuxSession} pass=${state.idlePassCount} activity=(${activitySummary(state)})`)

  // ─── Gate A: Rate-limit retry ─────────────────────────────────────
  if (state.inRetryState || (state.lastRetryAt && Date.now() - state.lastRetryAt < 10_000)) {
    log(`GATE-A: skipping idle — in retry state (retries=${state.retryCount}, lastRetry=${state.lastRetryAt ? new Date(state.lastRetryAt).toISOString() : "never"})`)
    return
  }

  // ─── Workers (pane > 0) ───────────────────────────────────────────
  if (isWorker && tmuxSession) {
    // ─── Gate B: Meaningful work check ────────────────────────────
    if (!hasMeaningfulWork(state)) {
      if (state.idlePassCount <= 1) {
        // First idle with no work: grace period
        log(`GATE-B: no meaningful work yet, grace period (pass ${state.idlePassCount})`)
        return
      } else {
        // Second+ idle with still no work: notify failure and kill
        log(`GATE-B: no meaningful work after ${state.idlePassCount} idle passes, notifying failure`)
        const message = `[WORKER FAILED pane ${paneIndex}] No meaningful activity detected (${state.retryCount} retries). Likely rate-limited or errored.`
        queueNotification(tmuxSession, message)
        setTimeout(() => {
          try {
            const kill = spawn(
              getAgentwirePath(),
              ["kill", "--pane", String(paneIndex)],
              { detached: true, stdio: "ignore" }
            )
            kill.unref()
          } catch {
            // Ignore errors
          }
        }, 1000)
        return
      }
    }

    // ─── Two-pass summary system (worker did real work) ───────────
    const summaryPath = join(cwd, ".agentwire", `${sid}.md`)
    const summaryExists = existsSync(summaryPath)

    setTimeout(() => {
      if (!summaryExists && !state.summaryRequested) {
        // First idle with work: request summary
        state.summaryRequested = true
        log(`WORKER: requesting summary (${activitySummary(state)})`)

        const instruction = `Please write an exit summary to ${summaryPath} with these sections:

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

## What Didn't Work
[Issues and why]

## Notes for Orchestrator
[Context for follow-up]`

        try {
          const send = spawn(
            getAgentwirePath(),
            ["send", "--pane", String(paneIndex), instruction],
            { detached: true, stdio: "ignore" }
          )
          send.unref()
        } catch {
          // Ignore errors
        }
      } else if (summaryExists) {
        // Second idle: summary written, read and notify parent
        log(`WORKER: reading summary and notifying (${activitySummary(state)})`)
        try {
          const summaryContent = readFileSync(summaryPath, "utf-8")
          const message = `[WORKER SUMMARY pane ${paneIndex}] (${activitySummary(state)})\n\n${summaryContent}`
          queueNotification(tmuxSession, message)

          // Kill pane after queuing
          setTimeout(() => {
            try {
              const kill = spawn(
                getAgentwirePath(),
                ["kill", "--pane", String(paneIndex)],
                { detached: true, stdio: "ignore" }
              )
              kill.unref()
            } catch {
              // Ignore errors
            }
          }, 1000)
        } catch {
          // Can't read summary, just kill the pane
          log("WORKER: failed to read summary, killing pane")
          try {
            const kill = spawn(
              getAgentwirePath(),
              ["kill", "--pane", String(paneIndex)],
              { detached: true, stdio: "ignore" }
            )
            kill.unref()
          } catch {
            // Ignore errors
          }
        }
      }
    }, 2000) // Wait 2s for OpenCode to settle

  // ─── Orchestrator (pane 0) ────────────────────────────────────────
  } else if (paneIndex === 0 && tmuxSession) {
    // Check for scheduled task context first
    const taskCtx = readTaskContext(tmuxSession)
    if (taskCtx) {
      handleScheduledTask(tmuxSession, cwd)
    } else if (config.parent) {
      // No scheduled task — notify parent with activity context
      const message = `${sessionName} is idle (${activitySummary(state)})`
      log(`ORCHESTRATOR: notifying parent=${config.parent} message="${message}"`)
      try {
        const child = spawn(
          getAgentwirePath(),
          ["alert", "-q", "--to", config.parent, message],
          { detached: true, stdio: "ignore" }
        )
        child.unref()
      } catch {
        // Ignore errors
      }
    }
  }
}

// =============================================================================
// Plugin export — multi-event dispatch
// =============================================================================

export const AgentWireNotifyPlugin = async () => {
  const states = new Map<string, SessionState>()

  log("Plugin loaded — event-bus-aware idle handler v2")

  return {
    event: async ({ event }: any) => {
      switch (event.type) {
        case "message.updated":
          handleMessageUpdated(event, states)
          break

        case "session.status":
          handleStatus(event, states)
          break

        case "session.diff":
          handleDiff(event, states)
          break

        case "session.deleted": {
          const sid = event.properties?.sessionID || event.properties?.info?.id
          if (sid) states.delete(sid)
          break
        }

        case "session.idle":
          await handleIdle(event, states)
          break
      }
    },
  }
}
