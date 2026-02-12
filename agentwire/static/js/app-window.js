/**
 * app-window.js
 *
 * AppWindow class — displays agent-generated HTML or external URLs
 * in a sandboxed iframe within a WinBox window.
 */

import { desktop } from './desktop-manager.js';

export class AppWindow {
    /**
     * @param {Object} options
     * @param {string} options.url - URL to load (relative /apps/... or absolute https://...)
     * @param {string} options.title - Window title
     * @param {string} options.appId - Unique window identifier
     * @param {HTMLElement} options.root - Parent element for WinBox
     * @param {Function} options.onClose - Callback when window closes
     * @param {Function} options.onFocus - Callback when window gains focus
     */
    constructor(options) {
        this.url = options.url;
        this.title = options.title || 'App';
        this.appId = options.appId;
        this.root = options.root || document.body;
        this.onCloseCallback = options.onClose || null;
        this.onFocusCallback = options.onFocus || null;

        this.winbox = null;
        this.iframe = null;
        this.isOpen = false;
    }

    /**
     * Open the app window.
     */
    open() {
        if (this.isOpen) {
            this.focus();
            return;
        }

        const container = this._createContainer();
        this._createWinBox(container);
        this._loadUrl();
        this.isOpen = true;
    }

    /**
     * Close the app window and clean up.
     */
    close() {
        if (!this.isOpen) return;

        // Remove iframe to stop any running scripts
        if (this.iframe) {
            this.iframe.src = 'about:blank';
            this.iframe = null;
        }

        if (this.winbox) {
            const wb = this.winbox;
            this.winbox = null;
            wb.close();
        }

        desktop.unregisterWindow(this.appId);
        this.isOpen = false;

        if (this.onCloseCallback) {
            this.onCloseCallback(this);
        }
    }

    focus() {
        if (this.winbox) this.winbox.focus();
    }

    minimize() {
        if (this.winbox) this.winbox.minimize();
    }

    restore() {
        if (this.winbox) this.winbox.restore();
    }

    get isMinimized() {
        return this.winbox ? this.winbox.min : false;
    }

    /**
     * Reload the iframe content.
     */
    reload() {
        if (this.iframe) {
            this.iframe.src = this._resolveUrl();
        }
    }

    // Private methods

    _createContainer() {
        const container = document.createElement('div');
        container.className = 'app-window-content';
        container.innerHTML = `
            <div class="app-loading">Loading...</div>
            <div class="app-error hidden">
                <div class="app-error-message">Failed to load</div>
                <button class="btn btn-primary app-reload-btn">Reload</button>
            </div>
        `;
        return container;
    }

    _createWinBox(container) {
        this.winbox = new WinBox({
            title: this.title,
            icon: '<span style="font-size:14px">&#x1F4CB;</span>',
            mount: container,
            root: this.root,
            width: '80%',
            height: '80%',
            x: 'center',
            y: 'center',
            minwidth: 320,
            minheight: 240,
            class: ['app-window'],
            onclose: () => {
                this.winbox = null;
                this.close();
                return false;
            },
            onfocus: () => {
                if (this.onFocusCallback) this.onFocusCallback(this);
            },
            onminimize: () => {
                desktop.emit('window_minimized', { id: this.appId });
            },
            onrestore: () => {
                desktop.emit('window_restored', { id: this.appId });
                if (this.onFocusCallback) this.onFocusCallback(this);
            },
        });

        desktop.registerWindow(this.appId, this.winbox);

        // Set up reload button
        const reloadBtn = container.querySelector('.app-reload-btn');
        if (reloadBtn) {
            reloadBtn.addEventListener('click', () => this.reload());
        }
    }

    _resolveUrl() {
        const url = this.url;
        // Absolute URLs (http/https) — use as-is
        if (url.startsWith('http://') || url.startsWith('https://')) {
            return url;
        }
        // Already a path starting with /
        if (url.startsWith('/')) {
            return url;
        }
        // Relative filename — serve from /apps/
        return `/apps/${url}`;
    }

    _isExternalUrl() {
        return this.url.startsWith('http://') || this.url.startsWith('https://');
    }

    _loadUrl() {
        if (!this.winbox) return;

        const container = this.winbox.body;
        const loadingEl = container.querySelector('.app-loading');
        const errorEl = container.querySelector('.app-error');

        // Create iframe with appropriate sandbox
        this.iframe = document.createElement('iframe');
        this.iframe.className = 'app-iframe';

        // Smart sandboxing:
        // - Local files: allow-scripts allow-same-origin (needed for local JS/CSS)
        // - External URLs: allow-scripts allow-forms allow-popups (no same-origin for security)
        if (this._isExternalUrl()) {
            this.iframe.sandbox = 'allow-scripts allow-forms allow-popups';
        } else {
            this.iframe.sandbox = 'allow-scripts allow-same-origin';
        }

        this.iframe.addEventListener('load', () => {
            if (loadingEl) loadingEl.classList.add('hidden');
            if (errorEl) errorEl.classList.add('hidden');
        });

        this.iframe.addEventListener('error', () => {
            if (loadingEl) loadingEl.classList.add('hidden');
            if (errorEl) {
                errorEl.classList.remove('hidden');
                errorEl.querySelector('.app-error-message').textContent =
                    `Failed to load: ${this.url}`;
            }
        });

        this.iframe.src = this._resolveUrl();
        container.insertBefore(this.iframe, container.firstChild);
    }
}
