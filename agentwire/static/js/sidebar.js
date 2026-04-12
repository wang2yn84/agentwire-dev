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

    // --- Accordion sections (Phase 3) ---
    _sections: new Map(),

    addSection(id, sectionObj) {
        const container = document.getElementById('sidebarSections');
        if (!container) return;
        const section = document.createElement('div');
        section.className = 'sidebar-section';
        section.dataset.sectionId = id;

        const header = document.createElement('div');
        header.className = 'sidebar-section-title sidebar-section-toggle';
        header.innerHTML = `<span class="sidebar-section-chevron">▸</span> ${sectionObj.title}`;
        header.addEventListener('click', () => this.toggleSection(id));
        section.appendChild(header);

        const body = document.createElement('div');
        body.className = 'sidebar-section-body';
        body.style.display = 'none';
        section.appendChild(body);

        container.appendChild(section);

        const entry = { id, sectionObj, el: section, header, body, mounted: false, refreshTimer: null };
        this._sections.set(id, entry);
    },

    expandSection(id) {
        const entry = this._sections.get(id);
        if (!entry) return;
        this.open();
        entry.body.style.display = '';
        entry.header.querySelector('.sidebar-section-chevron').textContent = '▾';
        entry.el.classList.add('expanded');
        const s = entry.sectionObj;
        if (!entry.mounted) {
            entry.mounted = true;
            s.mount(entry.body);
        } else if (s.refresh) {
            s.refresh(entry.body);
        }
        if (s.autoRefreshMs && !entry.refreshTimer) {
            entry.refreshTimer = setInterval(() => {
                if (entry.el.classList.contains('expanded') && s.refresh) {
                    s.refresh(entry.body);
                }
            }, s.autoRefreshMs);
        }
        entry.body.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    },

    collapseSection(id) {
        const entry = this._sections.get(id);
        if (!entry) return;
        entry.body.style.display = 'none';
        entry.header.querySelector('.sidebar-section-chevron').textContent = '▸';
        entry.el.classList.remove('expanded');
        if (entry.refreshTimer) {
            clearInterval(entry.refreshTimer);
            entry.refreshTimer = null;
        }
    },

    toggleSection(id) {
        const entry = this._sections.get(id);
        if (!entry) return;
        entry.el.classList.contains('expanded') ? this.collapseSection(id) : this.expandSection(id);
    },
};
