#!/usr/bin/env python3
"""run_trader_runtime_v1.py — Native trader cycle runtime.

Replaces the dev-claude.sh / claude CLI dependency with a self-contained
Python runtime that handles the trader on-chain operations cycle:

  1. Read wallet balance and position state
  2. Evaluate trading signals (trending, price data)
  3. Execute trades if warranted (conservative: min_signal_confidence gate)
  4. Write result.json and latest.json

No LLM dependency. Uses TagClaw API directly.

Usage (called by trader-cycle.sh):
    cd $WORKSPACE && python3 scripts/run_trader_runtime_v1.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", Path.home() / ".openclaw" / "workspace"))
RUNTIME_TRADER = WORKSPACE / "runtime" / "trader"
RUNTIME_SHARED = WORKSPACE / "runtime" / "shared"
CONFIG_DIR = WORKSPACE / "config"
BEHAVIOR_FILE = WORKSPACE / "agents" / "trader.md"
CREDENTIALS_PATH = Path.home() / ".config" / "tagclaw" / "credentials.json"


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent),
                                     suffix=".tmp", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
        tmp = f.name
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# TagClaw API
# ---------------------------------------------------------------------------

def resolve_api_key() -> str:
    """Resolve TagClaw API key from skill env or legacy credentials."""
    skill_env = WORKSPACE / "skills" / "tagclaw" / ".env"
    if skill_env.exists():
        for line in skill_env.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip("\"'")
            if k == "TAGCLAW_API_KEY" and v:
                return v

    if CREDENTIALS_PATH.exists():
        try:
            creds = json.loads(CREDENTIALS_PATH.read_text())
            return creds.get("apiKey") or creds.get("api_key") or creds.get("API_KEY") or ""
        except Exception:
            pass
    return ""


def tagclaw_get(endpoint: str, api_key: str) -> dict | list | None:
    """HTTP GET against TagClaw API."""
    import urllib.request

    base_url = "https://bsc-api.tagai.fun/tagclaw"
    url = f"{base_url}{endpoint}"
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Trading logic
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load agency config for trading settings."""
    config_path = CONFIG_DIR / "agency.config.yaml"
    if not config_path.exists():
        return {}
    try:
        text = config_path.read_text()
        config: dict[str, Any] = {}
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#") or ":" not in s:
                continue
            k, v = s.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v:
                try:
                    config[k] = float(v) if "." in v else int(v)
                except ValueError:
                    config[k] = v
        return config
    except Exception:
        return {}


def evaluate_signals(api_key: str) -> dict:
    """Evaluate trading signals. Returns signal assessment."""
    signals: list[dict] = []
    errors: list[str] = []

    # 1. Get trending data
    trending = tagclaw_get("/trending", api_key)
    if trending is None:
        errors.append("Failed to fetch trending data")
    else:
        items = trending if isinstance(trending, list) else \
                trending.get("items") or trending.get("data") or []
        for item in items[:10]:
            if not isinstance(item, dict):
                continue
            signals.append({
                "source": "trending",
                "ticker": item.get("ticker") or item.get("symbol") or item.get("name", "unknown"),
                "confidence": 0.3,  # trending alone is weak signal
                "direction": "neutral",
            })

    # 2. Get wallet balance for position awareness
    balance = tagclaw_get("/wallet/balance", api_key)
    wallet_status = "unknown"
    if balance is not None:
        wallet_status = "ok"
    else:
        errors.append("Failed to fetch wallet balance")
        wallet_status = "unavailable"

    return {
        "signals": signals,
        "wallet_status": wallet_status,
        "balance": balance,
        "errors": errors,
    }


def make_trade_decisions(signals: list[dict], config: dict) -> list[dict]:
    """Apply trading rules to signals. Returns list of trade decisions.

    Conservative by default: only trades when confidence exceeds threshold.
    """
    min_confidence = config.get("min_signal_confidence", 0.7)
    decisions: list[dict] = []

    for signal in signals:
        conf = signal.get("confidence", 0)
        if conf >= min_confidence:
            decisions.append({
                "ticker": signal.get("ticker", "unknown"),
                "action": "evaluate",  # not auto-executing, just flagging
                "confidence": conf,
                "direction": signal.get("direction", "neutral"),
                "reason": f"Signal confidence {conf:.2f} >= threshold {min_confidence}",
            })

    return decisions


def run_trader_cycle() -> dict:
    """Execute one trader cycle. Returns result dict."""
    ts_start = now_iso()
    api_key = resolve_api_key()
    errors: list[str] = []
    trades_executed: list[dict] = []

    # 1. Check credentials
    has_credentials = bool(api_key)
    if not has_credentials:
        errors.append("No API key found — cannot access TagClaw trading endpoints")

    # 2. Evaluate signals
    signal_assessment = evaluate_signals(api_key) if has_credentials else {
        "signals": [], "wallet_status": "no-credentials", "balance": None, "errors": ["No credentials"]
    }
    errors.extend(signal_assessment.get("errors", []))

    # 3. Make trade decisions
    config = load_config()
    decisions = make_trade_decisions(signal_assessment.get("signals", []), config)

    # 4. Execute trades (conservative: native runtime only flags, doesn't auto-trade)
    # Auto-trading requires explicit opt-in via config. Default: observe-only.
    auto_trade = config.get("auto_trade_enabled", False)
    for decision in decisions:
        if auto_trade and decision.get("confidence", 0) >= config.get("min_signal_confidence", 0.7):
            # Would execute trade here via tagclaw-wallet
            trades_executed.append({
                **decision,
                "status": "skipped",
                "reason": "Auto-trade not yet implemented in native runtime v1",
            })
        else:
            trades_executed.append({
                **decision,
                "status": "observed",
                "reason": "Observe-only mode (auto_trade_enabled=false)",
            })

    # 5. Build result
    status = "ok" if not errors else ("partial" if has_credentials else "blocked")

    return {
        "schema": "trader.result.v1",
        "status": status,
        "started_at": ts_start,
        "completed_at": now_iso(),
        "has_credentials": has_credentials,
        "wallet_status": signal_assessment.get("wallet_status", "unknown"),
        "signals_evaluated": len(signal_assessment.get("signals", [])),
        "decisions": decisions,
        "trades_executed": trades_executed,
        "trades_ok": sum(1 for t in trades_executed if t["status"] == "ok"),
        "errors": errors,
        "execution_backend": "native-python",
        # Internal: pass raw data for canonical output publishing
        "_balance_data": signal_assessment.get("balance"),
        "_signals": signal_assessment.get("signals", []),
    }


# ---------------------------------------------------------------------------
# Canonical runtime output publishers
# ---------------------------------------------------------------------------

def publish_trader_canonical(result: dict, ts_now: str, bundle_ts: str) -> None:
    """Publish canonical runtime JSON files that dashboard and input-packet read.

    Files: tas-trade, reward-status, wallet-snapshot, risk-status.
    Written after every cycle so dashboard never shows stale bootstrap/null data.
    """
    status = result.get("status", "blocked")
    has_credentials = result.get("has_credentials", False)
    wallet_status = result.get("wallet_status", "unknown")
    balance_data = result.get("_balance_data")
    signals = result.get("_signals", [])
    decisions = result.get("decisions", [])

    # ── wallet-snapshot.json ─────────────────────────────────────────────
    wallet_snapshot: dict[str, Any] = {
        "schema": "trader.wallet-snapshot.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "bundle_ts": bundle_ts,
        "status": "ok" if wallet_status == "ok" else ("blocked" if not has_credentials else "degraded"),
    }
    if isinstance(balance_data, dict):
        wallet_snapshot["wallet_address"] = balance_data.get("address") or balance_data.get("wallet") or None
        wallet_snapshot["balances"] = {
            k: v for k, v in balance_data.items()
            if k not in ("address", "wallet", "status")
        }
    else:
        wallet_snapshot["wallet_address"] = None
        wallet_snapshot["balances"] = {}
    atomic_write_json(RUNTIME_TRADER / "wallet-snapshot.json", wallet_snapshot)

    # ── reward-status.json ───────────────────────────────────────────────
    reward_status: dict[str, Any] = {
        "schema": "trader.reward-status.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "checked_at": ts_now,
        "bundle_ts": bundle_ts,
        "status": status,
        "claimable": [],
        "claimable_usd_total": 0,
    }
    # If we have balance data, try to extract reward info
    if isinstance(balance_data, dict):
        rewards = balance_data.get("rewards") or balance_data.get("claimable")
        if isinstance(rewards, list):
            reward_status["claimable"] = rewards
            total = 0.0
            for r in rewards:
                if isinstance(r, dict):
                    try:
                        total += float(r.get("reward_value_usd") or r.get("usd_value") or 0)
                    except (ValueError, TypeError):
                        pass
            reward_status["claimable_usd_total"] = round(total, 4)
    atomic_write_json(RUNTIME_TRADER / "reward-status.json", reward_status)

    # ── tas-trade.json ───────────────────────────────────────────────────
    # Native runtime provides observe-only signals; value is null until
    # a proper measurement pipeline is in place.
    tas_trade: dict[str, Any] = {
        "schema": "trader.tas-trade.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "bundle_ts": bundle_ts,
        "status": "ok" if has_credentials and wallet_status == "ok" else ("partial" if has_credentials else "blocked"),
        "value": None,  # native runtime does not compute TAS — deferred to measurement pipeline
        "portfolio_usd_raw": None,
        "risk_flags": [],
        "autonomy_mode": "observe-only",
        "signals_evaluated": len(signals),
        "decisions_count": len(decisions),
        "measurement_quality": {
            "overall_status": "pending",
            "price_visibility": "unknown",
        },
    }
    atomic_write_json(RUNTIME_TRADER / "tas-trade.json", tas_trade)

    # ── risk-status.json ─────────────────────────────────────────────────
    risk_flags: list[str] = []
    if not has_credentials:
        risk_flags.append("no_credentials")
    if wallet_status != "ok":
        risk_flags.append("wallet_unavailable")

    risk_status: dict[str, Any] = {
        "schema": "trader.risk-status.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "bundle_ts": bundle_ts,
        "status": "ok" if not risk_flags else "flagged",
        "risk_flags": risk_flags,
        "flags": risk_flags,
    }
    atomic_write_json(RUNTIME_TRADER / "risk-status.json", risk_status)

    print(f"[trader-runtime] Published canonical outputs: wallet-snapshot, reward-status, tas-trade, risk-status")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"[trader-runtime] Starting native trade cycle at {now_iso()}")

    try:
        result = run_trader_cycle()
    except Exception as e:
        result = {
            "schema": "trader.result.v1",
            "status": "blocked",
            "started_at": now_iso(),
            "completed_at": now_iso(),
            "errors": [f"Runtime exception: {e}"],
            "execution_backend": "native-python",
            "traceback": traceback.format_exc(),
        }

    # Write result.json
    atomic_write_json(RUNTIME_TRADER / "result.json", result)
    print(f"[trader-runtime] Wrote result.json (status={result['status']})")

    # Write latest.json
    ts_now = now_iso()
    bundle_ts = ts_now  # coherent bundle timestamp for all trader artifacts
    latest = {
        "schema": "trader.latest.v1",
        "generated_at": ts_now,
        "bundle_ts": bundle_ts,
        "status": result["status"],
        "source": "run_trader_runtime_v1.py",
        "signals_evaluated": result.get("signals_evaluated", 0),
        "trades_ok": result.get("trades_ok", 0),
        "wallet_status": result.get("wallet_status", "unknown"),
    }
    atomic_write_json(RUNTIME_TRADER / "latest.json", latest)
    print(f"[trader-runtime] Wrote latest.json")

    # ── Publish canonical runtime outputs for dashboard/input-packet ──────
    publish_trader_canonical(result, ts_now, bundle_ts)

    # Update shared runtime-status
    rs_path = RUNTIME_SHARED / "runtime-status.json"
    try:
        rs = json.loads(rs_path.read_text()) if rs_path.exists() else {}
    except Exception:
        rs = {}
    rs.setdefault("schema", "runtime-status.v1")
    rs["trader"] = {"status": result["status"], "updated_at": ts_now}
    rs.pop("bootstrap", None)
    atomic_write_json(rs_path, rs)

    status_code = 0 if result["status"] in ("ok", "partial") else 1
    print(f"[trader-runtime] Cycle complete (exit={status_code})")
    return status_code


if __name__ == "__main__":
    sys.exit(main())
