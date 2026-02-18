---
name: voice
description: Voice communication for agentwire sessions
---

# Voice

You can speak and listen via agentwire's voice system.

## Speaking

Use `say(text)` to speak. Audio routes to the portal browser if connected, otherwise local speakers.

```
say(text="Working on that now")
say(text="[chuckle] Well, that didn't work")
```

Available tags: `[laugh]`, `[sigh]`, `[chuckle]`, `[hmm]`, `[excited]`

## Listening

When you see `[User said: '...']`, the user is speaking via push-to-talk. Respond with `say()`.

## When to speak vs write

**Speak:** Acknowledgements, progress updates, results, questions.
**Write:** Code, file contents, tables, URLs, long explanations.

Keep voice responses to 1-2 sentences.
