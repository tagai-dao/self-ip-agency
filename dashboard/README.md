# Self-IP Agency Dashboard

Real-time monitoring dashboard for the 3-agent IP operations team.

## Quick start

```bash
pip3 install -r requirements.txt
python3 server.py --runtime-root ~/.openclaw/workspace/runtime --port 8765
```

Then open http://localhost:8765

## API

### `GET /api/data`

Returns a combined snapshot of all agent states:

```json
{
  "generated_at": "2026-04-02T10:00:00Z",
  "tas": {
    "total": 856.0,
    "social": 980.0,
    "trade": 560.0,
    "mode": "active"
  },
  "agents": {
    "main": { "status": "active", "mode": "active" },
    "bookmarker": { "posts_curated": 12, "vp_spent": 45, "tas_social": 980.0 },
    "trader": { "trades_executed": 2, "net_pnl_usd": 12.5, "tas_trade": 560.0 }
  },
  "vp": { "daily_budget": 1000, "used_today": 45, "remaining": 955 }
}
```

### `GET /api/health`

Health check.

## Arguments

| Arg | Default | Description |
|-----|---------|-------------|
| `--runtime-root` | `~/.openclaw/workspace/runtime` | Path to runtime/ directory |
| `--port` | `8765` | HTTP port |
| `--host` | `0.0.0.0` | Bind address |
