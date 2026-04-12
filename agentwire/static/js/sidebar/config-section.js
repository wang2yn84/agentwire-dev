export const configSection = {
    title: 'Config',
    async mount(body) { await this.refresh(body); },
    async refresh(body) {
        try {
            const res = await fetch('/api/config?format=display');
            const data = await res.json();
            const items = data.items || [];
            body.innerHTML = items.map(({ key, value }) => {
                let display = value;
                if (value === null || value === undefined) display = '<em>null</em>';
                else if (typeof value === 'boolean') display = value ? '✓' : '✗';
                else if (typeof value === 'object') display = `<code>${JSON.stringify(value)}</code>`;
                return `<div class="sidebar-config-item"><span class="sidebar-config-key">${key}</span><span class="sidebar-config-val">${display}</span></div>`;
            }).join('');
        } catch (e) {
            body.innerHTML = '<div class="sidebar-empty">Failed to load config</div>';
        }
    },
};
