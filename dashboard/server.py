#!/usr/bin/env python3
"""Self-IP Agency Dashboard Server — Rich monitoring for 3-agent stack.

Endpoints:
  /api/status   — Aggregated agent health, TAS, guidance, wiki status
  /api/wiki     — Wiki system health (lint, contract verification)
  /api/strategy — Strategy stats, experiment state, recent decisions
  /api/health   — Health check
  /              — Serve dashboard UI

Usage:
    python3 server.py
    python3 server.py --workspace /path/to/workspace --port 8765
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
import uvicorn

# ── Paths ──
WORKSPACE = Path(__file__).parent.parent  # self-ip-agency root
RUNTIME = WORKSPACE / "runtime"
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Self-IP Agency Dashboard", version="2.0.0")

STALE_THRESHOLD_HOURS = 4


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


app.add_middleware(NoCacheMiddleware)

# ── Live OP/VP cache ──
_TAGAI_BASE_URL = "https://bsc-api.tagai.fun"
_tagai_cache: dict[str, Any] = {"op": None, "vp": None, "ts": 0.0}
_tagai_lock = threading.Lock()
_TAGAI_TTL = 120


def _fetch_live_op_vp() -> dict[str, Any]:
    now = time.monotonic()
    with _tagai_lock:
        if now - _tagai_cache["ts"] < _TAGAI_TTL:
            return {"op": _tagai_cache["op"], "vp": _tagai_cache["vp"]}
    try:
        creds_path = os.path.expanduser("~/.config/tagclaw/credentials.json")
        creds = json.loads(Path(creds_path).read_text())
        api_key = creds.get("api_key") or creds.get("apiKey") or creds.get("token")
        if not api_key:
            return {"op": None, "vp": None}
        import urllib.request
        req = urllib.request.Request(
            f"{_TAGAI_BASE_URL}/tagclaw/me",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        agent = body.get("agent") or body.get("data", {}).get("agent", {})
        op_val = agent.get("op")
        vp_val = agent.get("vp")
        with _tagai_lock:
            _tagai_cache.update({"op": op_val, "vp": vp_val, "ts": time.monotonic()})
        return {"op": op_val, "vp": vp_val}
    except Exception:
        return {"op": _tagai_cache.get("op"), "vp": _tagai_cache.get("vp")}


# ── Helpers ──

def _load(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe(path: str) -> dict | list | None:
    return _load(RUNTIME / path)


def _mtime_iso(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _age_hours(date_str: str | None) -> float | None:
    if not date_str:
        return None
    try:
        s = str(date_str).strip()
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600, 1)
    except Exception:
        return None


def _age_status(date_str: str | None, max_hours: float = 4.0) -> str:
    h = _age_hours(date_str)
    if h is None:
        return "missing"
    return "ok" if h < max_hours else "stale"


def _dir_info(directory: Path) -> dict:
    try:
        if not directory.is_dir():
            return {"file_count": 0, "newest_file_age_hours": None}
        files = [f for f in directory.rglob("*") if f.is_file()]
        if not files:
            return {"file_count": 0, "newest_file_age_hours": None}
        newest_mtime = max(f.stat().st_mtime for f in files)
        age_hours = (datetime.now(timezone.utc).timestamp() - newest_mtime) / 3600
        return {"file_count": len(files), "newest_file_age_hours": round(age_hours, 1)}
    except Exception:
        return {"file_count": 0, "newest_file_age_hours": None}


# ── API Endpoints ──

@app.get("/api/status")
async def get_status() -> JSONResponse:
    """Aggregated agent status, TAS, wiki, strategy snapshot."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Main agent
    main_latest = _safe("main/latest.json") or {}
    main_runtime = _safe("main/runtime-state.json") or {}
    main_health = _safe("main/runtime-health.json") or {}
    tas_latest = _safe("main/tas-latest.json") or {}
    strategy_plan = _safe("main/strategy-plan.json") or {}
    bk_guidance = _safe("main/bookmarker-guidance.json") or {}
    tr_guidance = _safe("main/trader-guidance.json") or {}

    # Bookmarker
    bk_latest = _safe("bookmarker/latest.json") or {}
    bk_brief = _safe("bookmarker/topic-brief.json") or {}
    bk_source = _safe("bookmarker/source-health.json") or {}
    bk_tas = _safe("bookmarker/tas-social.json") or {}
    bk_heatmap = _safe("bookmarker/topic-heatmap.json") or {}

    # Trader
    tr_latest = _safe("trader/latest.json") or {}
    tr_exec = _safe("trader/execution-record.json") or {}
    tr_tas = _safe("trader/tas-trade.json") or {}
    tr_portfolio = _safe("trader/portfolio-delta.json") or {}
    tr_wallet = _safe("trader/wallet-snapshot.json") or {}

    # Shared
    wiki_lint = _safe("shared/wiki-lint-status.json") or {}
    wiki_contract = _safe("shared/wiki-contract-verify.json") or {}
    community_heat = _safe("shared/community-heat.json") or {}
    strategy_exp = _safe("shared/strategy-experiment.json") or {}
    dispatch_config = _safe("shared/dispatch-config.json") or {}

    # Live OP/VP
    live_op_vp = _fetch_live_op_vp()

    # TAS scores
    tas_social = tas_latest.get("tas_social") or bk_tas.get("value") or 0
    tas_trade = tas_latest.get("tas_trade") or tr_tas.get("value") or 0
    tas_total = tas_latest.get("tas_total") or round(float(tas_social) * 0.7 + float(tas_trade) * 0.3, 2)

    return JSONResponse({
        "generated_at": now,
        "tas": {
            "total": tas_total,
            "social": tas_social,
            "trade": tas_trade,
            "mode": tas_latest.get("status") or strategy_plan.get("mode") or "idle",
        },
        "live_op_vp": live_op_vp,
        "agents": {
            "main": {
                "status": main_health.get("main_status") or main_latest.get("status") or "unknown",
                "last_heartbeat": main_latest.get("generated_at"),
                "age_status": _age_status(main_latest.get("generated_at")),
                "uptime_hours": main_latest.get("uptime_hours"),
            },
            "bookmarker": {
                "status": main_health.get("bookmarker_status") or bk_latest.get("status") or "unknown",
                "last_execution": bk_latest.get("generated_at"),
                "age_status": _age_status(bk_latest.get("generated_at")),
                "tas_social": tas_social,
                "source_health": bk_source,
                "topic_brief": bk_brief.get("top_topics", [])[:5] if isinstance(bk_brief, dict) else [],
            },
            "trader": {
                "status": main_health.get("trader_status") or tr_latest.get("status") or "unknown",
                "last_execution": tr_latest.get("generated_at") or tr_exec.get("executed_at"),
                "age_status": _age_status(tr_latest.get("generated_at") or tr_exec.get("executed_at")),
                "tas_trade": tas_trade,
                "portfolio_delta": tr_portfolio,
                "wallet": {
                    "total_usd": tr_wallet.get("total_usd"),
                    "positions": len(tr_wallet.get("positions", [])),
                },
            },
        },
        "wiki": {
            "health_score": wiki_lint.get("health_score"),
            "needs_attention": wiki_lint.get("needs_attention", False),
            "lint_age_status": _age_status(wiki_lint.get("generated_at"), 168),
            "contract_status": wiki_contract.get("status"),
            "contract_pass": wiki_contract.get("pass", 0),
            "contract_fail": wiki_contract.get("fail", 0),
        },
        "strategy": {
            "plan": strategy_plan.get("mode") or strategy_plan.get("strategy_id"),
            "bk_mode": bk_guidance.get("experiment_mode"),
            "tr_mode": tr_guidance.get("experiment_mode"),
            "bk_win_rate": (bk_guidance.get("cycle_stats") or {}).get("win_rate"),
            "tr_win_rate": (tr_guidance.get("cycle_stats") or {}).get("win_rate"),
            "experiment_cycle": strategy_exp.get("cycle_count", 0),
        },
        "community_heat": {
            "top_ticks": list((community_heat.get("ticks") or {}).keys())[:5],
            "source_status": (community_heat.get("source_health") or {}).get("status"),
        },
    })


@app.get("/api/wiki")
async def get_wiki_status() -> JSONResponse:
    """Detailed wiki system health."""
    wiki_dir = WORKSPACE / "wiki"
    raw_dir = WORKSPACE / "raw"

    wiki_subdirs = {}
    if wiki_dir.is_dir():
        for child in sorted(wiki_dir.iterdir()):
            if child.is_dir():
                wiki_subdirs[child.name] = _dir_info(child)

    raw_subdirs = {}
    if raw_dir.is_dir():
        for child in sorted(raw_dir.iterdir()):
            if child.is_dir():
                raw_subdirs[child.name] = _dir_info(child)

    wiki_lint = _safe("shared/wiki-lint-status.json") or {}
    wiki_contract = _safe("shared/wiki-contract-verify.json") or {}
    exec_brief = _safe("shared/wiki-execution-brief.json") or {}

    return JSONResponse({
        "wiki_directories": wiki_subdirs,
        "raw_directories": raw_subdirs,
        "lint": wiki_lint,
        "contract": wiki_contract,
        "execution_brief": {
            "compiled_at": exec_brief.get("compiled_at"),
            "top_themes": (exec_brief.get("top_themes") or [])[:5],
        },
    })


@app.get("/api/strategy")
async def get_strategy() -> JSONResponse:
    """Strategy and autoresearch status."""
    strategy_exp = _safe("shared/strategy-experiment.json") or {}
    bk_guidance = _safe("main/bookmarker-guidance.json") or {}
    tr_guidance = _safe("main/trader-guidance.json") or {}

    # Read last 10 strategy log entries
    strategy_log_path = WORKSPACE / "memory" / "main-strategy-log.jsonl"
    recent_cycles = []
    if strategy_log_path.exists():
        try:
            lines = strategy_log_path.read_text(encoding="utf-8").strip().split("\n")
            for line in lines[-10:]:
                try:
                    entry = json.loads(line)
                    recent_cycles.append({
                        "cycle_id": entry.get("cycle_id"),
                        "outcome": entry.get("outcome"),
                        "kept": entry.get("kept"),
                        "delta": entry.get("delta"),
                        "experiment_mode": entry.get("experiment_mode"),
                    })
                except Exception:
                    pass
        except Exception:
            pass

    return JSONResponse({
        "experiment": {
            "cycle_count": strategy_exp.get("cycle_count", 0),
            "track_a_best": (strategy_exp.get("track_a") or {}).get("best_arm"),
            "track_b_best": (strategy_exp.get("track_b") or {}).get("best_arm"),
        },
        "bookmarker_guidance": {
            "mode": bk_guidance.get("experiment_mode"),
            "win_rate": (bk_guidance.get("cycle_stats") or {}).get("win_rate"),
            "guidance": bk_guidance.get("guidance"),
        },
        "trader_guidance": {
            "mode": tr_guidance.get("experiment_mode"),
            "win_rate": (tr_guidance.get("cycle_stats") or {}).get("win_rate"),
            "guidance": tr_guidance.get("guidance"),
        },
        "recent_cycles": recent_cycles,
    })


@app.get("/api/live-op-vp")
async def get_live_op_vp() -> JSONResponse:
    return JSONResponse(_fetch_live_op_vp())


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "workspace": str(WORKSPACE), "version": "2.0.0"}


# Mount static files
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(STATIC / "index.html"))


def main() -> None:
    global WORKSPACE, RUNTIME, STATIC

    parser = argparse.ArgumentParser(description="Self-IP Agency Dashboard")
    parser.add_argument("--workspace", default=str(WORKSPACE),
                        help="Path to workspace root")
    parser.add_argument("--port", type=int, default=8765, help="Server port")
    parser.add_argument("--host", default="0.0.0.0", help="Server host")
    args = parser.parse_args()

    WORKSPACE = Path(args.workspace)
    RUNTIME = WORKSPACE / "runtime"

    if not RUNTIME.exists():
        print(f"Warning: runtime directory not found at {RUNTIME}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
