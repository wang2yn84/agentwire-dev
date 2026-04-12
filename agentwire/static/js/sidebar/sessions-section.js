import { desktop } from '../desktop-manager.js';

const activityStates = new Map();

export const sessionsSection = {
    title: 'Sessions',
    _body: null,
    _sessions: [],

    async mount(body) {
        this._body = body;
        await this.refresh(body);

        desktop.on('sessions', (sessions) => {
            this._sessions = sessions;
            this._render(body);
        });
        desktop.on('session_activity', ({ session, active }) => {
            const prev = activityStates.get(session);
            if (prev === 'generating' || prev === 'playing') return;
            activityStates.set(session, active ? 'processing' : 'idle');
            this._updateActivityDot(body, session);
        });
        desktop.on('tts_start', ({ session }) => {
            activityStates.set(session, 'generating');
            this._updateActivityDot(body, session);
        });
        desktop.on('audio', ({ session }) => {
            activityStates.set(session, 'playing');
            this._updateActivityDot(body, session);
        });
        desktop.on('audio_ended', ({ session }) => {
            activityStates.set(session, 'idle');
            this._updateActivityDot(body, session);
        });
    },

    async refresh(body) {
        // Render local sessions immediately, then append remote when it arrives.
        // Remote can take seconds if machines are unreachable (SSH timeouts).
        try {
            const localRes = await fetch('/api/sessions/local');
            const localData = await localRes.json();
            this._sessions = localData.sessions || [];
            this._render(body);
        } catch (e) {
            this._sessions = [];
            this._render(body);
        }
        // Fire-and-forget: merge remote sessions when they arrive
        fetch('/api/sessions/remote').then(async (res) => {
            try {
                const data = await res.json();
                const remote = data.sessions || [];
                if (remote.length) {
                    const localNames = new Set(this._sessions.map(s => s.name));
                    for (const s of remote) {
                        if (!localNames.has(s.name)) this._sessions.push(s);
                    }
                    this._render(body);
                }
            } catch (e) {}
        }).catch(() => {});
    },

    _render(body) {
        if (!this._sessions.length) {
            body.innerHTML = '<div class="sidebar-empty">No sessions</div>';
            return;
        }
        body.innerHTML = this._sessions.map(s => {
            const name = s.name || '';
            const machine = s.machine || null;
            const id = machine ? `${name}@${machine}` : name;
            const activity = activityStates.get(name) || s.activity || 'idle';
            const dotClass = activity === 'idle' ? 'dot-idle' : activity === 'processing' ? 'dot-processing' : activity === 'generating' ? 'dot-generating' : 'dot-playing';
            const isSdk = (s.type || '').startsWith('sdk');
            const connectAction = isSdk ? 'sdk' : 'connect';
            const connectLabel = isSdk ? 'Open' : '▸';
            const tags = [];
            if (s.type) tags.push(`<span class="sidebar-tag">${s.type}</span>`);
            if (machine) tags.push(`<span class="sidebar-tag">@${machine}</span>`);
            const roles = (s.roles || []).map(r => `<span class="sidebar-tag sidebar-tag-role">${r}</span>`).join('');
            const path = s.path ? s.path.replace(/^\/Users\/[^/]+\//, '~/') : '';
            return `<div class="sidebar-session-card" data-session="${name}" data-machine="${machine || ''}" data-id="${id}">
                <div class="sidebar-session-row1">
                    <span class="sidebar-activity-dot ${dotClass}" data-session-dot="${name}"></span>
                    <span class="sidebar-session-name">${name}</span>
                    <button class="sidebar-list-item-btn" data-action="${connectAction}" title="Connect">${connectLabel}</button>
                    <button class="sidebar-list-item-btn" data-action="monitor" title="Monitor">👁</button>
                </div>
                <div class="sidebar-session-row2">
                    ${tags.join('')}${roles}
                    ${path ? `<span class="sidebar-session-path">${path}</span>` : ''}
                </div>
            </div>`;
        }).join('');
        body.onclick = (e) => this._handleClick(e, body);
    },

    _updateActivityDot(body, session) {
        const dot = body.querySelector(`[data-session-dot="${CSS.escape(session)}"]`);
        if (!dot) return;
        dot.className = 'sidebar-activity-dot';
        const state = activityStates.get(session) || 'idle';
        dot.classList.add(state === 'idle' ? 'dot-idle' : state === 'processing' ? 'dot-processing' : state === 'generating' ? 'dot-generating' : 'dot-playing');
    },

    async _handleClick(e, body) {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const item = btn.closest('[data-session]');
        if (!item) return;
        const session = item.dataset.session;
        const machine = item.dataset.machine || null;
        const action = btn.dataset.action;
        const { openSessionTerminal } = await import('../desktop.js');
        if (action === 'connect') openSessionTerminal(session, 'terminal', machine);
        else if (action === 'monitor') openSessionTerminal(session, 'monitor', machine);
        else if (action === 'sdk') openSessionTerminal(session, 'sdk', machine);
    },
};
