# Ambient Listening Mode

**One-liner:** Hands-free voice interaction through continuous listening with wake word detection.

## Problem

Push-to-talk requires users to click a button before speaking. This interrupts flow, especially when:
- Hands are on keyboard coding
- Using a tablet propped up nearby
- Walking around while debugging
- Want to quickly ask "what's the status?" without context-switching

The current model is **voice as output** (agent speaks) + **manual input** (user clicks button). We want **voice as bidirectional ambient channel**.

## Proposed Solution

Add an optional "ambient listening" mode where:

1. **Continuous transcription** runs in the background (low-power VAD + STT)
2. **Wake phrase detection** ("Hey Echo", configurable) activates command mode
3. **End-of-utterance detection** determines when user is done speaking
4. **Confirmation feedback** plays a subtle audio cue when command is received

### User Flow

```
[Ambient mode on, user is coding]
User: "Hey Echo"
Echo: *chime*
User: "What's the status of the auth worker?"
Echo: *processes, then speaks response*
[Returns to ambient listening]
```

### Modes

| Mode | Trigger | Use Case |
|------|---------|----------|
| Push-to-talk | Click button | Noisy environments, shared spaces |
| Ambient | Wake phrase | Solo work, hands-free |
| Hybrid | Either works | Default - use what's convenient |

## Implementation

### Wake Word Detection

Use [Porcupine](https://picovoice.ai/platform/porcupine/) or similar edge wake word engine:
- Runs locally (privacy-preserving)
- Sub-50ms latency
- Custom wake phrases ("Hey Echo", "Hey Agent", project name)
- Low CPU/battery usage

```python
# New STT component for wake word
class AmbientListener:
    def __init__(self, wake_phrases: list[str]):
        self.porcupine = pvporcupine.create(keywords=wake_phrases)
        self.active = False

    def process_audio_frame(self, frame):
        keyword_index = self.porcupine.process(frame)
        if keyword_index >= 0:
            self.active = True
            play_activation_chime()
            return self.capture_command()
```

### Continuous VAD (Voice Activity Detection)

Only send audio to STT when speech is detected:
- WebRTC VAD or Silero VAD for speech detection
- Buffer pre-roll (capture 500ms before detection to avoid cutting words)
- End-of-speech detection (1.5s silence = command complete)

### Portal Changes

```yaml
# config.yaml additions
stt:
  ambient:
    enabled: true
    wake_phrases: ["hey echo", "hey agent"]
    activation_sound: "~/.agentwire/sounds/activate.wav"
    silence_threshold_ms: 1500
    pre_roll_ms: 500
```

Portal WebSocket protocol extension:
```typescript
// New message types
{ type: "ambient_start" }          // Enable ambient mode
{ type: "ambient_stop" }           // Disable
{ type: "wake_detected" }          // Server -> client: wake heard
{ type: "command_complete" }       // Server -> client: utterance done
```

### Browser Audio

Use Web Audio API for continuous capture:
```typescript
// Existing: capture on button hold
// New: continuous capture with local VAD
const audioContext = new AudioContext();
const vadProcessor = new VADProcessor(); // WASM module

mediaStream.connect(vadProcessor);
vadProcessor.onSpeechStart = () => startBuffering();
vadProcessor.onSpeechEnd = () => sendToServer();
```

## Potential Challenges

### 1. Privacy / Accidental Activation

**Risk:** User says something that sounds like wake phrase, or forgets mic is live.

**Mitigations:**
- Clear visual indicator when ambient mode is active (pulsing mic icon)
- Distinctive activation chime so user knows they triggered it
- Auto-disable after X minutes of no activation
- "What did you hear?" command to replay last captured audio

### 2. Battery/CPU on Mobile

**Risk:** Continuous audio processing drains battery.

**Mitigations:**
- Wake word detection is very lightweight (<1% CPU on Porcupine)
- VAD runs on device, only streams when speech detected
- Option to auto-disable ambient mode on battery below X%

### 3. Multi-Device Conflicts

**Risk:** User has portal open on laptop and tablet, both hear wake phrase.

**Mitigations:**
- Only one device can be in ambient mode at a time
- First device to detect wake phrase wins (WebSocket lock)
- Or: device proximity detection via audio fingerprinting

### 4. Background Noise

**Risk:** TV, music, other people talking trigger false activations.

**Mitigations:**
- Porcupine has very low false activation rate (<1 per week typical)
- Adjustable sensitivity threshold
- "Cancel" command to abort if activated accidentally

### 5. Latency

**Risk:** Wake word + STT + LLM round-trip feels slow.

**Mitigations:**
- Stream transcription as user speaks (show partial results)
- Pre-warm LLM context while transcription is happening
- Target: <500ms from end-of-speech to agent starts responding

## Success Metrics

- **Adoption:** % of sessions using ambient mode
- **False activation rate:** <1 per 8-hour session
- **Command completion rate:** % of ambient commands successfully executed
- **User preference:** Survey data on push-to-talk vs ambient

## Future Extensions

- **Continuous conversation mode:** After response, briefly listen for follow-up without wake word
- **Context-aware wake phrases:** "Hey Frontend" routes to frontend worker, "Hey Backend" to backend
- **Voice authentication:** Only respond to registered voices
- **Whisper mode:** Respond quietly when user whispers the command
