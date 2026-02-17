/**
 * Scheduler Window v2 — three-tab layout with WebSocket push
 *
 * Tabs:
 *   Queue   — what's happening now + what's coming next
 *   History — timeline of completed runs (click to expand)
 *   Tasks   — full admin table with toggle/run/view
 *
 * Task drill-down: click any task name → side panel with config + run history
 */

import { desktop } from '../desktop-manager.js';

/** @type {Object|null} */
let schedulerWindow = null;

/** Cached data */
let cachedBoard = [];
let cachedLiveState = null;
let cachedEvents = [];

/** Active tab */
let activeTab = 'queue';

/** Active drill-down task */
let drilldownTask = null;

/** History filters */
let historyStatusFilter = 'all';
let historySearchQuery = '';

/** Tasks tab search */
let tasksSearchQuery = '';

/** Whether scheduler daemon is running */
let schedulerRunning = false;

/** WebSocket listener cleanups */
let wsCleanups = [];

/** Agent progress state per session */
let agentProgress = {};

/** Output preview state */
let outputExpanded = false;
let expandedSession = null;
let elapsedTimer = null;
let outputRefreshTimer = null;
let idleTimer = null;

/**
 * Open the Scheduler window.
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

    // WebSocket listeners — no polling
    wsCleanups.push(desktop.on('scheduler_state', (data) => {
        cachedLiveState = data;
        renderStatusHeader(container, data);
        if (activeTab === 'queue') renderQueueTab(container);
    }));
    wsCleanups.push(desktop.on('scheduler_update', (data) => {
        // Task completed — clear progress for that session, stop timers
        if (data.session) delete agentProgress[data.session];
        stopElapsedTimer();
        stopHeaderElapsedTimer();
        stopOutputRefresh();
        outputExpanded = false;
        expandedSession = null;
        refreshAll(container);
    }));
    wsCleanups.push(desktop.on('agent_progress', (data) => {
        if (data.session) {
            agentProgress[data.session] = data;
            updateActiveCardProgress(container);
        }
    }));

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
    wsCleanups.forEach(fn => fn());
    wsCleanups = [];
    boundClickHandler = null;
    desktop.unregisterWindow('scheduler');
    cachedBoard = [];
    cachedLiveState = null;
    cachedEvents = [];
    drilldownTask = null;
    activeTab = 'queue';
    agentProgress = {};
    outputExpanded = false;
    expandedSession = null;
    stopElapsedTimer();
    stopOutputRefresh();
    stopIdleTimer();
    stopHeaderElapsedTimer();
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
                <span class="sched-status-label">Stopped</span>
                <span class="sched-uptime"></span>
            </div>
            <div class="sched-status-center">
                <span class="sched-current-activity"></span>
                <div class="sched-progress-bar"><div class="sched-progress-fill"></div></div>
            </div>
            <div class="sched-status-right">
                <span class="sched-stat">\u2713 <span class="sched-stat-value sched-completed">0</span></span>
                <span class="sched-stat">\u2717 <span class="sched-stat-value sched-failed">0</span></span>
                <button class="sched-btn sched-power-btn" title="Start scheduler">Start</button>
            </div>
        </div>
        <div class="sched-tabs">
            <button class="sched-tab active" data-tab="queue">Queue</button>
            <button class="sched-tab" data-tab="history">History</button>
            <button class="sched-tab" data-tab="tasks">Tasks</button>
            <div class="sched-tabs-right">
                <button class="sched-refresh-btn" title="Refresh">\u21BB</button>
            </div>
        </div>
        <div class="sched-body">
            <div class="sched-tab-content"></div>
            <div class="sched-drilldown"></div>
        </div>
    `;

    // Tab switching
    el.querySelectorAll('.sched-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            activeTab = btn.dataset.tab;
            el.querySelectorAll('.sched-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === activeTab));
            renderActiveTab(el);
        });
    });

    // Refresh button
    el.querySelector('.sched-refresh-btn').addEventListener('click', () => refreshAll(el));

    // Power button (start/stop)
    el.querySelector('.sched-power-btn').addEventListener('click', () => toggleScheduler(el));

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
    renderActiveTab(container);
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
    } catch (err) {
        console.error('[Scheduler] Board fetch failed:', err);
    }
}

async function refreshEvents(container) {
    try {
        const res = await fetch('/api/scheduler/events?tail=200');
        const data = await res.json();
        cachedEvents = data.events || [];
    } catch (err) {
        console.error('[Scheduler] Events fetch failed:', err);
    }
}

function renderActiveTab(container) {
    if (activeTab === 'queue') renderQueueTab(container);
    else if (activeTab === 'history') renderHistoryTab(container);
    else if (activeTab === 'tasks') renderTasksTab(container);
}

// ============================================
// Status Header
// ============================================

function renderStatusNotRunning(container) {
    schedulerRunning = false;
    stopIdleTimer();
    const dot = container.querySelector('.sched-status-dot');
    dot.className = 'sched-status-dot not-running';
    container.querySelector('.sched-status-label').textContent = 'Stopped';
    container.querySelector('.sched-uptime').textContent = '';
    container.querySelector('.sched-current-activity').textContent = '';
    container.querySelector('.sched-progress-bar').style.display = 'none';
    container.querySelector('.sched-completed').textContent = '0';
    container.querySelector('.sched-failed').textContent = '0';

    const btn = container.querySelector('.sched-power-btn');
    btn.textContent = 'Start';
    btn.className = 'sched-btn sched-power-btn sched-power-start';
    btn.disabled = false;
}

function renderStatusHeader(container, state) {
    schedulerRunning = true;
    const dot = container.querySelector('.sched-status-dot');
    const isExecuting = !!state.current_task;

    // Left: dot + label + uptime
    dot.className = 'sched-status-dot ' + (isExecuting ? 'executing' : 'running');
    container.querySelector('.sched-status-label').textContent = 'Running';
    const uptimeEl = container.querySelector('.sched-uptime');
    if (state.uptime_seconds != null) {
        uptimeEl.textContent = formatDuration(state.uptime_seconds);
    }

    // Center: dynamic activity
    const activityEl = container.querySelector('.sched-current-activity');
    const progressBar = container.querySelector('.sched-progress-bar');

    if (state.current_task) {
        // Executing: task name (clickable) + elapsed timer + progress bar
        stopIdleTimer();
        const taskData = cachedBoard.find(t => t.name === state.current_task);
        const session = state.current_session || taskData?.session;
        const elapsed = state.current_task_started
            ? Math.floor((Date.now() - new Date(state.current_task_started).getTime()) / 1000)
            : 0;
        if (session) {
            activityEl.innerHTML = `<a class="sched-current-link" title="Open ${escapeHtml(session)}">${escapeHtml(state.current_task)}</a> <span class="sched-activity-elapsed" data-started="${state.current_task_started || ''}">${formatDuration(elapsed)}</span>`;
            activityEl.querySelector('.sched-current-link').addEventListener('click', () => openSession(session));
        } else {
            activityEl.innerHTML = `${escapeHtml(state.current_task)} <span class="sched-activity-elapsed" data-started="${state.current_task_started || ''}">${formatDuration(elapsed)}</span>`;
        }
        progressBar.style.display = 'block';
        startHeaderElapsedTimer(container);
    } else if (state.next_task && state.next_in_seconds > 0) {
        // Idle with upcoming task: "Idle — next: task in countdown"
        stopHeaderElapsedTimer();
        progressBar.style.display = 'none';
        const nextDue = Date.now() + state.next_in_seconds * 1000;
        activityEl.innerHTML = `Idle \u2014 next: ${escapeHtml(state.next_task)} in <span class="sched-activity-countdown" data-due="${nextDue}">${formatDuration(state.next_in_seconds)}</span>`;
        startIdleTimer(container);
    } else {
        // Idle with nothing due
        stopIdleTimer();
        stopHeaderElapsedTimer();
        progressBar.style.display = 'none';
        activityEl.textContent = 'Idle \u2014 all caught up';
    }

    // Right: stats + stop button
    container.querySelector('.sched-completed').textContent = state.tasks_completed ?? 0;
    container.querySelector('.sched-failed').textContent = state.tasks_failed ?? 0;

    const btn = container.querySelector('.sched-power-btn');
    btn.textContent = 'Stop';
    btn.className = 'sched-btn sched-power-btn sched-power-stop';
    btn.disabled = false;
}

async function toggleScheduler(container) {
    const btn = container.querySelector('.sched-power-btn');
    btn.disabled = true;
    btn.textContent = '...';

    try {
        const endpoint = schedulerRunning ? '/api/scheduler/stop' : '/api/scheduler/start';
        await fetch(endpoint, { method: 'POST' });
        // Wait a beat for state to settle, then refresh
        await new Promise(r => setTimeout(r, 1500));
        await refreshAll(container);
    } catch (err) {
        console.error('[Scheduler] Toggle failed:', err);
        btn.disabled = false;
        btn.textContent = schedulerRunning ? 'Stop' : 'Start';
    }
}

// ============================================
// Queue Tab
// ============================================

function renderQueueTab(container) {
    const content = container.querySelector('.sched-tab-content');
    const currentTask = cachedLiveState?.current_task || null;

    // Active task
    const activeTask = currentTask ? cachedBoard.find(t => t.name === currentTask) : null;

    // Up Next: overdue or never run, sorted by when they'll fire
    const upNext = cachedBoard
        .filter(t => t.enabled && t.name !== currentTask && (t.overdue_by > 0 || t.last_status === 'never'))
        .sort((a, b) => b.overdue_by - a.overdue_by);

    // Coming later: enabled tasks not overdue
    const later = cachedBoard
        .filter(t => t.enabled && t.name !== currentTask && t.overdue_by <= 0 && t.last_status !== 'never')
        .sort((a, b) => b.overdue_by - a.overdue_by);  // least negative (soonest) first

    let html = '';

    // Active section
    if (activeTask) {
        const elapsed = cachedLiveState?.current_task_started
            ? Math.floor((Date.now() - new Date(cachedLiveState.current_task_started).getTime()) / 1000)
            : 0;
        const session = activeTask.session || '';
        const progress = agentProgress[session];
        const statusBadge = progress ? getAgentStatusBadge(progress.status) : '';
        const respCount = progress?.responses > 0 ? `<span class="sched-agent-responses">${progress.responses} resp</span>` : '';
        const diffIcon = progress?.has_diffs ? '<span class="sched-diff-indicator" title="Files changed">\u270E</span>' : '';
        const expandIcon = outputExpanded && expandedSession === session ? '\u25B2' : '\u25BC';

        html += `
            <div class="sched-queue-section sched-queue-active">
                <div class="sched-queue-section-label sched-section-running">Running</div>
                <div class="sched-queue-active-card">
                    <div class="sched-active-card-row">
                        <span class="sched-queue-task-name sched-clickable" data-action="drilldown" data-task="${activeTask.name}">${escapeHtml(activeTask.name)}</span>
                        <span class="sched-queue-session">${session ? `<a class="sched-session-link" data-action="open-session" data-session="${escapeHtml(session)}">${escapeHtml(session)}</a>` : ''}</span>
                        <span class="sched-queue-elapsed" data-started="${cachedLiveState?.current_task_started || ''}">${formatDuration(elapsed)}</span>
                        <div class="sched-agent-stats">
                            ${statusBadge}
                            ${respCount}
                            ${diffIcon}
                        </div>
                        <div class="sched-progress-bar" style="display:block;width:80px;"><div class="sched-progress-fill"></div></div>
                        ${session ? `<button class="sched-expand-btn" data-action="toggle-output" data-session="${escapeHtml(session)}" title="Toggle output preview">${expandIcon}</button>` : ''}
                    </div>
                    <div class="sched-active-output" style="display:${outputExpanded && expandedSession === session ? 'block' : 'none'};">
                        <pre class="sched-output-pre">${outputExpanded && expandedSession === session ? 'Loading...' : ''}</pre>
                    </div>
                </div>
            </div>
        `;

        // Start elapsed timer
        startElapsedTimer(container);

        // If output is expanded, load it
        if (outputExpanded && expandedSession === session) {
            fetchAndShowOutput(container, session);
            startOutputRefresh(container, session);
        }
    } else {
        stopElapsedTimer();
        stopOutputRefresh();
    }

    // Up Next section
    if (upNext.length > 0) {
        html += `
            <div class="sched-queue-section">
                <div class="sched-queue-section-label sched-section-upnext">Due Now <span class="sched-section-count">${upNext.length}</span></div>
                <div class="sched-queue-list">
                    ${upNext.map(t => renderQueueRow(t, 'overdue')).join('')}
                </div>
            </div>
        `;
    }

    // Coming later
    if (later.length > 0) {
        html += `
            <div class="sched-queue-section">
                <div class="sched-queue-section-label">Up Next <span class="sched-section-count">${later.length}</span></div>
                <div class="sched-queue-list">
                    ${later.map(t => renderQueueRow(t, 'upcoming')).join('')}
                </div>
            </div>
        `;
    }

    if (!html) {
        if (schedulerRunning) {
            html = '<div class="sched-empty sched-idle-state">All caught up \u2014 no overdue jobs</div>';
        } else {
            html = '<div class="sched-empty">Scheduler stopped</div>';
        }
    }

    content.innerHTML = html;
    wireContentActions(container);
}

function renderQueueRow(task, mode) {
    const timeStr = formatDuration(Math.abs(task.overdue_by));
    const fillerTag = task.filler ? ' <span class="sched-filler-tag">(filler)</span>' : '';

    return `
        <div class="sched-queue-row" data-task="${task.name}">
            <span class="sched-queue-task-name sched-clickable" data-action="drilldown" data-task="${task.name}">${escapeHtml(task.name)}${fillerTag}</span>
            <span class="sched-queue-time ${mode === 'overdue' ? 'sched-queue-overdue' : ''}">${timeStr}</span>
            <span class="sched-queue-session-small">${escapeHtml(task.session || '')}</span>
        </div>
    `;
}

// ============================================
// History Tab
// ============================================

function renderHistoryTab(container) {
    const content = container.querySelector('.sched-tab-content');

    // Filter events to task_completed only
    let events = cachedEvents.filter(e => (e.event || e.type) === 'task_completed');

    // Apply status filter
    if (historyStatusFilter !== 'all') {
        events = events.filter(e => e.status === historyStatusFilter);
    }

    // Apply search filter
    if (historySearchQuery) {
        const q = historySearchQuery.toLowerCase();
        events = events.filter(e => (e.task || '').toLowerCase().includes(q));
    }

    // Newest first
    events = [...events].reverse();

    let html = `
        <div class="sched-history-toolbar">
            <input type="text" class="sched-search sched-history-search" placeholder="Filter by task..." value="${escapeHtml(historySearchQuery)}" />
            <div class="sched-history-filters">
                ${['all', 'complete', 'failed', 'timeout'].map(s =>
                    `<button class="sched-filter-btn ${historyStatusFilter === s ? 'active' : ''}" data-filter="${s}">${s}</button>`
                ).join('')}
            </div>
        </div>
    `;

    if (events.length === 0) {
        html += '<div class="sched-empty">No matching events</div>';
    } else {
        html += '<div class="sched-history-list">';
        events.forEach((evt, i) => {
            const tsDate = evt.ts ? formatDate(evt.ts) : '';
            const tsTime = evt.ts ? formatTimestamp(evt.ts) : '';
            const ts = tsDate ? `${tsDate} ${tsTime}` : tsTime;
            const statusClass = getStatusClass(evt.status);
            const durationStr = evt.duration ? `${evt.duration}s` : '';
            const summaryPreview = evt.summary ? evt.summary.substring(0, 120) + (evt.summary.length > 120 ? '...' : '') : '';

            html += `
                <div class="sched-history-row" data-index="${i}">
                    <div class="sched-history-row-main">
                        <span class="sched-history-ts">${ts}</span>
                        <span class="sched-history-task sched-clickable" data-action="drilldown" data-task="${escapeHtml(evt.task || '')}">${escapeHtml(evt.task || '')}</span>
                        <span class="sched-badge sched-badge-${statusClass}">${evt.status || 'unknown'}</span>
                        <span class="sched-history-duration">${durationStr}</span>
                        <span class="sched-history-preview">${escapeHtml(summaryPreview)}</span>
                        <span class="sched-history-expand" data-action="expand-history" data-index="${i}">${evt.summary ? '\u25B6' : ''}</span>
                    </div>
                    <div class="sched-history-detail" style="display:none;">
                        <pre class="sched-summary-pre">${escapeHtml(evt.summary || 'No summary')}</pre>
                    </div>
                </div>
            `;
        });
        html += '</div>';
    }

    content.innerHTML = html;

    // Wire filter buttons
    content.querySelectorAll('.sched-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            historyStatusFilter = btn.dataset.filter;
            renderHistoryTab(container);
        });
    });

    // Wire search
    const searchInput = content.querySelector('.sched-history-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            historySearchQuery = e.target.value;
            renderHistoryTab(container);
        });
    }

    // Wire expand
    content.querySelectorAll('[data-action="expand-history"]').forEach(btn => {
        btn.addEventListener('click', () => {
            const row = btn.closest('.sched-history-row');
            const detail = row.querySelector('.sched-history-detail');
            const isOpen = detail.style.display !== 'none';
            detail.style.display = isOpen ? 'none' : 'block';
            btn.textContent = isOpen ? '\u25B6' : '\u25BC';
        });
    });

    wireContentActions(container);
}

// ============================================
// Tasks Tab (Admin)
// ============================================

function renderTasksTab(container) {
    const content = container.querySelector('.sched-tab-content');

    let tasks = cachedBoard;
    if (tasksSearchQuery) {
        const q = tasksSearchQuery.toLowerCase();
        tasks = tasks.filter(t => t.name.toLowerCase().includes(q));
    }

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

    let html = `
        <div class="sched-tasks-toolbar">
            <input type="text" class="sched-search sched-tasks-search" placeholder="Filter tasks..." value="${escapeHtml(tasksSearchQuery)}" />
        </div>
    `;

    if (!tasks.length) {
        html += '<div class="sched-empty">No tasks configured</div>';
    } else {
        html += `
            <div class="sched-board-table-wrap">
                <table class="sched-board-table">
                    ${tableHead}
                    <tbody>${tasks.map(t => renderTaskRow(t)).join('')}</tbody>
                </table>
            </div>
        `;
    }

    content.innerHTML = html;

    // Wire search
    const searchInput = content.querySelector('.sched-tasks-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            tasksSearchQuery = e.target.value;
            renderTasksTab(container);
        });
    }

    wireContentActions(container);
}

function renderTaskRow(task) {
    const currentTask = cachedLiveState?.current_task || null;
    const isActive = task.name === currentTask;
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

    const durationStr = task.last_duration != null ? `${task.last_duration}s` : '\u2014';
    const overdueStr = task.overdue_str || '\u2014';
    const fillerTag = task.filler ? ' <span class="sched-filler-tag">(filler)</span>' : '';

    return `
        <tr class="${rowClass}" data-task="${task.name}">
            <td><span class="${toggleDotClass}" data-action="toggle" data-task="${task.name}" data-enabled="${task.enabled}" title="${task.enabled ? 'Disable' : 'Enable'}"></span></td>
            <td class="sched-task-name sched-clickable" data-action="drilldown" data-task="${task.name}">${escapeHtml(task.name)}${fillerTag}</td>
            <td class="sched-task-session">${task.session ? `<a class="sched-session-link" data-action="open-session" data-session="${escapeHtml(task.session)}" title="Open session">${escapeHtml(task.session)}</a>` : '\u2014'}</td>
            <td>${task.interval_str || '\u2014'}</td>
            <td>${task.last_run && task.last_run !== 'never' ? escapeHtml(task.last_run) : 'never'}</td>
            <td>${statusBadge}</td>
            <td>${durationStr}</td>
            <td class="sched-overdue">${overdueStr}</td>
            <td>${task.run_count ?? 0}</td>
            <td class="sched-actions">
                <button class="sched-btn sched-btn-run" data-action="run" data-task="${task.name}" title="Force run">Run</button>
            </td>
        </tr>
    `;
}

// ============================================
// Shared Action Wiring
// ============================================

/** Bound click handler reference for cleanup */
let boundClickHandler = null;

function wireContentActions(container) {
    const content = container.querySelector('.sched-tab-content');
    // Remove previous handler to avoid duplicates
    if (boundClickHandler) {
        content.removeEventListener('click', boundClickHandler);
    }
    boundClickHandler = (e) => handleContentClick(container, e);
    content.addEventListener('click', boundClickHandler);
}

function handleContentClick(container, e) {
    const target = e.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;

    // Skip actions handled by dedicated listeners (expand-history, expand-run)
    if (action === 'expand-history' || action === 'expand-run') return;

    if (action === 'toggle-output') {
        const session = target.dataset.session;
        toggleOutputPreview(container, session);
        return;
    } else if (action === 'drilldown') {
        const taskName = target.dataset.task;
        openDrilldown(container, taskName);
    } else if (action === 'toggle') {
        const taskName = target.dataset.task;
        const isEnabled = target.dataset.enabled === 'true';
        toggleTask(taskName, !isEnabled, container);
    } else if (action === 'run') {
        const taskName = target.dataset.task;
        forceRunTask(taskName, target, container);
    } else if (action === 'open-session') {
        const session = target.dataset.session;
        if (session) openSession(session);
    }
}

// ============================================
// Drilldown Side Panel
// ============================================

async function openDrilldown(container, taskName) {
    drilldownTask = taskName;
    const panel = container.querySelector('.sched-drilldown');
    panel.classList.add('open');

    const task = cachedBoard.find(t => t.name === taskName);

    // Show loading
    panel.innerHTML = `
        <div class="sched-drilldown-header">
            <span class="sched-drilldown-title">${escapeHtml(taskName)}</span>
            <button class="sched-drilldown-close" data-action="close-drilldown">\u2715</button>
        </div>
        <div class="sched-drilldown-body">
            <div class="sched-empty">Loading...</div>
        </div>
    `;

    panel.querySelector('.sched-drilldown-close').addEventListener('click', () => closeDrilldown(container));

    // Fetch task events
    let events = [];
    try {
        const res = await fetch(`/api/scheduler/tasks/${encodeURIComponent(taskName)}/events?tail=50`);
        const data = await res.json();
        events = (data.events || []).filter(e => (e.event || e.type) === 'task_completed').reverse();
    } catch {
        // empty
    }

    // Check we're still showing this task
    if (drilldownTask !== taskName) return;

    const statusBadge = task?.last_status
        ? `<span class="sched-badge sched-badge-${getStatusClass(task.last_status)}">${task.last_status}</span>`
        : '<span class="sched-badge sched-badge-never">never</span>';

    let bodyHtml = '';

    // Config section
    if (task) {
        bodyHtml += `
            <div class="sched-drilldown-section">
                <div class="sched-drilldown-section-title">Configuration</div>
                <table class="sched-detail-table">
                    <tr><td>Session</td><td>${escapeHtml(task.session || '\u2014')}</td></tr>
                    <tr><td>Project</td><td>${escapeHtml(task.project || '\u2014')}</td></tr>
                    <tr><td>Task</td><td>${escapeHtml(task.task || '\u2014')}</td></tr>
                    <tr><td>Interval</td><td>${task.interval_str || '\u2014'} (${task.interval}s)</td></tr>
                    <tr><td>Priority</td><td>${task.priority ?? '\u2014'}</td></tr>
                    <tr><td>Filler</td><td>${task.filler ? 'Yes' : 'No'}</td></tr>
                    <tr><td>Enabled</td><td>${task.enabled ? 'Yes' : 'No'}</td></tr>
                    <tr><td>Status</td><td>${statusBadge}</td></tr>
                    <tr><td>Runs</td><td>${task.run_count ?? 0}</td></tr>
                </table>
            </div>
        `;
    }

    // Run history
    bodyHtml += `
        <div class="sched-drilldown-section">
            <div class="sched-drilldown-section-title">Run History <span class="sched-section-count">${events.length}</span></div>
    `;

    if (events.length === 0) {
        bodyHtml += '<div class="sched-empty">No runs recorded</div>';
    } else {
        events.forEach((evt, i) => {
            const ts = evt.ts ? formatTimestamp(evt.ts) : '';
            const tsDate = evt.ts ? formatDate(evt.ts) : '';
            const sc = getStatusClass(evt.status);
            const dur = evt.duration ? `${evt.duration}s` : '';

            bodyHtml += `
                <div class="sched-drilldown-run">
                    <div class="sched-drilldown-run-header" data-action="expand-run" data-idx="${i}">
                        <span class="sched-drilldown-run-ts">${tsDate} ${ts}</span>
                        <span class="sched-badge sched-badge-${sc}">${evt.status || '?'}</span>
                        <span class="sched-drilldown-run-dur">${dur}</span>
                        <span class="sched-drilldown-run-chevron">${evt.summary ? '\u25B6' : ''}</span>
                    </div>
                    ${evt.summary ? `<div class="sched-drilldown-run-detail" style="display:none;"><pre class="sched-summary-pre">${escapeHtml(evt.summary)}</pre></div>` : ''}
                </div>
            `;
        });
    }

    bodyHtml += '</div>';

    const body = panel.querySelector('.sched-drilldown-body');
    body.innerHTML = bodyHtml;

    // Wire expand for runs
    body.querySelectorAll('[data-action="expand-run"]').forEach(hdr => {
        hdr.addEventListener('click', () => {
            const run = hdr.closest('.sched-drilldown-run');
            const detail = run.querySelector('.sched-drilldown-run-detail');
            if (!detail) return;
            const isOpen = detail.style.display !== 'none';
            detail.style.display = isOpen ? 'none' : 'block';
            hdr.querySelector('.sched-drilldown-run-chevron').textContent = isOpen ? '\u25B6' : '\u25BC';
        });
    });
}

function closeDrilldown(container) {
    drilldownTask = null;
    const panel = container.querySelector('.sched-drilldown');
    panel.classList.remove('open');
    panel.innerHTML = '';
}

// ============================================
// Task Actions
// ============================================

async function toggleTask(name, enable, container) {
    const action = enable ? 'enable' : 'disable';
    try {
        const res = await fetch(`/api/scheduler/tasks/${encodeURIComponent(name)}/${action}`, { method: 'POST' });
        if (res.ok) {
            await refreshBoard(container);
            renderActiveTab(container);
        }
    } catch (err) {
        console.error(`[Scheduler] Toggle failed:`, err);
    }
}

async function forceRunTask(name, btn, container) {
    btn.disabled = true;
    btn.textContent = '...';
    try {
        await fetch(`/api/scheduler/tasks/${encodeURIComponent(name)}/run`, { method: 'POST' });
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
// Session Navigation
// ============================================

async function openSession(session) {
    const { openSessionTerminal } = await import('../desktop.js');
    openSessionTerminal(session, 'terminal');
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

function formatDuration(seconds) {
    if (seconds == null) return '';
    const s = Math.floor(Math.abs(seconds));
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

function formatDate(ts) {
    try {
        const d = new Date(ts);
        const today = new Date();
        if (d.toDateString() === today.toDateString()) return 'Today';
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);
        if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
        return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    } catch {
        return '';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ============================================
// Agent Progress Helpers
// ============================================

function getAgentStatusBadge(status) {
    if (!status) return '';
    const cls = status === 'busy' ? 'sched-agent-busy'
        : status === 'retry' ? 'sched-agent-retry'
        : 'sched-agent-idle';
    return `<span class="sched-agent-badge ${cls}">${status}</span>`;
}

/** Inline DOM update for progress stats — avoids full tab re-render */
function updateActiveCardProgress(container) {
    const statsEl = container.querySelector('.sched-agent-stats');
    if (!statsEl) return;

    const activeTask = cachedLiveState?.current_task
        ? cachedBoard.find(t => t.name === cachedLiveState.current_task)
        : null;
    if (!activeTask) return;

    const session = activeTask.session || '';
    const progress = agentProgress[session];
    if (!progress) return;

    const statusBadge = getAgentStatusBadge(progress.status);
    const respCount = progress.responses > 0 ? `<span class="sched-agent-responses">${progress.responses} resp</span>` : '';
    const diffIcon = progress.has_diffs ? '<span class="sched-diff-indicator" title="Files changed">\u270E</span>' : '';
    statsEl.innerHTML = `${statusBadge}${respCount}${diffIcon}`;
}

function toggleOutputPreview(container, session) {
    if (outputExpanded && expandedSession === session) {
        // Collapse
        outputExpanded = false;
        expandedSession = null;
        stopOutputRefresh();
        const outputEl = container.querySelector('.sched-active-output');
        if (outputEl) outputEl.style.display = 'none';
        const btn = container.querySelector('.sched-expand-btn');
        if (btn) btn.textContent = '\u25BC';
    } else {
        // Expand
        outputExpanded = true;
        expandedSession = session;
        const outputEl = container.querySelector('.sched-active-output');
        if (outputEl) {
            outputEl.style.display = 'block';
            const pre = outputEl.querySelector('.sched-output-pre');
            if (pre) pre.textContent = 'Loading...';
        }
        const btn = container.querySelector('.sched-expand-btn');
        if (btn) btn.textContent = '\u25B2';
        fetchAndShowOutput(container, session);
        startOutputRefresh(container, session);
    }
}

async function fetchAndShowOutput(container, session) {
    try {
        const res = await fetch(`/api/scheduler/output?session=${encodeURIComponent(session)}&lines=30`);
        const data = await res.json();
        const pre = container.querySelector('.sched-output-pre');
        if (pre && expandedSession === session) {
            pre.textContent = data.output || '(no output)';
            // Auto-scroll to bottom
            const outputEl = container.querySelector('.sched-active-output');
            if (outputEl) outputEl.scrollTop = outputEl.scrollHeight;
        }
    } catch {
        const pre = container.querySelector('.sched-output-pre');
        if (pre) pre.textContent = '(failed to fetch output)';
    }
}

function startOutputRefresh(container, session) {
    stopOutputRefresh();
    outputRefreshTimer = setInterval(() => {
        if (outputExpanded && expandedSession === session) {
            fetchAndShowOutput(container, session);
        } else {
            stopOutputRefresh();
        }
    }, 5000);
}

function stopOutputRefresh() {
    if (outputRefreshTimer) {
        clearInterval(outputRefreshTimer);
        outputRefreshTimer = null;
    }
}

function startElapsedTimer(container) {
    stopElapsedTimer();
    elapsedTimer = setInterval(() => {
        const el = container.querySelector('.sched-queue-elapsed');
        if (!el) { stopElapsedTimer(); return; }
        const started = el.dataset.started;
        if (!started) return;
        const elapsed = Math.floor((Date.now() - new Date(started).getTime()) / 1000);
        el.textContent = formatDuration(elapsed);
    }, 1000);
}

function stopElapsedTimer() {
    if (elapsedTimer) {
        clearInterval(elapsedTimer);
        elapsedTimer = null;
    }
}

/** Tick down the "next: task in X" countdown in the center area */
function startIdleTimer(container) {
    stopIdleTimer();
    idleTimer = setInterval(() => {
        const countdownEl = container.querySelector('.sched-activity-countdown');
        if (!countdownEl) { stopIdleTimer(); return; }
        const due = Number(countdownEl.dataset.due);
        if (!due) return;
        const remaining = Math.max(0, Math.floor((due - Date.now()) / 1000));
        countdownEl.textContent = formatDuration(remaining);
    }, 1000);
}

function stopIdleTimer() {
    if (idleTimer) {
        clearInterval(idleTimer);
        idleTimer = null;
    }
}

/** Tick up the elapsed time in the header center when executing */
let headerElapsedTimer = null;

function startHeaderElapsedTimer(container) {
    stopHeaderElapsedTimer();
    headerElapsedTimer = setInterval(() => {
        const el = container.querySelector('.sched-activity-elapsed');
        if (!el) { stopHeaderElapsedTimer(); return; }
        const started = el.dataset.started;
        if (!started) return;
        const elapsed = Math.floor((Date.now() - new Date(started).getTime()) / 1000);
        el.textContent = formatDuration(elapsed);
    }, 1000);
}

function stopHeaderElapsedTimer() {
    if (headerElapsedTimer) {
        clearInterval(headerElapsedTimer);
        headerElapsedTimer = null;
    }
}
