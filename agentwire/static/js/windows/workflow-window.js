/**
 * workflow-window.js
 *
 * WorkflowWindow — shows detail for a single workflow run: metadata,
 * per-node status/tokens/cost, tool calls, and final text.
 *
 * Read-only historical view. Live-streaming in-flight runs is a later
 * enhancement — this window just reloads on open.
 */

import { desktop } from '../desktop-manager.js';

// Cache open windows by run_id so repeat clicks focus the existing one.
const _openWindows = new Map();

function _escape(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _fmtDuration(ms) {
    if (!ms) return '—';
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return rem ? `${m}m${rem}s` : `${m}m`;
}

function _fmtCost(n) {
    if (!n) return '$0.00';
    return '$' + n.toFixed(4);
}

function _fmtTokens(n) {
    return (n || 0).toLocaleString();
}

function _renderDetail(data) {
    const meta = data.metadata || {};
    const context = data.context || {};
    const nodes = data.nodes || [];

    // Run-level totals aren't stored in metadata — sum from nodes.
    const totalIn = nodes.reduce((a, n) => a + (n.tokens_in || 0), 0);
    const totalOut = nodes.reduce((a, n) => a + (n.tokens_out || 0), 0);
    const totalCost = nodes.reduce((a, n) => a + (n.cost || 0), 0);

    const metaHtml = `
        <div class="wf-meta">
            <div class="wf-meta-row"><span class="wf-meta-label">Workflow</span><span class="wf-meta-value">${_escape(meta.workflow)}</span></div>
            <div class="wf-meta-row"><span class="wf-meta-label">Run ID</span><span class="wf-meta-value wf-meta-runid">${_escape(meta.run_id)}</span></div>
            <div class="wf-meta-row"><span class="wf-meta-label">Status</span><span class="wf-meta-value wf-status-${_escape(meta.status)}">${_escape(meta.status)}</span></div>
            <div class="wf-meta-row"><span class="wf-meta-label">Runner</span><span class="wf-meta-value">${_escape(meta.runner || '—')}</span></div>
            <div class="wf-meta-row"><span class="wf-meta-label">Duration</span><span class="wf-meta-value">${_fmtDuration(meta.duration_ms)}</span></div>
            <div class="wf-meta-row"><span class="wf-meta-label">Tokens in/out</span><span class="wf-meta-value">${_fmtTokens(totalIn)} / ${_fmtTokens(totalOut)}</span></div>
            <div class="wf-meta-row"><span class="wf-meta-label">Cost (nominal)</span><span class="wf-meta-value">${_fmtCost(totalCost)}</span></div>
        </div>
    `;

    const inputsHtml = Object.keys(context.inputs || {}).length
        ? `<details class="wf-section"><summary>Inputs</summary><pre class="wf-pre">${_escape(JSON.stringify(context.inputs, null, 2))}</pre></details>`
        : '';

    const errorHtml = meta.error
        ? `<div class="wf-node-error"><strong>Run error:</strong> ${_escape(meta.error)}</div>`
        : '';

    const nodesHtml = nodes.map(n => {
        const toolCallsHtml = (n.tool_calls || []).length
            ? `<div class="wf-node-tools"><div class="wf-node-label">Tool calls (${n.tool_calls.length})</div>` +
              n.tool_calls.map(tc =>
                  `<div class="wf-tool-call"><span class="wf-tool-name">${_escape(tc.name)}</span><span class="wf-tool-input">${_escape(tc.input_preview)}</span></div>`
              ).join('') + '</div>'
            : '';

        const finalTextHtml = n.final_text
            ? `<details class="wf-node-final"><summary>Final text</summary><pre class="wf-pre">${_escape(n.final_text)}</pre></details>`
            : '';

        const nodeErrorHtml = n.error
            ? `<div class="wf-node-error"><strong>Error:</strong> ${_escape(n.error)}</div>`
            : '';

        return `
            <div class="wf-node">
                <div class="wf-node-header">
                    <span class="wf-status-dot wf-status-${_escape(n.status)}"></span>
                    <span class="wf-node-id">${_escape(n.node_id)}</span>
                    <span class="wf-node-runner">${_escape(n.runner || '')}</span>
                    <span class="wf-node-dur">${_fmtDuration(n.duration_ms)}</span>
                    <span class="wf-node-tokens">${_fmtTokens(n.tokens_in)} / ${_fmtTokens(n.tokens_out)} tok</span>
                    <span class="wf-node-events">${n.event_count || 0} events</span>
                </div>
                ${nodeErrorHtml}
                ${toolCallsHtml}
                ${finalTextHtml}
            </div>
        `;
    }).join('');

    return `
        <div class="wf-window-content">
            ${metaHtml}
            ${inputsHtml}
            ${errorHtml}
            <div class="wf-nodes">
                <div class="wf-section-header">Nodes</div>
                ${nodesHtml || '<div class="wf-empty">No nodes recorded</div>'}
            </div>
        </div>
    `;
}

export function openWorkflowWindow(runId) {
    if (_openWindows.has(runId)) {
        _openWindows.get(runId).focus();
        return;
    }

    const container = document.createElement('div');
    container.className = 'workflow-window-content';
    container.innerHTML = '<div class="wf-loading">Loading run…</div>';

    const winbox = new WinBox({
        title: `Workflow · ${runId}`,
        icon: '<span style="font-size:14px">&#x26A1;</span>',
        mount: container,
        root: document.body,
        width: '70%',
        height: '80%',
        x: 'center',
        y: 'center',
        minwidth: 480,
        minheight: 320,
        class: ['workflow-window'],
        onclose: () => {
            _openWindows.delete(runId);
            desktop.unregisterWindow(`workflow:${runId}`);
            return false;
        },
        onfocus: () => {},
    });

    desktop.registerWindow(`workflow:${runId}`, winbox);
    _openWindows.set(runId, winbox);

    // Fetch and render
    fetch(`/api/workflows/runs/${encodeURIComponent(runId)}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                container.innerHTML = `<div class="wf-error">${_escape(data.error)}</div>`;
                return;
            }
            container.innerHTML = _renderDetail(data);
        })
        .catch(e => {
            container.innerHTML = `<div class="wf-error">Failed to load: ${_escape(e.message || e)}</div>`;
        });
}
