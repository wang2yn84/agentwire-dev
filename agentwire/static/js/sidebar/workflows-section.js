/**
 * Workflows sidebar section — lists recent workflow runs.
 *
 * Clicking a run opens the WorkflowWindow detail view. Independent of the
 * Scheduler section (scheduler shows *what will fire*, this shows *what ran*).
 */

import { openWorkflowWindow } from '../windows/workflow-window.js';

function _fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
        + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function _fmtDuration(ms) {
    if (!ms) return '';
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return rem ? `${m}m${rem}s` : `${m}m`;
}

function _statusDot(status) {
    if (status === 'success') return 'dot-online';
    if (status === 'failed' || status === 'error') return 'dot-offline';
    if (status === 'running') return 'dot-processing';
    return 'dot-idle';
}

export const workflowsSection = {
    title: 'Workflows',
    autoRefreshMs: 10000,  // poll every 10s while expanded
    _body: null,
    _runs: null,

    actions: [
        { id: 'refresh', label: '↻', title: 'Refresh' },
    ],

    onAction(actionId, body) {
        if (actionId === 'refresh') this.refresh(body);
    },

    async mount(body) {
        this._body = body;
        await this.refresh(body);
    },

    async refresh(body) {
        try {
            const res = await fetch('/api/workflows/runs?limit=30');
            const data = await res.json();
            this._runs = Array.isArray(data.runs) ? data.runs : [];
        } catch (e) {
            this._runs = null;
        }
        this._render(body);
    },

    _render(body) {
        if (this._runs === null) {
            body.innerHTML = '<div class="sidebar-empty">Failed to load runs</div>';
            return;
        }
        if (this._runs.length === 0) {
            body.innerHTML = '<div class="sidebar-empty">No runs yet</div>';
            return;
        }

        // Group by workflow name — scannable with multiple runs per workflow
        const byWorkflow = new Map();
        for (const r of this._runs) {
            const wf = r.workflow || '(unknown)';
            if (!byWorkflow.has(wf)) byWorkflow.set(wf, []);
            byWorkflow.get(wf).push(r);
        }

        let html = '';
        for (const [wf, runs] of byWorkflow) {
            html += `<div class="sidebar-section-subheader">${wf}</div>`;
            for (const r of runs) {
                const dot = _statusDot(r.status);
                const when = _fmtTime(r.started_at);
                const dur = _fmtDuration(r.duration_ms);
                const runner = r.runner || '';
                const runnerBadge = runner
                    ? `<span class="sidebar-workflow-runner" data-runner="${runner}">${runner}</span>`
                    : '';
                html += `<div class="sidebar-list-item sidebar-workflow-run" data-run-id="${r.run_id}" title="${r.run_id}">
                    <span class="sidebar-status-dot ${dot}"></span>
                    <span class="sidebar-list-item-title">${when} · ${dur}</span>
                    ${runnerBadge}
                </div>`;
            }
        }

        body.innerHTML = html;
        body.onclick = (e) => this._handleClick(e);
    },

    _handleClick(e) {
        const item = e.target.closest('[data-run-id]');
        if (!item) return;
        openWorkflowWindow(item.dataset.runId);
    },
};
