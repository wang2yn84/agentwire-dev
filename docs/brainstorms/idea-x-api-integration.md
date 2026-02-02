# X (Twitter) API Integration Research

Research into X API capabilities for AgentWire integration.

## Use Cases

- Reply to mentions automatically
- Read DMs for voice-activated responses
- Real-time monitoring of mentions/keywords

## API Capabilities

### Replying to Users

**Endpoint:** `POST /2/tweets` with reply object

```json
{
  "text": "Your reply text here",
  "reply": {
    "in_reply_to_tweet_id": "tweet_id_you_are_replying_to"
  }
}
```

**Rate Limits:**
- Per App: 10,000 tweets per 24 hours
- Per User: 100 tweets per 15 minutes

### Reading Direct Messages

**Endpoints:**
- `GET /2/dm_conversations/:id/dm_events` - Specific conversation
- `GET /2/dm_events` - All DM events

**Limitations:**
- Events from up to 30 days ago only
- No real-time streaming - polling only
- Rate: 15 requests per 15 minutes per user

### Real-Time Streaming

**Filtered Stream:** `/2/tweets/search/stream`
- Persistent HTTP connection
- Define rules with operators (keywords, accounts, hashtags)
- Keep-alive signals every ~20 seconds
- **Requires Pro tier ($5,000/mo)**

**Alternative (Basic tier):** Poll mentions timeline
- `GET /2/users/:id/mentions`
- Rate: 450/15min (App), 300/15min (User)
- Poll every 15-60 seconds

## Authentication

| Method | Use Case |
|--------|----------|
| OAuth 2.0 with PKCE | User context (posting, DMs) |
| Bearer Token | Public data, no user context |
| OAuth 1.0a | Legacy v1.1 endpoints |

Access tokens valid 2 hours, use `offline.access` scope for refresh tokens.

## Pricing Tiers (2025-2026)

| Tier | Cost | Posts/Month | Key Features |
|------|------|-------------|--------------|
| Free | $0 | 500 | Write-only, no DMs, no streaming |
| Basic | $200/mo | 10,000 | Most v2 endpoints, DMs |
| Pro | $5,000/mo | Higher | Filtered stream, full-archive search |
| Enterprise | $42,000+/mo | Custom | Firehose, historical data |

## Capability Matrix

| Capability | Free | Basic ($200) | Pro ($5,000) |
|------------|------|--------------|--------------|
| Post tweets/replies | 500/mo | 10K/mo | Yes |
| Read tweets | 100/mo | 10K/mo | Yes |
| Mentions timeline | No | Yes | Yes |
| Direct Messages | No | Yes | Yes |
| Filtered Stream | No | No | Yes |
| Full-archive search | No | No | Yes |

## Recommendation

**Basic tier ($200/mo)** for AgentWire:
- Poll `/2/users/:id/mentions` every 30 seconds
- Reply via `POST /2/tweets`
- Read DMs via polling
- Acceptable latency for voice-response use case

Real-time streaming only worth it if response latency is critical.

## Implementation Notes

1. Use OAuth 2.0 with PKCE for user context
2. Store refresh tokens securely
3. Implement exponential backoff for rate limits
4. Consider caching to reduce API calls

## Pay-Per-Use Pilot

X launched a closed beta (late 2025) for usage-based pricing:
- No fixed monthly fees
- Pay per API request
- Not yet generally available
- Worth monitoring for cost optimization
