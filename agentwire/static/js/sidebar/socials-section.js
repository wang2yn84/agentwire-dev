import { getAllSessions, isSocial, renderCard, handleSessionClick, onSessionsChanged } from './sessions-section.js';

export const socialsSection = {
    title: 'Socials',
    _body: null,

    async mount(body) {
        this._body = body;
        onSessionsChanged(() => this._render(body));
        this._render(body);
    },

    async refresh(body) {
        this._render(body);
    },

    _render(body) {
        const socials = getAllSessions().filter(s => isSocial(s));
        if (!socials.length) {
            body.innerHTML = '<div class="sidebar-empty">No socials</div>';
            return;
        }
        body.innerHTML = socials.map(s => renderCard(s)).join('');
        body.onclick = (e) => handleSessionClick(e);
    },
};
