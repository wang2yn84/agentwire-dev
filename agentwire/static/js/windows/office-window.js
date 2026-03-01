/**
 * office-window.js
 *
 * Opens the pixel agents office visualization in a WinBox window with an iframe.
 * Subscribes to portal desktop events and translates them into the office app's
 * expected message format (postMessage to iframe).
 */

import { desktop } from '../desktop-manager.js';

const STORAGE_KEY = 'agentwire_office_layout';
const STORAGE_KEY_SEATS = 'agentwire_office_seats';

let officeWindow = null;
let officeIframe = null;
let iframeReady = false;
let eventCleanups = [];

// Track pane IDs per session for sub-agent visualization.
// session name → Set<pane_id>  (excludes pane 0 / orchestrator)
const sessionPanes = new Map();
// Map pane_id → toolId used when spawning the sub-agent (needed for subagentClear)
const paneToolIds = new Map();

/**
 * Hash a session name to a stable integer ID for character assignment.
 * Same session always gets the same character skin.
 */
function hashSessionName(name) {
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
        hash = ((hash << 5) - hash + name.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
}

/**
 * Post a message to the office iframe in the portal message format.
 */
function postToOffice(payload) {
    if (!officeIframe || !iframeReady) return;
    officeIframe.contentWindow.postMessage({ source: 'portal', payload }, '*');
}

/**
 * Build an agent list from current sessions for existingAgents message.
 */
function buildExistingAgents(sessions) {
    const agents = [];
    const folderNames = {};
    const agentMeta = {};
    const savedSeats = loadSeats();

    for (const s of sessions) {
        const name = s.name || s;
        const id = hashSessionName(name);
        agents.push(id);
        folderNames[id] = name;
        if (savedSeats[id]) {
            agentMeta[id] = savedSeats[id];
        }
    }
    return { agents, folderNames, agentMeta };
}

function loadLayout() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}

function saveLayout(layout) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
    } catch { /* ignore */ }
}

function loadSeats() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY_SEATS);
        return raw ? JSON.parse(raw) : {};
    } catch {
        return {};
    }
}

function saveSeats(seats) {
    try {
        localStorage.setItem(STORAGE_KEY_SEATS, JSON.stringify(seats));
    } catch { /* ignore */ }
}

/**
 * Handle messages from the office iframe (layout saves, agent focus, etc.)
 */
function handleOfficeMessage(e) {
    if (e.data?.source !== 'office') return;
    const msg = e.data.payload;
    if (!msg) return;

    if (msg.type === 'saveLayout') {
        saveLayout(msg.layout);
    } else if (msg.type === 'saveAgentSeats') {
        saveSeats(msg.seats);
    } else if (msg.type === 'webviewReady') {
        iframeReady = true;
        initializeOffice();
    } else if (msg.type === 'focusAgent') {
        // Find session name by ID and open its terminal
        const sessions = desktop.sessions || [];
        for (const s of sessions) {
            const name = s.name || s;
            if (hashSessionName(name) === msg.id) {
                // Dispatch to open session terminal via desktop event
                desktop.emit('office_focus_session', { session: name });
                break;
            }
        }
    } else if (msg.type === 'closeAgent') {
        // Find session name and kill it
        const sessions = desktop.sessions || [];
        for (const s of sessions) {
            const name = s.name || s;
            if (hashSessionName(name) === msg.id) {
                fetch(`/api/sessions/${encodeURIComponent(name)}`, { method: 'DELETE' });
                break;
            }
        }
    }
}

/**
 * Send initial state to the office after iframe is ready.
 */
function initializeOffice() {
    // Send layout
    const layout = loadLayout();
    postToOffice({ type: 'layoutLoaded', layout });

    // Send existing sessions as agents
    const sessions = desktop.sessions || [];
    if (sessions.length > 0) {
        const { agents, folderNames, agentMeta } = buildExistingAgents(sessions);
        postToOffice({ type: 'existingAgents', agents, folderNames, agentMeta });

        // Send initial activity status for each session
        for (const s of sessions) {
            const name = s.name || s;
            const id = hashSessionName(name);
            const isActive = s.activity === 'active';
            postToOffice({
                type: 'agentStatus',
                id,
                status: isActive ? 'active' : 'waiting',
            });
        }
    }
}

/**
 * Subscribe to desktop events and translate them to office messages.
 */
function setupEventBridge() {
    const on = (event, handler) => {
        desktop.on(event, handler);
        eventCleanups.push(() => desktop.off(event, handler));
    };

    // Full session list update — reconcile agents and activity
    on('sessions', (sessions) => {
        if (!iframeReady) return;
        const { agents, folderNames, agentMeta } = buildExistingAgents(sessions);
        postToOffice({ type: 'existingAgents', agents, folderNames, agentMeta });

        // Send activity status for each session
        for (const s of sessions) {
            const name = s.name || s;
            const id = hashSessionName(name);
            const isActive = s.activity === 'active';
            postToOffice({
                type: 'agentStatus',
                id,
                status: isActive ? 'active' : 'waiting',
            });
        }
    });

    // New session created
    on('session_created', ({ session }) => {
        if (!iframeReady) return;
        const id = hashSessionName(session);
        postToOffice({ type: 'agentCreated', id, folderName: session });
    });

    // Session activity (active/idle)
    on('session_activity', ({ session, active }) => {
        if (!iframeReady) return;
        const id = hashSessionName(session);
        postToOffice({
            type: 'agentStatus',
            id,
            status: active ? 'active' : 'waiting',
        });
    });

    // Session processing state — mark active and show tool activity
    on('session_processing', ({ session, processing }) => {
        if (!iframeReady) return;
        const id = hashSessionName(session);
        postToOffice({
            type: 'agentStatus',
            id,
            status: processing ? 'active' : 'waiting',
        });
        if (processing) {
            postToOffice({
                type: 'agentToolStart',
                id,
                toolId: `processing-${Date.now()}`,
                status: 'Processing prompt...',
            });
        } else {
            postToOffice({ type: 'agentToolsClear', id });
        }
    });

    // TTS start — show tool activity
    on('tts_start', ({ session }) => {
        if (!iframeReady) return;
        const id = hashSessionName(session);
        postToOffice({
            type: 'agentToolStart',
            id,
            toolId: `tts-${Date.now()}`,
            status: 'Speaking...',
        });
    });

    // Audio ended — clear tool activity
    on('audio_ended', ({ session }) => {
        if (!iframeReady) return;
        const id = hashSessionName(session);
        postToOffice({ type: 'agentToolsClear', id });
    });

    // Permission request — show permission bubble
    on('session_permission', ({ session }) => {
        if (!iframeReady) return;
        const id = hashSessionName(session);
        postToOffice({ type: 'agentToolPermission', id });
    });

    // Permission resolved — clear permission bubble
    on('session_permission_clear', ({ session }) => {
        if (!iframeReady) return;
        const id = hashSessionName(session);
        postToOffice({ type: 'agentToolPermissionClear', id });
    });

    // Worker pane created — spawn sub-agent character
    on('pane_created', ({ session, pane_id }) => {
        if (!iframeReady || !pane_id) return;
        // Track this pane
        if (!sessionPanes.has(session)) {
            sessionPanes.set(session, new Set());
        }
        sessionPanes.get(session).add(pane_id);

        // Send agentToolStart with "Subtask:" prefix to trigger sub-agent spawn
        const parentId = hashSessionName(session);
        const toolId = `pane-${pane_id}`;
        paneToolIds.set(pane_id, toolId);
        postToOffice({
            type: 'agentToolStart',
            id: parentId,
            toolId,
            status: `Subtask: Worker ${pane_id}`,
        });
    });

    // Worker pane received a prompt — update sub-agent label
    on('pane_prompt', ({ session, pane_id, prompt }) => {
        if (!iframeReady || !pane_id || !prompt) return;
        const toolId = paneToolIds.get(pane_id);
        if (!toolId) return;
        const parentId = hashSessionName(session);
        postToOffice({
            type: 'subagentLabel',
            id: parentId,
            parentToolId: toolId,
            label: prompt,
        });
    });

    // Worker pane died — diff tracked panes against live state to find which died
    on('pane_died', ({ session }) => {
        if (!iframeReady) return;
        const tracked = sessionPanes.get(session);
        if (!tracked || tracked.size === 0) return;

        // Fetch current pane list to find which pane was removed
        fetch(`/api/session/${encodeURIComponent(session)}/info`)
            .then(r => r.json())
            .then(info => {
                const livePaneIds = new Set(
                    (info.panes || []).map(p => p.pane_id).filter(Boolean)
                );
                const parentId = hashSessionName(session);

                for (const paneId of tracked) {
                    if (!livePaneIds.has(paneId)) {
                        // This pane was removed — clear its sub-agent
                        const toolId = paneToolIds.get(paneId) || `pane-${paneId}`;
                        postToOffice({
                            type: 'subagentClear',
                            id: parentId,
                            parentToolId: toolId,
                        });
                        tracked.delete(paneId);
                        paneToolIds.delete(paneId);
                    }
                }
                if (tracked.size === 0) {
                    sessionPanes.delete(session);
                }
            })
            .catch(() => {
                // Session might be gone — clear all sub-agents for it
                const parentId = hashSessionName(session);
                for (const paneId of tracked) {
                    const toolId = paneToolIds.get(paneId) || `pane-${paneId}`;
                    postToOffice({
                        type: 'subagentClear',
                        id: parentId,
                        parentToolId: toolId,
                    });
                    paneToolIds.delete(paneId);
                }
                sessionPanes.delete(session);
            });
    });

    // Session closed — clean up all sub-agent tracking
    on('session_closed', ({ session }) => {
        if (!iframeReady) return;
        const id = hashSessionName(session);
        postToOffice({ type: 'agentClosed', id });

        // Clean up pane tracking
        const tracked = sessionPanes.get(session);
        if (tracked) {
            for (const paneId of tracked) {
                paneToolIds.delete(paneId);
            }
            sessionPanes.delete(session);
        }
    });
}

function cleanupEventBridge() {
    for (const cleanup of eventCleanups) cleanup();
    eventCleanups = [];
    sessionPanes.clear();
    paneToolIds.clear();
}

/**
 * Open the office window. Creates a WinBox with an iframe pointing to the
 * built pixel-agents office app.
 */
export function openOfficeWindow(onClose = null) {
    // If already open, restore/focus
    if (officeWindow) {
        if (officeWindow.min) {
            officeWindow.restore();
        } else {
            officeWindow.focus();
        }
        return officeWindow;
    }

    // Start listening for iframe messages
    window.addEventListener('message', handleOfficeMessage);

    // Create container with iframe
    const container = document.createElement('div');
    container.style.cssText = 'width:100%;height:100%;overflow:hidden;';

    const iframe = document.createElement('iframe');
    iframe.src = '/static/office/index.html';
    iframe.style.cssText = 'width:100%;height:100%;border:none;';
    iframe.sandbox = 'allow-scripts allow-same-origin';
    container.appendChild(iframe);
    officeIframe = iframe;

    officeWindow = new WinBox({
        title: 'Office',
        icon: '<span style="font-size:14px">&#x1F3E2;</span>',
        mount: container,
        root: document.getElementById('desktopArea'),
        width: '80%',
        height: '80%',
        x: 'center',
        y: 'center',
        minwidth: 400,
        minheight: 300,
        class: ['office-window'],
        onclose: () => {
            cleanupEventBridge();
            window.removeEventListener('message', handleOfficeMessage);
            desktop.unregisterWindow('office');
            officeWindow = null;
            officeIframe = null;
            iframeReady = false;
            if (onClose) onClose();
            return false;
        },
        onfocus: () => {
            desktop.setActiveWindow('office');
        },
        onminimize: () => {
            desktop.emit('window_minimized', { id: 'office' });
        },
        onrestore: () => {
            desktop.emit('window_restored', { id: 'office' });
        },
    });

    desktop.registerWindow('office', officeWindow);

    // Set up event bridge to translate portal events to office messages
    setupEventBridge();

    return officeWindow;
}

/**
 * Get the current office WinBox instance (if open).
 */
export function getOfficeWindow() {
    return officeWindow;
}
