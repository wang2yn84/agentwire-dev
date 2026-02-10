# Spatial Voice Mixing

> Position each session's voice in a different location in the stereo field so you can monitor multiple sessions by ear alone.

## Problem

When running 3-5 sessions simultaneously (common in multi-project orchestration), all voice output comes from the same audio source. Every `agentwire_say` sounds identical in position - you have to actively listen to the content to figure out which session is speaking. This breaks the passive monitoring workflow: you can't walk away from the screen and still know what's happening where.

Humans are remarkably good at separating spatially-positioned audio sources (the "cocktail party effect"), but agentwire doesn't exploit this at all. Every session sounds like it's standing in the same spot.

## Why This Matters

- **Multi-session monitoring is the core use case** - orchestrators managing 3+ project sessions need ambient awareness without screen-watching
- **Cognitive load** - parsing which session is speaking from content alone requires active attention, defeating the purpose of voice
- **Missed context** - when two sessions speak in quick succession, the second often gets mentally attributed to the first
- **Mobile/tablet usage** - users monitoring from another room or device benefit most from spatial differentiation

## Proposed Solution

### Core Mechanism: Stereo Panning Per Session

Each active session gets assigned a position in the stereo field. When TTS audio is generated for that session, it's panned to its assigned position before playback.

```
Left ◄─────────────────────────────► Right

  frontend    orchestrator    backend     api-worker
  (-0.7)         (0.0)        (0.5)        (0.8)
```

### Position Assignment

Positions are assigned automatically based on session order but can be overridden in `.agentwire.yml`:

```yaml
# .agentwire.yml
voice: may
pan: -0.7    # -1.0 (full left) to 1.0 (full right), 0.0 = center
```

The orchestrator (parent session) always defaults to center (0.0). Child sessions spread evenly across the remaining field.

**Auto-assignment algorithm:**
1. Parent/main session → center (0.0)
2. First child → -0.6 (left)
3. Second child → 0.6 (right)
4. Additional children fill gaps: -0.3, 0.3, -0.9, 0.9

### CLI & MCP Integration

```bash
# Set pan position for a session
agentwire pan -s frontend -0.7

# View current spatial layout
agentwire pan list
# Output:
#   main         ▏    ████████████████████    ▕  center (0.0)
#   frontend     ▏████████                    ▕  left (-0.7)
#   backend      ▏              ████████████  ▕  right (0.5)
#   api-worker   ▏                ████████████▕  right (0.8)

# Say with explicit pan override (one-shot, doesn't change session default)
agentwire say --pan -0.5 "Build complete"
```

MCP tool extension:
```python
agentwire_say(text="Build complete", pan=-0.7)  # Override for this utterance
```

### Audio Processing

The panning happens at the portal level, after TTS generation but before browser playback:

```
TTS Engine → mono audio → StereoMixer → panned stereo → WebSocket → browser
```

**StereoMixer** applies constant-power panning:

```python
import math

def pan_audio(mono_samples: bytes, pan: float) -> bytes:
    """Apply stereo panning. pan: -1.0 (left) to 1.0 (right)."""
    # Constant-power panning law
    angle = (pan + 1.0) / 2.0 * (math.pi / 2.0)
    left_gain = math.cos(angle)
    right_gain = math.sin(angle)
    # Interleave left/right channels with gains applied
    ...
```

This is lightweight - just a gain multiplication per sample, no FFT or convolution needed.

### Portal Browser Playback

The portal already uses the Web Audio API for TTS playback. Spatial mixing fits naturally:

```javascript
// In portal audio handler
const audioCtx = new AudioContext();
const pannerNode = audioCtx.createStereoPanner();
pannerNode.pan.value = sessionPan; // -1.0 to 1.0
source.connect(pannerNode).connect(audioCtx.destination);
```

The `StereoPannerNode` is supported in all modern browsers and adds zero latency.

### Visual Feedback in Portal

The portal session list shows spatial position as a visual indicator:

```
┌─────────────────────────────────────┐
│  ◄── frontend    main    backend ──►│
│      (-0.7)     (0.0)    (0.5)     │
└─────────────────────────────────────┘
```

Drag-to-reposition in the portal UI for quick adjustments.

## Advanced: Distance as Volume

Extend the spatial metaphor - sessions that are "closer" (more important or active) are louder:

```yaml
# .agentwire.yml
pan: -0.7
distance: 0.3   # 0.0 = loudest (closest), 1.0 = quietest (furthest)
```

The orchestrator could dynamically adjust distance based on session activity:
- Session with failing tests → distance 0.0 (loud, close)
- Idle session → distance 0.8 (quiet, far)
- Session awaiting input → distance 0.2 (prominent)

This creates an "audio landscape" where active/urgent sessions naturally demand attention.

## Implementation Considerations

1. **TTS output format** - Current TTS pipeline outputs mono. The mixer just needs to duplicate to stereo with gain - trivial to add between TTS response and WebSocket send
2. **Local speaker fallback** - When audio routes to local speakers instead of portal, use `ffplay` or `sox` for panning: `sox input.wav output.wav remix 1v0.3 1v0.7`
3. **Headphones vs speakers** - Spatial separation is dramatically more effective with headphones. Could detect via Web Audio API's `AudioContext.destination.channelCount` and adjust separation accordingly
4. **Config persistence** - Pan positions stored in each project's `.agentwire.yml`, auto-assignment positions stored in `~/.agentwire/spatial.yaml`

## Potential Challenges

- **Speaker users** - Stereo separation is subtle on laptop speakers. Consider adding subtle pitch shifting or voice variation as a fallback differentiator for non-headphone setups
- **Too many sessions** - Beyond 5-6 sessions, the stereo field gets crowded. Could group related sessions (same parent) into spatial clusters
- **Dynamic session count** - Sessions starting/stopping changes the layout. Need smooth transitions (fade pan over 2-3 seconds) to avoid jarring jumps
- **Portal vs local audio** - Two different audio pipelines need the same panning logic. The portal path (Web Audio API) is trivial; the local path (sox/ffmpeg) adds a dependency
