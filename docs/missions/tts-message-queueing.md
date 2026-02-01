> Living document. Update this, don't create new versions.

# Mission: TTS Message Queueing

**Status:** Complete
**Goal:** Ensure TTS messages play sequentially without overlap, even when multiple arrive simultaneously

## Problem

Currently, when multiple TTS messages arrive close together (e.g., multiple workers reporting completion), they can overlap or interleave. Messages should queue and play one after another.

## Architecture Challenge

The system is distributed:
- **TTS Generator:** RunPod server (or local dotdev-pc)
- **Audio Player:** Portal browser (tablet/phone) or local speakers
- **Message Sender:** CLI/MCP tools from various agents

```
Agent 1 ─┐
Agent 2 ─┼──→ Portal ──→ TTS Server ──→ Audio (browser/local)
Agent 3 ─┘
```

The queueing could happen at:
1. **CLI/MCP level** - queue before sending to portal
2. **Portal level** - queue incoming requests
3. **TTS server level** - queue generation requests
4. **Player level** - queue audio chunks

## Current Flow

1. `agentwire say "text"` or `agentwire_say(text="...")`
2. CLI calls portal `/api/speak` endpoint
3. Portal calls TTS server `/generate` endpoint
4. TTS server returns audio URL/data
5. Portal sends audio to browser via WebSocket
6. Browser plays audio

## Proposed Solution

### Option A: Portal-Level Queue (Recommended)

The portal is the central hub - it knows about all sessions and can coordinate.

```python
# In server.py
class TTSQueue:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.processing = False

    async def add(self, text: str, session: str, voice: str):
        await self.queue.put({"text": text, "session": session, "voice": voice})
        if not self.processing:
            asyncio.create_task(self._process())

    async def _process(self):
        self.processing = True
        while not self.queue.empty():
            item = await self.queue.get()
            # Generate TTS
            audio = await generate_tts(item["text"], item["voice"])
            # Play to session (wait for completion)
            await play_to_session(item["session"], audio)
            # Small gap between messages
            await asyncio.sleep(0.3)
        self.processing = False
```

**Pros:**
- Single point of coordination
- Can prioritize by session
- Works with any TTS backend

**Cons:**
- Requires portal to know when audio finishes playing

### Option B: Browser-Level Queue (CHOSEN)

The browser queues audio playback. Generator works async, browser stacks and plays sequentially.

```javascript
class AudioQueue {
    constructor() {
        this.queue = [];
        this.playing = false;
    }

    add(audioUrl) {
        this.queue.push(audioUrl);
        if (!this.playing) this.playNext();
    }

    async playNext() {
        if (this.queue.length === 0) {
            this.playing = false;
            return;
        }
        this.playing = true;
        const url = this.queue.shift();
        const audio = new Audio(url);
        audio.onended = () => this.playNext();
        await audio.play();
    }
}
```

**Pros:**
- Simple implementation
- Browser knows exactly when audio ends
- Works without portal changes

**Cons:**
- Each browser has its own queue
- Doesn't help with local speaker playback

### Option C: TTS Server Queue

The TTS server queues generation requests.

**Pros:**
- Prevents overloading TTS GPU

**Cons:**
- Doesn't control playback timing
- Adds latency

## Implementation Steps

### Phase 1: Browser Audio Queue (Primary Solution)

1. Add `AudioQueue` class to portal's browser JavaScript
2. WebSocket `play_audio` messages go into queue instead of playing immediately
3. Queue plays next audio when current `ended` event fires
4. Small delay between messages (~300ms)

```javascript
// In portal static JS
class AudioQueue {
    queue = [];
    playing = false;

    add(audioUrl) {
        this.queue.push(audioUrl);
        if (!this.playing) this.playNext();
    }

    async playNext() {
        if (this.queue.length === 0) {
            this.playing = false;
            return;
        }
        this.playing = true;
        const audio = new Audio(this.queue.shift());
        audio.onended = () => {
            setTimeout(() => this.playNext(), 300);
        };
        await audio.play();
    }
}
```

### Phase 2: (Optional) Portal Acknowledgment

If we need portal to know when audio finishes:
1. Browser sends `audio_finished` WebSocket message
2. Portal can use this for logging/debugging

## Technical Details

### WebSocket Protocol Addition

```json
// Portal -> Browser
{"type": "play_audio", "url": "...", "id": "msg-123"}

// Browser -> Portal
{"type": "audio_finished", "id": "msg-123"}
```

### Portal Queue Structure

```python
@dataclass
class PendingAudio:
    id: str
    session: str
    text: str
    voice: str
    status: str  # "queued", "generating", "playing", "done"

class SessionAudioQueue:
    pending: dict[str, list[PendingAudio]]  # session -> queue
    current: dict[str, str | None]  # session -> current audio id
```

## Questions to Investigate

1. How does RunPod TTS handle concurrent requests?
2. What's the latency for audio generation?
3. Can browser Audio element reliably report `ended`?
4. How to handle browser disconnect during playback?
5. Should queue be per-session or global?

## Files Modified

- `agentwire/static/js/desktop-manager.js` - Added audio queue to DesktopManager class

## Implementation Notes

Added to DesktopManager constructor:
```javascript
this._audioQueue = [];      // Queue of {base64Data, session} objects
this._audioPlaying = false; // Whether audio is currently playing
```

Modified `_playAudio()` to queue audio instead of playing immediately:
- Adds audio to `_audioQueue`
- Calls `_playNextAudio()` if not already playing

Added `_playNextAudio()` method:
- Shifts next item from queue
- Plays audio via AudioBufferSourceNode
- On `source.onended`, waits 300ms then plays next
- On error, retries next after 100ms

## Acceptance Criteria

- [x] Multiple rapid `agentwire say` commands play sequentially
- [x] No audio overlap
- [x] Small gap between messages (~300ms)
- [x] Works with browser playback
- [ ] Works with local speaker playback (not implemented - browser only)
- [ ] Queue persists across brief disconnects (queue is in-memory)
