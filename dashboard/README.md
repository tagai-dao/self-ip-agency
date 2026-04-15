# Self-IP Agency Dashboard v2

Real-time monitoring dashboard for the 3-agent IP operations stack with wiki health, strategy insights, and live OP/VP metrics.

## Quick start

```bash
pip3 install -r requirements.txt
python3 server.py --workspace /path/to/workspace --port 8765
```

Then open http://localhost:8765

## API Endpoints

### `GET /api/status`

Aggregated snapshot: TAS scores, agent health, wiki status, strategy summary, community heat, live OP/VP.

```json
{
  "generated_at": "2026-04-15T10:00:00Z",
  "tas": { "total": 856.0, "social": 980.0, "trade": 560.0, "mode": "active" },
  "live_op_vp": { "op": 1200, "vp": 500 },
  "agents": {
    "main": { "status": "active", "last_heartbeat": "...", "age_status": "ok" },
    "bookmarker": { "status": "active", "tas_social": 980.0, "topic_brief": [...] },
    "trader": { "status": "active", "tas_trade": 560.0, "wallet": { "total_usd": 42.5 } }
  },
  "wiki": { "health_score": 7.2, "contract_status": "ok", "needs_attention": false },
  "strategy": { "bk_mode": "EXPLOIT", "tr_mode": "EXPLORE", "experiment_cycle": 12 },
  "community_heat": { "top_ticks": ["BTC", "ETH", "SOL"] }
}
```

### `GET /api/wiki`

Detailed wiki system health: directory listing, lint results, contract verification, execution brief.

### `GET /api/strategy`

Strategy and autoresearch status: experiment state (dual-track A/B), bookmarker/trader guidance, recent strategy cycles.

### `GET /api/live-op-vp`

Live TagAI OP/VP scores (cached 120s).

### `GET /api/health`

Health check with workspace path and version.

## Arguments

| Arg | Default | Description |
|-----|---------|-------------|
| `--workspace` | repo root | Path to workspace root (parent of `runtime/`) |
| `--port` | `8765` | HTTP port |
| `--host` | `0.0.0.0` | Bind address |

## Dashboard sections

- **Header**: TAS total (big number), agent status pills (green/yellow/red), live OP/VP
- **Wiki System**: health score, contract verification pass/fail, attention needed indicator
- **AutoResearch**: bookmarker/trader experiment modes, win rates, recent strategy cycles table
- **Agent Grid**: Main (heartbeat age, freshness), Bookmarker (TAS, topics), Trader (TAS, wallet)
- **Community Heat**: top trending ticks

## Runtime data

The dashboard reads JSON files from `{workspace}/runtime/`:

| Path | Content |
|------|---------|
| `main/latest.json` | Main agent last heartbeat |
| `main/tas-latest.json` | Latest TAS snapshot |
| `bookmarker/latest.json` | Bookmarker last execution |
| `trader/latest.json` | Trader last execution |
| `shared/wiki-lint-status.json` | Wiki lint results |
| `shared/wiki-contract-verify.json` | Contract verification |
| `shared/strategy-experiment.json` | Dual-track experiment state |
| `shared/community-heat.json` | Community tick heatmap |
