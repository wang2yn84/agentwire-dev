/**
 * Sidebar - auto-hide left navigation panel
 *
 * Phase 1: shell + utility chrome (clock / global PTT / voice indicator).
 * Phase 2: hosts "Open Windows" accordion (replaces bottom taskbar).
 * Phase 3: hosts Sessions/Machines/Projects/Artifacts/Scheduler/Config accordion sections.
 */

const PIN_KEY = 'sidebar-pinned';

export const sidebar = {
    el: null,
    hotzone: null,
    pinBtn: null,
    _closeTimer: null,

    init() {
        this.el = document.getElementById('sidebar');
        this.hotzone = document.getElementById('sidebarHotzone');
        this.pinBtn = document.getElementById('sidebarPin');
        if (!this.el || !this.hotzone) return;

        // Restore pinned state from localStorage
        if (localStorage.getItem(PIN_KEY) === 'true') {
            this.pin();
        }

        // Hover hotzone → open
        this.hotzone.addEventListener('mouseenter', () => this.open());

        // Also open when hovering the sidebar itself (prevents close-on-reenter race)
        this.el.addEventListener('mouseenter', () => {
            if (this._closeTimer) {
                clearTimeout(this._closeTimer);
                this._closeTimer = null;
            }
        });

        // Mouse leave sidebar → close (unless pinned)
        this.el.addEventListener('mouseleave', () => {
            if (this.isPinned()) return;
            // Small delay to avoid flicker when passing over child elements briefly
            this._closeTimer = setTimeout(() => this.close(), 120);
        });

        // ESC closes (unless pinned)
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isOpen() && !this.isPinned()) {
                this.close();
            }
        });

        // Pin toggle
        this.pinBtn?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.isPinned() ? this.unpin() : this.pin();
        });
    },

    open() {
        if (!this.el) return;
        this.el.classList.add('open');
    },

    close() {
        if (!this.el || this.isPinned()) return;
        this.el.classList.remove('open');
    },

    toggle() {
        this.isOpen() ? this.close() : this.open();
    },

    isOpen() {
        return !!this.el && this.el.classList.contains('open');
    },

    isPinned() {
        return !!this.el && this.el.classList.contains('pinned');
    },

    pin() {
        if (!this.el) return;
        this.el.classList.add('pinned');
        this.el.classList.add('open');
        document.body.classList.add('sidebar-pinned');
        try { localStorage.setItem(PIN_KEY, 'true'); } catch (e) {}
    },

    unpin() {
        if (!this.el) return;
        this.el.classList.remove('pinned');
        document.body.classList.remove('sidebar-pinned');
        try { localStorage.setItem(PIN_KEY, 'false'); } catch (e) {}
    },
};
