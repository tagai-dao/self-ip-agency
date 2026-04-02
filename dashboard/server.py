#!/usr/bin/env python3
"""
Self-IP Agency Dashboard Server
FastAPI server that reads runtime/ data and exposes /api/data endpoint.

Usage:
    python3 server.py --runtime-root /path/to/workspace/runtime --port 8765
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI(
    title="Self-IP Agency Dashboard",
    description="Real-time monitoring for the 3-agent IP operations system",
    version="1.0.0",
)

# Runtime root is configured at startup
RUNTIME_ROOT: Path = Path("runtime")


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, return empty dict on any error."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def compute_tas(social: float, trade: float) -> float:
    """Compute composite TAS score."""
    return round(social * 0.7 + trade * 0.3, 2)


def determine_mode(tas_total: float, vp: float) -> str:
    """Determine operating mode from TAS score."""
    if tas_total >= 1200 and vp >= 150:
        return "super"
    if tas_total >= 800 and vp >= 100:
        return "active"
    return "idle"


@app.get("/api/data")
async def get_dashboard_data() -> JSONResponse:
    """
    Main data endpoint. Reads all runtime JSON files and returns
    a combined snapshot for the dashboard frontend.
    """
    root = RUNTIME_ROOT

    # Read all runtime data
    main_status = read_json(root / "main" / "status.json")
    main_heartbeat = read_json(root / "main" / "heartbeat.json")
    bookmarker_result = read_json(root / "bookmarker" / "result.json")
    bookmarker_tas = read_json(root / "bookmarker" / "tas_social.json")
    trader_result = read_json(root / "trader" / "result.json")
    trader_tas = read_json(root / "trader" / "tas_trade.json")
    tas_snapshot = read_json(root / "shared" / "tas_snapshot.json")
    vp_budget = read_json(root / "shared" / "vp_budget.json")

    # Compute composite TAS
    tas_social = float(bookmarker_tas.get("tas_social", 0))
    tas_trade = float(trader_tas.get("tas_trade", 0))
    tas_total = tas_snapshot.get("tas_total") or compute_tas(tas_social, tas_trade)
    vp_remaining = float(vp_budget.get("remaining", vp_budget.get("daily_budget", 1000)))

    return JSONResponse({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tas": {
            "total": tas_total,
            "social": tas_social,
            "trade": tas_trade,
            "social_weight": 0.7,
            "trade_weight": 0.3,
            "mode": determine_mode(tas_total, vp_remaining),
        },
        "agents": {
            "main": {
                "status": main_status.get("status", "unknown"),
                "mode": main_status.get("current_mode", "idle"),
                "last_heartbeat": main_heartbeat.get("timestamp"),
                "uptime_hours": main_status.get("uptime_hours", 0),
            },
            "bookmarker": {
                "status": "active" if bookmarker_result.get("cycle_id") else "idle",
                "last_cycle": bookmarker_result.get("cycle_id"),
                "posts_curated": bookmarker_result.get("posts_curated", 0),
                "vp_spent": bookmarker_result.get("vp_spent", 0),
                "posts_created": bookmarker_result.get("posts_created", 0),
                "replies_sent": bookmarker_result.get("replies_sent", 0),
                "tas_social": tas_social,
            },
            "trader": {
                "status": "active" if trader_result.get("cycle_id") else "idle",
                "last_cycle": trader_result.get("cycle_id"),
                "trades_executed": trader_result.get("trades_executed", 0),
                "total_volume_usd": trader_result.get("total_volume_usd", 0.0),
                "net_pnl_usd": trader_result.get("net_pnl_usd", 0.0),
                "win_rate": trader_result.get("win_rate", 0.0),
                "tas_trade": tas_trade,
            },
        },
        "vp": {
            "daily_budget": vp_budget.get("daily_budget", 1000),
            "used_today": vp_budget.get("used_today", 0),
            "remaining": vp_remaining,
            "reserve_floor": vp_budget.get("reserve_floor", 50),
        },
        "alerts": main_heartbeat.get("alerts", []),
    })


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "runtime_root": str(RUNTIME_ROOT)}


# Mount static files AFTER API routes
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(static_dir / "index.html"))


def main() -> None:
    global RUNTIME_ROOT

    parser = argparse.ArgumentParser(description="Self-IP Agency Dashboard")
    parser.add_argument(
        "--runtime-root",
        default=str(Path.home() / ".openclaw" / "workspace" / "runtime"),
        help="Path to the runtime/ directory",
    )
    parser.add_argument("--port", type=int, default=8765, help="Server port")
    parser.add_argument("--host", default="0.0.0.0", help="Server host")
    args = parser.parse_args()

    RUNTIME_ROOT = Path(args.runtime_root)
    if not RUNTIME_ROOT.exists():
        print(f"Warning: runtime root does not exist: {RUNTIME_ROOT}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
