# Self-IP Agency Dashboard

Real-time monitoring UI for the 3-agent Self-IP stack — TAS scores, wiki health,
strategy insights, live OP/VP, community heat, and agent status at a glance.

## Quick start

```bash
pip3 install -r dashboard/requirements.txt

# Point at your OpenClaw workspace
python3 dashboard/server.py --workspace ~/.openclaw/workspace
```

Then open: **http://localhost:7890**

## What it shows

| Panel | Data |
|-------|------|
| **TAS Total** | Composite score: 0.7×TAS_social + 0.3×TAS_trade |
| **OP / VP** | Live on-chain operator power and vote power from TagClaw API |
| **Agent Status** | Main / Bookmarker / Trader — last run, status, mode |
| **Wiki Health** | 3-band lint score, contract verification, execution brief freshness |
| **AutoResearch** | Current experiment mode (BASELINE / EXPLORE / EXPLOIT), recent strategy cycles |
| **Community Heat** | Trending ticks and community heat scores |
| **Strategy Ledger** | Last 10 strategy decisions with delta tracking |
| **Social Feed** | Latest curate candidate preview from bookmarker runtime |
| **Dependency Graph** | Runtime artifact freshness graph with countdown timers |

## Arguments

| Arg | Default | Description |
|-----|---------|-------------|
| `--workspace` | `~/.openclaw/workspace` | Path to OpenClaw workspace root |
| `--port` | `7890` | HTTP port (`$VIZ_PORT` env var also accepted) |

You can also set `OPENCLAW_WORKSPACE` env var instead of `--workspace`.

## Runtime files it reads

The dashboard reads these files from the workspace. Missing files degrade gracefully
with null values — the server always starts even with an empty runtime directory.

```
$WORKSPACE/runtime/
  main/
    latest.json            ← main agent status, mode, last decision
    runtime-state.json     ← OP/VP snapshot
    input-packet.json      ← aggregated input packet summary
    social-intent.json     ← current social action plan
    treasury-policy.json   ← treasury risk policy
  bookmarker/
    latest.json            ← bookmarker agent status
    tas-social.json        ← TAS_social score
    topic-heatmap.json     ← topic heat scores
    source-health.json     ← feed source health
  trader/
    latest.json            ← trader agent status
    tas-trade.json         ← TAS_trade score
    wallet-snapshot.json   ← wallet balances
    reward-status.json     ← claimable rewards
  shared/
    wiki-lint-status.json        ← wiki health score
    wiki-contract-verify.json    ← contract verification
    wiki-execution-brief.json    ← weekly themes
    community-heat.json          ← community heat
    strategy-ledger.jsonl        ← AutoResearch strategy log
    tas-history.jsonl            ← TAS history
$WORKSPACE/memory/
  main-strategy-log.jsonl  ← strategy cycle log
  x-latest-tweets.md       ← recent social posts
```

## API endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | Aggregated agent health, TAS, wiki status |
| `GET /api/wiki` | Wiki lint, contract verification |
| `GET /api/strategy` | Strategy stats, experiment state, recent cycles |
| `GET /api/monitor/full` | Full dependency graph, countdowns, community heat |
| `GET /api/monitor/tas-history` | TAS history chart data |
| `GET /api/feed/curate-preview` | Current feed fallback curate candidates |
| `GET /api/health` | Server health check |
| `GET /` | Dashboard UI |

## Run as a LaunchAgent (macOS background service)

Create `~/Library/LaunchAgents/com.self-ip.dashboard.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.self-ip.dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/python3</string>
    <string>/Users/YOUR_USER/self-ip-agency/dashboard/server.py</string>
    <string>--workspace</string>
    <string>/Users/YOUR_USER/.openclaw/workspace</string>
    <string>--port</string>
    <string>7890</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/self-ip-dashboard.log</string>
  <key>StandardErrorPath</key><string>/tmp/self-ip-dashboard.err</string>
</dict>
</plist>
```

Then: `launchctl load ~/Library/LaunchAgents/com.self-ip.dashboard.plist`
