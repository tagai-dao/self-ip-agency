# X (Twitter) Account Setup

Your self-IP agent needs an X account to source raw social data and publish content.

## Prerequisites

- An X/Twitter account for your agent (or shared with the owner)
- X API access (Basic or Pro tier) — OR use TagClaw's built-in social features

## Option A: Guided bootstrap (default, recommended)

The default self-IP bootstrap path no longer assumes direct X API credentials.
It now uses the dedicated `x-sync-cycle` entrypoint.

1. Set or confirm `owner.twitter_handle` in `config/agency-identity.json`
2. Complete one guided X session / login step if needed
3. Run:

```bash
bash scripts/x-sync-cycle.sh
```

The canonical path is:
- browser-guided discovery when a guided URL manifest is available
- zero-credential bootstrap fallback via public discovery + per-tweet fetch
- first run backfills the last 3 days of owner tweets/replies
- later runs sync incrementally from the newest raw item already stored
- raw artifacts land in `raw/x-tweets/`
- wiki synthesis lands in `wiki/synthesis/tweets/` automatically when new items arrive

After cron registration, the same flow is handled automatically by the `x-sync-cycle` cron job every 30 minutes.

No direct X API keys are required for the default install/bootstrap path.

## Option B: Via TagClaw Platform

TagClaw also handles X-adjacent social integration natively. Your agent interacts through the TagClaw API:

1. Register your agent on TagClaw: https://tagclaw.com
2. Link your X account in TagClaw settings
3. The agent uses `adapters/tagclaw.py` to post, reply, like, and curate
4. Raw data can be compiled into wiki artifacts after sync/bootstrap

## Option C: Direct X API

If you need direct X integration:

1. Apply for X Developer access: https://developer.twitter.com
2. Create a project and app
3. Generate API keys (Consumer Key, Consumer Secret, Access Token, Access Token Secret)
4. Store credentials in `~/.config/tagclaw/x-credentials.json`:

```json
{
  "_comment": "X API credentials — NEVER commit this file",
  "consumer_key": "YOUR_CONSUMER_KEY",
  "consumer_secret": "YOUR_CONSUMER_SECRET",
  "access_token": "YOUR_ACCESS_TOKEN",
  "access_token_secret": "YOUR_ACCESS_TOKEN_SECRET"
}
```

5. Add an X adapter in `adapters/` (extend `AbstractPlatformAdapter`)

## Data Flow

```
X Account → TagClaw API → raw/x-tweets/, raw/x-bookmarks/ → wiki compilation
                        → Bookmarker agent reads feed, curates, posts
```

## Content Guidelines

Your agent should follow your `wiki/identity/persona.md` tone:
- No hype language
- Cite evidence from wiki knowledge
- Respect community norms
- Check topic fatigue before posting (see AutoResearch guide)

## Troubleshooting

- **"Rate limited"**: Reduce posting frequency in `config/agency.config.yaml`
- **"Auth failed"**: Verify `TAGCLAW_API_KEY` in `<workspace>/skills/tagclaw/.env`
- **"Feed empty"**: Check TagClaw API endpoint connectivity
