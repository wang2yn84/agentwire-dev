/**
 * Scheduler Window - dashboard panel for the scheduler daemon
 *
 * Custom window (not ListWindow) because the scheduler panel is a multi-section
 * dashboard (status header + task board + event timeline), not a flat item list.
 *
 * Board layout: two sections with the active task in the middle.
 * - "Up Next": tasks due to fire, sorted so next-to-fire is at the bottom (closest to active)
 * - "Active": currently running task, highlighted in the center
 * - "Completed": recently fired tasks, sorted so most recent is at the top (closest to active)
 */

import { desktop } from '../desktop-manager.js';

/** @type {Object|null} */
let schedulerWindow = null;

/** Cached board data for client-side filtering */
let cachedBoard = [];

/** Cached live state for active task detection */
let cachedLiveState = null;

/** Polling interval handle */
let pollInterval = null;

/** WebSocket listener cleanup */
let wsCleanup = null;

/**
 * Open the Scheduler window.
 * Returns an object matching the interface openListWindowWithTaskbar expects.
 */
export function openSchedulerWindow() {
    if (schedulerWindow?.winbox) {
        schedulerWindow.winbox.focus();
        return schedulerWindow;
    }

    const container = buildContainer();
    const root = document.getElementById('desktopArea') || document.body;

    const winbox = new WinBox({
        title: 'Scheduler',
        icon: '/static/favicon.png',
        mount: container,
        root,
        width: '100%',
        height: '100%',
        minwidth: 300,
        minheight: 200,
        class: ['list-window-box', 'no-full', 'no-resize', 'no-move'],
        onclose: () => {
            cleanup();
            if (schedulerWindow?._cleanup) schedulerWindow._cleanup();
        },
        onfocus: () => desktop.setActiveWindow('scheduler'),
        onmaximize: () => desktop.setActiveWindow('scheduler'),
        onminimize: () => desktop.emit('window_minimized', { id: 'scheduler' }),
        onrestore: () => {
            desktop.emit('window_restored', { id: 'scheduler' });
            desktop.setActiveWindow('scheduler');
        },
    });

    winbox.maximize();
    desktop.registerWindow('scheduler', winbox);

    // Listen for WebSocket scheduler updates
    wsCleanup = desktop.on('scheduler_update', () => refreshAll(container));

    // Poll all sections every 10s (live state, board, events)
    pollInterval = setInterval(() => refreshAll(container), 10000);

    // Initial fetch
    refreshAll(container);

    schedulerWindow = {
        winbox,
        title: 'Scheduler',
        get isMinimized() { return winbox ? winbox.min : false; },
        minimize() { if (winbox) winbox.minimize(); },
        restore() { if (winbox) winbox.restore(); },
        close() { if (winbox) winbox.close(); },
        _cleanup: () => { schedulerWindow = null; },
    };

    return schedulerWindow;
}

function cleanup() {
    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
    if (wsCleanup) { wsCleanup(); wsCleanup = null; }
    desktop.unregisterWindow('scheduler');
    cachedBoard = [];
    cachedLiveState = null;
}

// ============================================
// Container Layout
// ============================================

function buildContainer() {
    const el = document.createElement('div');
    el.className = 'scheduler-panel';
    el.innerHTML = `
        <div class="sched-status-header">
            <div class="sched-status-left">
                <span class="sched-status-dot not-running"></span>
                <span class="sched-status-label">Not Running</span>
                <span class="sched-uptime"></span>
            </div>
            <div class="sched-status-center">
                <span class="sched-current-task"></span>
                <div class="sched-progress-bar"><div class="sched-progress-fill"></div></div>
            </div>
            <div class="sched-status-right">
                <span class="sched-stat"><span class="sched-stat-value sched-completed">0</span> completed</span>
                <span class="sched-stat"><span class="sched-stat-value sched-failed">0</span> failed</span>
                <span class="sched-stat sched-next-stat"><span class="sched-stat-label">Next:</span> <span class="sched-next-task">—</span></span>
            </div>
        </div>
        <div class="sched-board-section">
            <div class="sched-board-toolbar">
                <input type="text" class="sched-search" placeholder="Filter tasks..." />
                <button class="sched-refresh-btn" title="Refresh">↻</button>
            </div>
            <div class="sched-board-table-wrap">
                <div class="sched-board-content"></div>
            </div>
        </div>
        <div class="sched-events-section collapsed">
            <div class="sched-events-header">
                <span class="sched-events-chevron">&#9660;</span>
                <span>Events</span>
                <span class="sched-events-badge">0</span>
            </div>
            <div class="sched-events-body"></div>
        </div>
    `;

    // Wire interactions
    el.querySelector('.sched-search').addEventListener('input', (e) => {
        filterBoard(el, e.target.value);
    });
    el.querySelector('.sched-refresh-btn').addEventListener('click', () => refreshAll(el));
    el.querySelector('.sched-events-header').addEventListener('click', () => {
        el.querySelector('.sched-events-section').classList.toggle('collapsed');
    });

    return el;
}

// ============================================
// Data Fetching
// ============================================

async function refreshAll(container) {
    await Promise.all([
        refreshLiveState(container),
        refreshBoard(container),
        refreshEvents(container),
    ]);
}

async function refreshLiveState(container) {
    try {
        const res = await fetch('/api/scheduler/live');
        if (res.status === 404) {
            cachedLiveState = null;
            renderStatusNotRunning(container);
            return;
        }
        const data = await res.json();
        cachedLiveState = data;
        renderStatusHeader(container, data);
    } catch {
        cachedLiveState = null;
        renderStatusNotRunning(container);
    }
}

async function refreshBoard(container) {
    try {
        const res = await fetch('/api/scheduler/board');
        const data = await res.json();
        cachedBoard = data.tasks || [];
        renderBoard(container, cachedBoard);
    } catch (err) {
        console.error('[Scheduler] Board fetch failed:', err);
        container.querySelector('.sched-board-content').innerHTML =
            '<div class="sched-empty">Failed to load board</div>';
    }
}

async function refreshEvents(container) {
    try {
        const res = await fetch('/api/scheduler/events?tail=30');
        const data = await res.json();
        renderEvents(container, data.events || []);
    } catch (err) {
        console.error('[Scheduler] Events fetch failed:', err);
    }
}

// ============================================
// Rendering: Status Header
// ============================================

function renderStatusNotRunning(container) {
    const dot = container.querySelector('.sched-status-dot');
    dot.className = 'sched-status-dot not-running';
    container.querySelector('.sched-status-label').textContent = 'Not Running';
    container.querySelector('.sched-uptime').textContent = '';
    container.querySelector('.sched-current-task').textContent = '';
    container.querySelector('.sched-progress-bar').style.display = 'none';
    container.querySelector('.sched-completed').textContent = '0';
    container.querySelector('.sched-failed').textContent = '0';
    container.querySelector('.sched-next-task').textContent = '—';
}

function renderStatusHeader(container, state) {
    const dot = container.querySelector('.sched-status-dot');
    const isExecuting = !!state.current_task;

    dot.className = 'sched-status-dot ' + (isExecuting ? 'executing' : 'running');
    container.querySelector('.sched-status-label').textContent = isExecuting ? 'Executing' : 'Running';

    // Uptime
    if (state.uptime_seconds != null) {
        container.querySelector('.sched-uptime').textContent = formatDuration(state.uptime_seconds);
    }

    // Current task — find session from board data and make clickable
    const currentEl = container.querySelector('.sched-current-task');
    const progressBar = container.querySelector('.sched-progress-bar');
    if (state.current_task) {
        const taskData = cachedBoard.find(t => t.name === state.current_task);
        const session = state.current_session || taskData?.session;
        if (session) {
            currentEl.innerHTML = `<a class="sched-current-link" title="Open ${escapeHtml(session)}">${escapeHtml(state.current_task)}</a>`;
            currentEl.querySelector('.sched-current-link').addEventListener('click', () => openSession(session));
        } else {
            currentEl.textContent = state.current_task;
        }
        progressBar.style.display = 'block';
    } else {
        currentEl.textContent = '';
        progressBar.style.display = 'none';
    }

    // Stats
    container.querySelector('.sched-completed').textContent = state.tasks_completed ?? 0;
    container.querySelector('.sched-failed').textContent = state.tasks_failed ?? 0;

    // Next task
    if (state.next_task) {
        const countdown = state.next_in_seconds > 0 ? ` (${formatDuration(state.next_in_seconds)})` : '';
        container.querySelector('.sched-next-task').textContent = state.next_task + countdown;
    } else {
        container.querySelector('.sched-next-task').textContent = '—';
    }
}

// ============================================
// Rendering: Task Board (Two-Section Layout)
// ============================================

function renderBoard(container, tasks) {
    const content = container.querySelector('.sched-board-content');
    if (!tasks.length) {
        content.innerHTML = '<div class="sched-empty">No tasks configured</div>';
        return;
    }

    const currentTask = cachedLiveState?.current_task || null;

    // Split tasks into three groups
    const activeTask = currentTask ? tasks.find(t => t.name === currentTask) : null;
    const otherTasks = tasks.filter(t => t.name !== currentTask);

    // "Up Next": overdue (overdue_by > 0) or never run — these are due to fire
    // "Completed": not overdue (overdue_by <= 0) and have run before
    const upNext = otherTasks.filter(t => t.overdue_by > 0 || t.last_status === 'never');
    const completed = otherTasks.filter(t => t.overdue_by <= 0 && t.last_status !== 'never');

    // Sort "Up Next": most overdue at bottom (closest to active) = ascending by overdue
    // So the task that's about to fire is at the bottom of the list
    upNext.sort((a, b) => a.overdue_by - b.overdue_by);

    // Sort "Completed": most recently run at top (closest to active)
    // last_run_iso gives us the ISO timestamp to sort by
    completed.sort((a, b) => {
        const ta = a.last_run_iso ? new Date(a.last_run_iso).getTime() : 0;
        const tb = b.last_run_iso ? new Date(b.last_run_iso).getTime() : 0;
        return tb - ta; // newest first
    });

    const tableHead = `
        <thead>
            <tr>
                <th class="sched-col-toggle"></th>
                <th>Task</th>
                <th>Session</th>
                <th>Interval</th>
                <th>Last Run</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Overdue</th>
                <th>Runs</th>
                <th>Actions</th>
            </tr>
        </thead>
    `;

    let html = '';

    // Up Next section
    if (upNext.length > 0) {
        html += `
            <div class="sched-section">
                <div class="sched-section-label sched-section-upnext">Up Next <span class="sched-section-count">${upNext.length}</span></div>
                <table class="sched-board-table">${tableHead}<tbody>${upNext.map(t => renderBoardRow(t, false)).join('')}</tbody></table>
            </div>
        `;
    }

    // Active task section
    if (activeTask) {
        html += `
            <div class="sched-section sched-section-active-wrap">
                <div class="sched-section-label sched-section-running">Running</div>
                <table class="sched-board-table">${tableHead}<tbody>${renderBoardRow(activeTask, true)}</tbody></table>
            </div>
        `;
    }

    // Completed section
    if (completed.length > 0) {
        html += `
            <div class="sched-section">
                <div class="sched-section-label sched-section-completed">Completed <span class="sched-section-count">${completed.length}</span></div>
                <table class="sched-board-table">${tableHead}<tbody>${completed.map(t => renderBoardRow(t, false)).join('')}</tbody></table>
            </div>
        `;
    }

    if (!html) {
        html = '<div class="sched-empty">No tasks configured</div>';
    }

    content.innerHTML = html;
    wireRowActions(container);
}

function renderBoardRow(task, isActive) {
    const statusClass = getStatusClass(task.last_status);
    const rowClass = [
        'sched-row',
        isActive ? 'sched-row-active' : '',
        task.enabled ? '' : 'sched-row-disabled',
        task.filler ? 'sched-row-filler' : '',
    ].filter(Boolean).join(' ');

    const toggleDotClass = task.enabled ? 'sched-toggle-dot enabled' : 'sched-toggle-dot disabled';

    const statusBadge = isActive
        ? '<span class="sched-badge sched-badge-running">running</span>'
        : (task.last_status
            ? `<span class="sched-badge sched-badge-${statusClass}">${task.last_status}</span>`
            : '<span class="sched-badge sched-badge-never">never</span>');

    const durationStr = task.last_duration != null ? `${task.last_duration}s` : '—';
    const overdueStr = task.overdue_str || '—';
    const fillerTag = task.filler ? ' <span class="sched-filler-tag">(filler)</span>' : '';

    return `
        <tr class="${rowClass}" data-task="${task.name}">
            <td><span class="${toggleDotClass}" data-action="toggle" data-task="${task.name}" data-enabled="${task.enabled}" title="${task.enabled ? 'Disable' : 'Enable'}"></span></td>
            <td class="sched-task-name">${escapeHtml(task.name)}${fillerTag}</td>
            <td class="sched-task-session">${task.session ? `<a class="sched-session-link" data-action="open-session" data-session="${escapeHtml(task.session)}" title="Open session">${escapeHtml(task.session)}</a>` : '—'}</td>
            <td>${task.interval_str || '—'}</td>
            <td>${task.last_run && task.last_run !== 'never' && task.last_summary ? `<a class="sched-session-link" data-action="show-summary" data-task="${task.name}" title="View summary">${escapeHtml(task.last_run)}</a>` : escapeHtml(task.last_run || 'never')}</td>
            <td>${statusBadge}</td>
            <td>${durationStr}</td>
            <td class="sched-overdue">${overdueStr}</td>
            <td>${task.run_count ?? 0}</td>
            <td class="sched-actions">
                <button class="sched-btn sched-btn-run" data-action="run" data-task="${task.name}" title="Force run">Run</button>
                <button class="sched-btn sched-btn-view" data-action="view" data-task="${task.name}" title="View details">View</button>
            </td>
        </tr>
    `;
}

function wireRowActions(container) {
    // Use event delegation on the board content area
    const content = container.querySelector('.sched-board-content');
    // Remove old listener by replacing the node (simple approach since we rebuild content)
    const clone = content.cloneNode(true);
    content.parentNode.replaceChild(clone, content);

    clone.addEventListener('click', async (e) => {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        const taskName = target.dataset.task;

        if (action === 'toggle') {
            const isEnabled = target.dataset.enabled === 'true';
            await toggleTask(taskName, !isEnabled, container);
        } else if (action === 'run') {
            await forceRunTask(taskName, target, container);
        } else if (action === 'view') {
            const task = cachedBoard.find(t => t.name === taskName);
            if (task) showTaskDetailModal(task);
        } else if (action === 'open-session') {
            const session = target.dataset.session;
            if (session) openSession(session);
        } else if (action === 'show-summary') {
            const task = cachedBoard.find(t => t.name === taskName);
            if (task?.last_summary) showSummaryModal(task);
        }
    });
}

function filterBoard(container, query) {
    const q = query.toLowerCase().trim();
    const filtered = q ? cachedBoard.filter(t => t.name.toLowerCase().includes(q)) : cachedBoard;
    renderBoard(container, filtered);
}

// ============================================
// Rendering: Events Timeline
// ============================================

function renderEvents(container, events) {
    const badge = container.querySelector('.sched-events-badge');
    badge.textContent = events.length;

    const body = container.querySelector('.sched-events-body');
    if (!events.length) {
        body.innerHTML = '<div class="sched-empty">No events</div>';
        return;
    }

    // Newest first
    const reversed = [...events].reverse();
    body.innerHTML = reversed.map(renderEventRow).join('');
}

function renderEventRow(evt) {
    const typeClass = getEventTypeClass(evt.event || evt.type);
    const ts = evt.timestamp ? formatTimestamp(evt.timestamp) : '';
    const eventType = evt.event || evt.type || '?';
    const taskName = evt.task || '';
    const detail = formatEventDetail(evt);

    return `
        <div class="sched-event-row">
            <span class="sched-event-ts">${ts}</span>
            <span class="sched-event-type sched-evt-${typeClass}">${eventType}</span>
            <span class="sched-event-task">${escapeHtml(taskName)}</span>
            <span class="sched-event-detail">${escapeHtml(detail)}</span>
        </div>
    `;
}

function formatEventDetail(evt) {
    const eventType = evt.event || evt.type || '';
    if (eventType === 'task_completed' || eventType === 'task_complete') {
        const parts = [];
        if (evt.status) parts.push(evt.status);
        if (evt.duration) parts.push(`${evt.duration}s`);
        if (evt.summary) parts.push(evt.summary);
        return parts.join(' — ');
    }
    if (eventType === 'task_skipped') return evt.reason || '';
    if (eventType === 'scheduler_sleeping') return evt.next_task ? `next: ${evt.next_task}` : '';
    if (evt.detail) return evt.detail;
    return '';
}

// ============================================
// Task Actions
// ============================================

async function toggleTask(name, enable, container) {
    const action = enable ? 'enable' : 'disable';
    try {
        const res = await fetch(`/api/scheduler/tasks/${encodeURIComponent(name)}/${action}`, { method: 'POST' });
        if (res.ok) await refreshBoard(container);
    } catch (err) {
        console.error(`[Scheduler] Toggle failed:`, err);
    }
}

async function forceRunTask(name, btn, container) {
    btn.disabled = true;
    btn.textContent = '...';
    try {
        await fetch(`/api/scheduler/tasks/${encodeURIComponent(name)}/run`, { method: 'POST' });
        // Don't refresh immediately — WebSocket scheduler_update will trigger it
        // Reset button after 2s
        setTimeout(() => {
            btn.disabled = false;
            btn.textContent = 'Run';
        }, 2000);
    } catch (err) {
        console.error(`[Scheduler] Force run failed:`, err);
        btn.disabled = false;
        btn.textContent = 'Run';
    }
}

// ============================================
// Task Detail Modal
// ============================================

function showTaskDetailModal(task) {
    // Remove existing
    document.querySelector('.sched-detail-modal')?.remove();

    const statusBadge = task.last_status
        ? `<span class="sched-badge sched-badge-${getStatusClass(task.last_status)}">${task.last_status}</span>`
        : '<span class="sched-badge sched-badge-never">never</span>';

    const modal = document.createElement('div');
    modal.className = 'modal-overlay sched-detail-modal';
    modal.innerHTML = `
        <div class="modal sched-modal">
            <div class="modal-header">
                <h3>${escapeHtml(task.name)}</h3>
                <button class="modal-close" data-action="close">&#10005;</button>
            </div>
            <div class="modal-body">
                <table class="sched-detail-table">
                    <tr><td>Session</td><td>${escapeHtml(task.session || '—')}</td></tr>
                    <tr><td>Project</td><td>${escapeHtml(task.project || '—')}</td></tr>
                    <tr><td>Task</td><td>${escapeHtml(task.task || '—')}</td></tr>
                    <tr><td>Interval</td><td>${task.interval_str || '—'} (${task.interval}s)</td></tr>
                    <tr><td>Priority</td><td>${task.priority ?? '—'}</td></tr>
                    <tr><td>Filler</td><td>${task.filler ? 'Yes' : 'No'}</td></tr>
                    <tr><td>Enabled</td><td>${task.enabled ? 'Yes' : 'No'}</td></tr>
                    <tr><td>Last Run</td><td>${escapeHtml(task.last_run || 'never')}</td></tr>
                    <tr><td>Status</td><td>${statusBadge}</td></tr>
                    <tr><td>Duration</td><td>${task.last_duration != null ? task.last_duration + 's' : '—'}</td></tr>
                    <tr><td>Runs</td><td>${task.run_count ?? 0}</td></tr>
                    <tr><td>Overdue</td><td>${task.overdue_str || '—'}</td></tr>
                </table>
                ${task.last_summary ? `<div class="sched-detail-summary"><strong>Last Summary:</strong><pre>${escapeHtml(task.last_summary)}</pre></div>` : ''}
            </div>
        </div>
    `;

    document.body.appendChild(modal);

    modal.addEventListener('click', (e) => {
        if (e.target === modal || e.target.dataset.action === 'close') modal.remove();
    });
}

// ============================================
// Summary Modal
// ============================================

function showSummaryModal(task) {
    document.querySelector('.sched-summary-modal')?.remove();

    const statusBadge = task.last_status
        ? `<span class="sched-badge sched-badge-${getStatusClass(task.last_status)}">${task.last_status}</span>`
        : '';

    const modal = document.createElement('div');
    modal.className = 'modal-overlay sched-summary-modal';
    modal.innerHTML = `
        <div class="modal sched-modal">
            <div class="modal-header">
                <h3>${escapeHtml(task.name)} — ${escapeHtml(task.last_run)}</h3>
                <button class="modal-close" data-action="close">&#10005;</button>
            </div>
            <div class="modal-body">
                <div style="margin-bottom: 10px;">${statusBadge} ${task.last_duration != null ? `<span style="color:var(--text-muted);font-size:12px;margin-left:8px;">${task.last_duration}s</span>` : ''}</div>
                <pre class="sched-summary-pre">${escapeHtml(task.last_summary)}</pre>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => {
        if (e.target === modal || e.target.dataset.action === 'close') modal.remove();
    });
}

// ============================================
// Session Navigation
// ============================================

async function openSession(session) {
    const { openSessionTerminal } = await import('../desktop.js');
    openSessionTerminal(session, 'monitor');
}

// ============================================
// Helpers
// ============================================

function getStatusClass(status) {
    if (!status) return 'never';
    switch (status) {
        case 'complete': return 'complete';
        case 'failed': return 'failed';
        case 'timeout': return 'timeout';
        case 'incomplete': return 'incomplete';
        case 'lock_conflict': return 'lock';
        default: return 'never';
    }
}

function getEventTypeClass(type) {
    if (type?.includes('completed') || type?.includes('complete')) return 'complete';
    if (type?.includes('started')) return 'started';
    if (type?.includes('skipped')) return 'skipped';
    if (type?.includes('sleeping')) return 'sleeping';
    if (type?.includes('scheduler_started')) return 'started';
    return 'default';
}

function formatDuration(seconds) {
    if (seconds == null) return '';
    const s = Math.floor(seconds);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${m}m`;
}

function formatTimestamp(ts) {
    try {
        const d = new Date(ts);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
        return ts;
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
