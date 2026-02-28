/**
 * Bridge replacement for VS Code API.
 * Posts messages to the parent window (portal) instead of VS Code extension host.
 */
export const vscode = {
  postMessage(msg: unknown): void {
    window.parent.postMessage({ source: 'office', payload: msg }, '*');
  }
};
