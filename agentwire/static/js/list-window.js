/**
 * ListWindow - Reusable list-based window component
 *
 * Creates WinBox windows with scrollable lists, customizable item rendering,
 * action handlers, and optional auto-refresh.
 */

import { desktop } from './desktop-manager.js';

export class ListWindow {
    /**
     * @param {Object} options
     * @param {string} options.id - Unique window identifier
     * @param {string} options.title - Window title
     * @param {string} [options.icon] - Icon URL (defaults to favicon)
     * @param {Function} options.fetchData - Async function returning array of items
     * @param {Function} options.renderItem - Function(item) returning HTML string
     * @param {Function} [options.onItemAction] - Handler for item actions: function(action, item, event)
     * @param {string} [options.emptyMessage='No items'] - Message when list is empty
     * @param {HTMLElement} [options.root] - Root element for WinBox (defaults to document body)
     */
    constructor(options) {
        this.id = options.id;
        this.title = options.title;
        this.icon = options.icon || '/static/favicon.png';
        this.fetchData = options.fetchData;
        this.renderItem = options.renderItem;
        this.onItemAction = options.onItemAction || (() => {});
        this.emptyMessage = options.emptyMessage || 'No items';
        this.root = options.root || document.body;

        this.winbox = null;
        this.container = null;
        this.contentEl = null;
        this.isLoading = false;
    }

    /**
     * Open the window and load initial data
     * @returns {WinBox} The WinBox instance
     */
    open() {
        // Don't create duplicate windows
        if (this.winbox) {
            this.winbox.focus();
            return this.winbox;
        }

        // Create container structure
        this.container = document.createElement('div');
        this.container.className = 'list-window';
        this.container.innerHTML = `
            <div class="list-header">
                <span class="list-title">${this.title}</span>
                <button class="list-refresh-btn" title="Refresh">↻</button>
            </div>
            <div class="list-content">
                <div class="list-loading">Loading...</div>
            </div>
        `;

        this.contentEl = this.container.querySelector('.list-content');

        // Attach refresh button handler
        const refreshBtn = this.container.querySelector('.list-refresh-btn');
        refreshBtn.addEventListener('click', () => this.refresh());

        // Create WinBox — always maximized, no dragging/resizing
        this.winbox = new WinBox({
            title: this.title,
            icon: this.icon,
            mount: this.container,
            root: this.root,
            width: '100%',
            height: '100%',
            minwidth: 300,
            minheight: 200,
            class: ['list-window-box', 'no-full', 'no-resize', 'no-move'],
            onclose: () => this._onClose(),
            onfocus: () => {
                desktop.setActiveWindow(this.id);
            },
            onmaximize: () => {
                desktop.setActiveWindow(this.id);
            },
            onminimize: () => {
                desktop.emit('window_minimized', { id: this.id });
            },
            onrestore: () => {
                desktop.emit('window_restored', { id: this.id });
                desktop.setActiveWindow(this.id);
            }
        });

        // Always open maximized
        this.winbox.maximize();

        // Register with desktop manager
        desktop.registerWindow(this.id, this.winbox);

        // Initial data fetch (with loading indicator)
        this.refresh(true);

        return this.winbox;
    }

    /**
     * Close the window and clean up
     */
    close() {
        if (this.winbox) {
            this.winbox.close();
        }
    }

    /**
     * Minimize the window.
     */
    minimize() {
        if (this.winbox) {
            this.winbox.minimize();
        }
    }

    /**
     * Restore the window from minimized state.
     */
    restore() {
        if (this.winbox) {
            this.winbox.restore();
        }
    }

    /**
     * Check if window is minimized.
     */
    get isMinimized() {
        return this.winbox ? this.winbox.min : false;
    }

    /**
     * Refresh the list data
     * @param {boolean} [showLoading=false] - Show loading state (only on initial load)
     */
    async refresh(showLoading = false) {
        if (this.isLoading || !this.contentEl) return;

        this.isLoading = true;

        // Only show loading on initial load, not on subsequent refreshes
        if (showLoading) {
            this._showLoading();
        }

        try {
            const items = await this.fetchData();
            this._render(items);
        } catch (error) {
            console.error(`ListWindow[${this.id}] fetch error:`, error);
            this._showError('Failed to load data');
        } finally {
            this.isLoading = false;
        }
    }

    /**
     * Refresh the list with provided data (skip fetch)
     * @param {Array} items - Items to render
     */
    refreshWithData(items) {
        if (this.isLoading || !this.contentEl) return;
        this._render(items);
    }

    /**
     * Render items into the list
     * @param {Array} items
     */
    _render(items) {
        if (!this.contentEl) return;

        if (!items || items.length === 0) {
            this._showEmpty();
            return;
        }

        // Build list HTML
        const html = items.map((item, index) => {
            const itemHtml = this.renderItem(item, index);
            return `<div class="list-item" data-index="${index}">${itemHtml}</div>`;
        }).join('');

        this.contentEl.innerHTML = html;

        // Attach action handlers to buttons with data-action
        this.contentEl.querySelectorAll('[data-action]').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const action = el.dataset.action;
                const itemEl = el.closest('.list-item');
                const index = parseInt(itemEl?.dataset.index, 10);
                const item = items[index];
                if (item) {
                    this.onItemAction(action, item, e);
                }
            });
        });

        // Attach click handler to list items themselves
        this.contentEl.querySelectorAll('.list-item').forEach((itemEl, index) => {
            itemEl.addEventListener('click', (e) => {
                // Only fire if click wasn't on a button/action element
                if (!e.target.closest('[data-action]')) {
                    const item = items[index];
                    if (item) {
                        this.onItemAction('select', item, e);
                    }
                }
            });
        });
    }

    /**
     * Show loading state
     */
    _showLoading() {
        if (this.contentEl) {
            this.contentEl.innerHTML = '<div class="list-loading">Loading...</div>';
        }
    }

    /**
     * Show empty state
     */
    _showEmpty() {
        if (this.contentEl) {
            this.contentEl.innerHTML = `<div class="list-empty">${this.emptyMessage}</div>`;
        }
    }

    /**
     * Show error state
     * @param {string} message
     */
    _showError(message) {
        if (this.contentEl) {
            this.contentEl.innerHTML = `<div class="list-error">${message}</div>`;
        }
    }

    /**
     * Handle window close
     */
    _onClose() {
        // Call custom cleanup if provided
        if (this._cleanup) {
            this._cleanup();
        }

        // Unregister from desktop manager
        desktop.unregisterWindow(this.id);

        // Clean up references
        this.winbox = null;
        this.container = null;
        this.contentEl = null;
    }

}
