> Living document. Update this, don't create new versions.

# AgentWire — Mission & Objective

## The Pitch

**AgentWire turns Claude Code into a network.** Voice control, multi-machine orchestration, persistent sessions, and scheduled agents — the infrastructure layer that Claude Code doesn't ship with. Talk to your agents out loud, run them across multiple machines simultaneously, and let them work while you sleep. We built it first, and it actually works.

---

## The Problem

Claude Code is a great tool for one session on one machine. The moment you want more — voice feedback, multiple coordinated sessions, remote machines, background scheduled work, a mobile interface — there's nothing. You're on your own.

Workarounds exist, but they're fragile. Nobody has solved multi-machine properly. Nobody built bidirectional voice that actually feels natural. Nobody made it easy to have agents running while you're away from your desk.

AgentWire solves all of it, out of the box.

---

## What Makes Us Different

### Voice — Better Than `/voice`
Claude Code shipped `/voice` mode after we did. Ours is still better: bidirectional, session-aware, works across machines, plays through any device. It's not a feature — it's a first-class interface.

### Multi-Machine — Nobody Else Does This Well
Run sessions on your GPU box, your Mac, your cloud VM — coordinated, tunneled, accessible from the portal. Other tools nominally "support" remote machines. Ours works out of the box.

### Session Orchestration
Spawn workers, assign roles, let them coordinate. The orchestrator/worker pattern with idle notifications and voice summaries is something no other tool in this space has thought through.

### Ahead of the Curve
We added voice before Claude Code did. We built scheduled tasks, multi-machine, and agent-to-agent communication before anyone else. The pattern holds: we see what's needed, build it, and the ecosystem catches up.

---

## Target Users

**Primary:** Solo developers using Claude Code daily. People who have one or more machines running agents, want to talk to them naturally, and want work happening in the background.

**Secondary:** Dev teams where multiple people share an agentwire setup — each with their own voice identity, sessions, and scheduled workloads.

**Not targeting:** Enterprise, non-developers, people who don't already use Claude Code.

---

## Community Goal

AgentWire is open source. The goal isn't just downloads — it's building a community of developers who follow the work, contribute ideas, and enable dotdev to keep building interesting things full-time.

**What success looks like:**
- GitHub stars and watchers (visibility, credibility)
- Discord members who are active and engaged
- YouTube / podcast audience watching the builds happen live
- Eventually: Patreon or equivalent — people paying to support the project because they find value in it and want it to continue

The community goal is honest: if enough people find this useful and follow the work, dotdev can spend all their time building open source tools. That's the mission behind the mission.

---

## North Star

An always-on personal AI presence that feels natural to interact with, runs across your whole dev setup, and handles real work in the background — while you stay in control via voice from any device.

When that's the default experience for a dev using Claude Code, AgentWire has succeeded.

---

## Current State

AgentWire works. Voice, multi-machine, sessions, orchestration, scheduler, Telegram bridge, artifact canvas, email notifications — all shipped and running. The gap right now is **discovery**: it's an amazing tool that nobody knows about yet.

The next phase is less about building features and more about getting it in front of people.

---

## What's Next

- **Visibility** — YouTube demos, writeups, social presence. Show people what this looks like in action.
- **Onboarding** — Make the first 10 minutes excellent. The init wizard exists; it needs to be frictionless.
- **Community** — Discord, GitHub engagement, responding to issues. Build the audience.
- **Later/Later** — Multi-channel bus, personal AI replica features, deeper team support.
