> Living document. Update this, don't create new versions.

# Mission: X API Integration

**Status:** Investigation Complete
**Branch:** TBD

## Goal

Enable AgentWire to post tweets to X (Twitter) using the free API tier.

## Use Case

- Automated posting (~10-20 posts/day)
- Leave headroom for manual posts
- No need to read mentions/replies (yet)

## API Tier

**Free Tier ($0/month):**
- 1,500 posts/month (~50/day)
- Write-only (no reading tweets/mentions/DMs)
- 1 App, 1 Project
- OAuth 1.0a authentication

## Setup Requirements

1. **Developer Account**
   - Sign up at [developer.x.com](https://developer.x.com)
   - Use existing X account
   - Instant approval for free tier

2. **Create App**
   - Create new Project in dashboard
   - Add App to project
   - Enable OAuth 1.0a with "Read and Write" permissions

3. **Get Credentials**
   - API Key (Consumer Key)
   - API Secret (Consumer Secret)
   - Access Token
   - Access Token Secret
   - Store in `~/.agentwire/.env` or env vars

## Implementation

### Library: Tweepy

Python wrapper for X API. Handles OAuth complexity.

```bash
pip install tweepy
```

### Basic Usage

```python
import tweepy

# Auth with OAuth 1.0a (required for posting)
client = tweepy.Client(
    consumer_key="API_KEY",
    consumer_secret="API_SECRET",
    access_token="ACCESS_TOKEN",
    access_token_secret="ACCESS_TOKEN_SECRET"
)

# Post a tweet
response = client.create_tweet(text="Hello from AgentWire!")
print(f"Tweet ID: {response.data['id']}")
```

### AgentWire Integration

Options:
1. **CLI command**: `agentwire tweet "message"`
2. **MCP tool**: `agentwire_tweet(text="message")`
3. **Scheduled task**: Post from task prompts

## Tasks

- [ ] Create X developer account
- [ ] Create project and app in dashboard
- [ ] Get API credentials
- [ ] Add `tweepy` to dependencies
- [ ] Implement `agentwire tweet` CLI command
- [ ] Add MCP tool for agents
- [ ] Test posting
- [ ] Document in CLAUDE.md

## Files to Create/Modify

| File | Changes |
|------|---------|
| `pyproject.toml` | Add tweepy dependency |
| `agentwire/x_api.py` | X API wrapper module |
| `agentwire/__main__.py` | Add `tweet` command |
| `agentwire/mcp_server.py` | Add tweet MCP tool |
| `~/.agentwire/.env` | Store credentials |

## Security Notes

- Never commit API credentials
- Store in `.env` file (gitignored)
- Regenerate tokens if exposed
- Rate limit: 1,500 posts/month total

## References

- [X API Documentation](https://developer.x.com/en/docs)
- [Tweepy Documentation](https://docs.tweepy.org/)
- [X API Pricing](https://getlate.dev/blog/twitter-api-pricing)
