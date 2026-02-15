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
import { readFileSync, writeFileSync, appendFileSync, existsSync, unlinkSync, mkdirSync } from "fs"
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
// Progress push to portal
// =============================================================================

let portalUrl: string | null = null

function getPortalUrl(): string {
  if (portalUrl) return portalUrl
  try {
    const configPath = join(homedir(), ".agentwire", "config.yaml")
    if (existsSync(configPath)) {
      const content = readFileSync(configPath, "utf-8")
      // Look for portal.url in config (under "portal:" section)
      const portalMatch = content.match(/^portal:\s*\n\s+url:\s*["']?([^"'\s]+)/m)
      if (portalMatch) {
        portalUrl = portalMatch[1]
        return portalUrl
      }
      // Fall back to constructing from server config
      const portMatch = content.match(/^server:\s*\n(?:.*\n)*?\s+port:\s*(\d+)/m)
      const port = portMatch ? portMatch[1] : "8765"
      portalUrl = `https://localhost:${port}`
      return portalUrl
    }
  } catch {
    // fallback
  }
  portalUrl = "https://localhost:8765"
  return portalUrl
}

function postProgress(session: string, state: SessionState, status: string): void {
  try {
    const url = `${getPortalUrl()}/api/notify`
    const payload = JSON.stringify({
      event: "agent_progress",
      session,
      status,
      responses: state.completedResponses,
      has_diffs: state.hasDiffs,
      busy_cycles: state.busyCount,
      retry_count: state.retryCount,
    })
    const child = spawn("curl", [
      "-sk", "-X", "POST",
      "-H", "Content-Type: application/json",
      "-d", payload,
      url,
    ], { detached: true, stdio: "ignore" })
    child.unref()
  } catch {
    // Fire-and-forget, ignore errors
  }
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
  mode: string           // "standard" or "loop"
  max_iterations: number // Safety cap (default: 3)
  iteration: number      // Current iteration (1-based)
  loop_review: boolean   // Write review file between iterations
  loop_delay: number     // Seconds to wait between loop iterations
  original_prompt: string // Fully expanded task prompt for re-sending
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

  const mode = ctx.mode || "standard"

  if (mode === "loop") {
    handleLoopTask(tmuxSession, cwd, ctx)
  } else {
    handleStandardTask(tmuxSession, cwd, ctx)
  }
}

function handleStandardTask(tmuxSession: string, cwd: string, ctx: TaskContext): void {
  const newIdleCount = ctx.idle_count + 1
  updateTaskContext(tmuxSession, { idle_count: newIdleCount })

  log(`TASK[standard]: session=${tmuxSession} task=${ctx.task} idle_count=${newIdleCount} exit_on_complete=${ctx.exit_on_complete}`)

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

    log("TASK[standard]: sending summary prompt")
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
    exitTask(tmuxSession, ctx)
  }
}

function handleLoopTask(tmuxSession: string, cwd: string, ctx: TaskContext): void {
  const iteration = ctx.iteration || 1
  const maxIterations = ctx.max_iterations || 3
  const loopReview = ctx.loop_review !== false
  const newIdleCount = ctx.idle_count + 1

  updateTaskContext(tmuxSession, { idle_count: newIdleCount })

  log(`TASK[loop]: session=${tmuxSession} task=${ctx.task} iteration=${iteration}/${maxIterations} idle_count=${newIdleCount} loop_review=${loopReview}`)

  if (loopReview) {
    // Two-pass mode: idle 1 → review prompt, idle 2 → check review + decide
    if (newIdleCount === 1) {
      // Send review prompt
      const iterFile = join(cwd, `.agentwire/iterations/${tmuxSession}-iter-${iteration}.md`)
      const iterDir = join(cwd, ".agentwire/iterations")
      try { mkdirSync(iterDir, { recursive: true }) } catch { /* ignore */ }

      const instruction = `Review your progress so far. Write a brief status report to ${iterFile}:

# Iteration ${iteration} Review

## Status
complete | incomplete

## What Was Done
[Brief description of work in this iteration]

## Remaining Work
[What still needs to be done, or "none" if complete]

Use "complete" if the task is fully done. Use "incomplete" if more work is needed.
Write the file now.`

      log(`TASK[loop]: sending review prompt for iteration ${iteration}`)
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
      // Second idle: read iteration file and decide
      const iterFile = join(cwd, `.agentwire/iterations/${tmuxSession}-iter-${iteration}.md`)
      let status = "incomplete"

      if (existsSync(iterFile)) {
        try {
          const content = readFileSync(iterFile, "utf-8")
          const match = content.match(/##\s*Status[\s:]*\n?\s*(\w+)/i)
          if (match) status = match[1].toLowerCase()
        } catch {
          // Ignore read errors
        }
      }

      log(`TASK[loop]: iteration ${iteration} status=${status}`)

      if (status === "complete" || iteration >= maxIterations) {
        log(`TASK[loop]: exiting loop (status=${status}, iteration=${iteration}/${maxIterations})`)
        transitionToStandardExit(tmuxSession, cwd, ctx)
      } else {
        continueLoop(tmuxSession, cwd, ctx, iteration)
      }
    }
  } else {
    // Single-pass mode: idle → check iteration cap → re-prompt or exit
    if (iteration >= maxIterations) {
      log(`TASK[loop]: max iterations reached (${iteration}/${maxIterations}), exiting`)
      transitionToStandardExit(tmuxSession, cwd, ctx)
    } else {
      continueLoop(tmuxSession, cwd, ctx, iteration)
    }
  }
}

function transitionToStandardExit(tmuxSession: string, cwd: string, ctx: TaskContext): void {
  // Switch to standard mode so next idle triggers normal summary → exit flow
  updateTaskContext(tmuxSession, { mode: "standard", idle_count: 0 })
  log(`TASK[loop→standard]: transitioned to standard exit`)

  // The next idle event will enter handleStandardTask with idle_count=0,
  // which will increment to 1 and send the summary prompt
}

function continueLoop(tmuxSession: string, cwd: string, ctx: TaskContext, iteration: number): void {
  const nextIteration = iteration + 1
  const maxIterations = ctx.max_iterations || 3
  const originalPrompt = ctx.original_prompt || ""
  const loopDelay = ctx.loop_delay || 0
  const iterationsDir = join(cwd, ".agentwire/iterations")

  // Reset idle_count and advance iteration
  updateTaskContext(tmuxSession, { idle_count: 0, iteration: nextIteration })

  const sendNext = () => {
    const instruction = `Continue working on the task. This is iteration ${nextIteration} of ${maxIterations}.

Previous iteration reviews are in ${iterationsDir}/ — read them for context on what's been done.

Original task:
${originalPrompt}

Continue where you left off. Focus on remaining work identified in previous reviews.`

    log(`TASK[loop]: sending iteration ${nextIteration}/${maxIterations}`)
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
  }

  if (loopDelay > 0) {
    log(`TASK[loop]: waiting ${loopDelay}s before iteration ${nextIteration}/${maxIterations}`)
    setTimeout(sendNext, loopDelay * 1000)
  } else {
    log(`TASK[loop]: continuing to iteration ${nextIteration}/${maxIterations}`)
    sendNext()
  }
}

function exitTask(tmuxSession: string, ctx: TaskContext): void {
  if (ctx.exit_on_complete) {
    log("TASK: exit_on_complete=true, cleaning up task context")
    // Don't send /exit — OpenCode exits on its own when idle.
    // Don't kill the tmux session — the scheduler needs it alive for the next task.
    // Just clean up the task context so the next run starts fresh.
    clearTaskContext(tmuxSession)
    log("TASK: cleaned up task context")
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

  // Push progress to portal on every status transition
  const tmuxSession = getTmuxSessionName()
  if (tmuxSession && statusType) {
    postProgress(tmuxSession, state, statusType)
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

    const tmuxSession = getTmuxSessionName()
    if (tmuxSession) {
      postProgress(tmuxSession, state, "busy")
    }
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

    const tmuxSession = getTmuxSessionName()
    if (tmuxSession) {
      postProgress(tmuxSession, state, "busy")
    }
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
