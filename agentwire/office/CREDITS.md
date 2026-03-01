> Living document. Update this, don't create new versions.

# Credits

The AgentWire pixel office is built on top of open-source work. We owe thanks to:

## pixel-agents
**Author:** Pablo De Lucca
**License:** MIT
**Source:** https://github.com/pablodelucca/pixel-agents

The office engine, character FSM, tile map, furniture catalog, and Canvas 2D renderer were
forked from pixel-agents and adapted to work with the AgentWire portal instead of VS Code.
The postMessage bridge (`vscodeApi.ts` / `office-window.js`) replaced the VS Code extension API.

Key additions on top of the fork:
- Multi-room dynamic office building (project zones)
- Sub-agent visualization (worker panes as small characters)
- Tool activity overlays (typing/reading animations per tool)
- Permission bubbles and waiting indicators
- Matrix spawn/despawn effects
- Sleep state (characters sleep after 30min of inactivity)
- Portal postMessage bridge for session/agent state

## MetroCity Free Top-Down Character Pack
**Author:** Jik-A
**License:** CC0 (public domain)
**Source:** https://jik-a-4.itch.io/metrocity-free-topdown-character-pack

The character sprite sheets (`char_0.png` – `char_5.png`) and wall tile art (`walls.png`)
come from this asset pack. CC0 means no attribution is legally required, but we include it
here because it's the right thing to do.
