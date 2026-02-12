/**
 * Artifacts Window - displays files in the artifacts directory with open/delete actions
 */

import { ListWindow } from '../list-window.js';

/** @type {ListWindow|null} */
let artifactsWindow = null;

/**
 * Open the Artifacts window
 * @returns {ListWindow} The artifacts window instance
 */
export function openArtifactsWindow() {
    if (artifactsWindow?.winbox) {
        artifactsWindow.winbox.focus();
        return artifactsWindow;
    }

    artifactsWindow = new ListWindow({
        id: 'artifacts',
        title: 'Artifacts',
        fetchData: fetchArtifacts,
        renderItem: renderArtifactItem,
        onItemAction: handleArtifactAction,
        emptyMessage: 'No artifacts',
    });

    artifactsWindow._cleanup = () => {
        artifactsWindow = null;
    };

    artifactsWindow.open();
    return artifactsWindow;
}

async function fetchArtifacts() {
    const res = await fetch('/api/artifacts');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(mtime) {
    return new Date(mtime * 1000).toLocaleString([], {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function renderArtifactItem(item) {
    return `
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px;">
            <div class="list-item-info">
                <div class="list-item-name">${item.name}</div>
                <div class="list-item-meta">${formatSize(item.size)} &middot; ${formatDate(item.mtime)}</div>
            </div>
            <div class="list-item-actions">
                <button class="primary" data-action="open">Open</button>
                <button class="danger" data-action="delete">Delete</button>
            </div>
        </div>
    `;
}

async function handleArtifactAction(action, item) {
    if (action === 'open' || action === 'select') {
        // Dynamic import to avoid circular dependency with desktop.js
        const { openArtifactWindow } = await import('../desktop.js');
        openArtifactWindow(item.name, item.name);
    } else if (action === 'delete') {
        try {
            const res = await fetch(`/api/artifacts/${encodeURIComponent(item.name)}`, {
                method: 'DELETE',
            });
            if (res.ok) {
                artifactsWindow?.refresh();
            }
        } catch (err) {
            console.error('Delete artifact failed:', err);
        }
    }
}
