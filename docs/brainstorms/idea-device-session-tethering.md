# Device-Session Tethering

**Persistently bind specific devices to specific sessions so push-to-talk automatically routes to the right place.**

## Problem

When managing multiple agentwire sessions across devices (phone on the couch, tablet in the kitchen, laptop at the desk), you have to manually select which session to talk to each time. This creates friction in the most common workflow: you have a mental model of "my iPad talks to the orchestrator" and "my phone talks to the website project," but the portal doesn't know that. Every voice interaction starts with a session selection step that breaks flow.

This gets worse with more sessions. If you're running 4-5 project sessions plus a main orchestrator, the cognitive load of picking the right target every time adds up. You end up talking to the wrong session, wasting a command, or just not bothering with voice at all.

## Proposed Solution

### Device Tethering

Each device that connects to the portal can be "tethered" to a specific session. Once tethered, all push-to-talk from that device routes directly to the tethered session with zero selection UI.

```yaml
# Stored in ~/.agentwire/tethers.yaml
tethers:
  - device_id: "ipad-pro-kitchen"
    device_name: "iPad Pro"
    session: "main"
    created: "2026-02-07T10:00:00Z"

  - device_id: "iphone-15"
    device_name: "iPhone"
    session: "agentwire-website"
    created: "2026-02-07T10:05:00Z"

  - device_id: "macbook-desk"
    device_name: "MacBook"
    session: null  # untethered, shows session picker
    created: "2026-02-07T10:10:00Z"
```

### Device Identification

Assign each browser/device a stable fingerprint using a combination of:
- Persistent localStorage token (generated on first portal visit)
- Optional user-assigned device name ("iPad Pro", "Phone", etc.)

No actual browser fingerprinting - just a self-assigned UUID that persists across page reloads.

### Tether Modes

| Mode | Behavior |
|------|----------|
| **Sticky** | Always routes to tethered session. If session dies, shows "session offline" instead of falling back |
| **Fallback** | Routes to tethered session if alive, falls back to session picker if not |
| **Follow** | Routes to whichever session the user last interacted with on ANY device (shared cursor) |

### Portal UI

- Long-press/right-click a session card to tether current device
- Tether indicator icon on session cards showing which devices are connected
- Device management page: see all known devices, their tethers, last-seen timestamps
- Quick untether via swipe or button

### Voice Command Integration

```
"Hey, tether to main" → tethers current device to "main" session
"Untether" → removes tether, returns to session picker
"Switch to website" → re-tethers to "agentwire-website" session
```

### CLI Support

```bash
agentwire tether list                    # show all device tethers
agentwire tether set <device> <session>  # set tether remotely
agentwire tether clear <device>          # remove tether
```

## Implementation Considerations

- **Device ID persistence**: localStorage is per-origin, so the portal URL must stay consistent. Consider a backup cookie for resilience.
- **Session lifecycle**: When a tethered session is killed and recreated (same name), the tether should automatically reconnect. Match by session name, not internal ID.
- **WebSocket routing**: The portal already maintains per-client WebSocket connections. Tethering adds a `target_session` field to each connection that the audio routing layer uses.
- **Multi-tab**: If the same device has multiple portal tabs open, they should share the same device ID and tether. Use BroadcastChannel API or SharedWorker for tab coordination.
- **State sync**: Tether state lives server-side so it survives browser restarts. Device re-identifies via its localStorage token on reconnect.

## Potential Challenges

- **Stale tethers**: Devices that haven't connected in weeks still show up. Need a TTL or "last seen" cleanup (e.g., auto-remove tethers for devices not seen in 30 days).
- **Session name collisions**: If a session is destroyed and a different project reuses the name, the tether points to the wrong thing. Mitigate by clearing tethers when sessions are explicitly killed.
- **Network switching**: Mobile devices changing from WiFi to cellular get new WebSocket connections. The reconnection logic needs to re-apply the tether without user action.
- **Discoverability**: Users need to know tethering exists. First-time portal visitors on a new device could get a subtle prompt: "Want to always talk to [session] from this device?"
