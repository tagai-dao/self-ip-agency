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
RAW_TRADER = WORKSPACE / "raw" / "trader"
CONFIG_DIR = WORKSPACE / "config"
BEHAVIOR_FILE = WORKSPACE / "agents" / "trader.md"

# TAS_trade normalization baselines (mirror dashboard display expectations)
PORTFOLIO_USD_BASELINE = 50.0
CLAIMABLE_USD_BASELINE = 5.0
PORTFOLIO_WEIGHT = 0.9
CLAIMABLE_WEIGHT = 0.1


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
    """Resolve TagClaw API key from skills/tagclaw/.env."""
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


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_agent_state(api_key: str) -> tuple[dict | None, str | None]:
    """Fetch /me — canonical agent state (op, vp, credits, portfolio).

    Returns (agent_dict, error). Uses /me as the primary source-of-truth per
    deployment experience — older endpoints like /wallet/balance and
    /trending are unreliable in deployed environments.
    """
    resp = tagclaw_get("/me", api_key)
    if resp is None:
        return None, "could not fetch /me"
    if not isinstance(resp, dict):
        return None, "unexpected /me shape"
    # Envelope normalization — same precedence as adapters/tagclaw.extract_me_agent.
    # Covers {agent:{}}, {data:{agent:{}}}, {data:{...flat}}, and legacy flat.
    if isinstance(resp.get("agent"), dict):
        return resp["agent"], None
    data = resp.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("agent"), dict):
            return data["agent"], None
        return data, None
    return resp, None


def fetch_agent_rewards(api_key: str) -> tuple[list[dict], str | None]:
    """Best-effort fetch of claimable rewards.

    Tries a few likely shapes (/agent/rewards, /rewards, /me for embedded
    rewards). Absence is non-fatal — the trader runtime still computes a
    conservative tas_trade from /me alone.
    """
    for endpoint in ("/agent/rewards", "/rewards"):
        resp = tagclaw_get(endpoint, api_key)
        if resp is None:
            continue
        if isinstance(resp, list):
            return resp, None
        if isinstance(resp, dict):
            for key in ("rewards", "claimable", "items", "data"):
                val = resp.get(key)
                if isinstance(val, list):
                    return val, None
    return [], "no dedicated rewards endpoint responded"


def evaluate_signals(api_key: str) -> dict:
    """Gather trader signals from stable endpoints.

    Source-of-truth: /me (always required). Trending and rewards are
    treated as optional — their absence no longer blocks the cycle.
    """
    signals: list[dict] = []
    errors: list[str] = []

    # 1. Agent state — required
    agent, agent_err = fetch_agent_state(api_key)
    if agent_err:
        errors.append(agent_err)
    wallet_status = "ok" if agent else "unavailable"

    # 2. Rewards — optional
    rewards_list, rewards_err = fetch_agent_rewards(api_key)
    rewards_source = "/agent/rewards" if rewards_list and not rewards_err else None
    if rewards_err:
        errors.append(rewards_err)

    # 3. Trending — optional (legacy /trending often 404s in deployed envs)
    trending_resp = tagclaw_get("/trending", api_key)
    if isinstance(trending_resp, dict):
        items = trending_resp.get("items") or trending_resp.get("data") or []
    elif isinstance(trending_resp, list):
        items = trending_resp
    else:
        items = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        signals.append({
            "source": "trending",
            "ticker": item.get("ticker") or item.get("symbol") or item.get("name", "unknown"),
            "confidence": 0.3,
            "direction": "neutral",
        })

    return {
        "signals": signals,
        "wallet_status": wallet_status,
        "agent": agent,
        "rewards": rewards_list,
        "rewards_source": rewards_source,
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
    # /me returning data means the core source-of-truth is live, so we treat
    # the cycle as ``ok`` even when optional signals (trending / rewards)
    # were missing. Only promote to ``blocked`` when /me itself fails.
    wallet_status = signal_assessment.get("wallet_status", "unknown")
    if not has_credentials:
        status = "blocked"
    elif wallet_status != "ok":
        status = "blocked"
    elif errors:
        status = "partial"
    else:
        status = "ok"

    return {
        "schema": "trader.result.v1",
        "status": status,
        "started_at": ts_start,
        "completed_at": now_iso(),
        "has_credentials": has_credentials,
        "wallet_status": wallet_status,
        "signals_evaluated": len(signal_assessment.get("signals", [])),
        "decisions": decisions,
        "trades_executed": trades_executed,
        "trades_ok": sum(1 for t in trades_executed if t["status"] == "ok"),
        "errors": errors,
        "execution_backend": "native-python",
        # Internal: pass raw data for canonical output publishing
        "_agent": signal_assessment.get("agent"),
        "_rewards": signal_assessment.get("rewards", []),
        "_rewards_source": signal_assessment.get("rewards_source"),
        "_signals": signal_assessment.get("signals", []),
    }


# ---------------------------------------------------------------------------
# Canonical runtime output publishers
# ---------------------------------------------------------------------------

def _fetch_bnb_balance_onchain(wallet_address: str) -> float | None:
    """Fetch native BNB balance via BSC JSON-RPC (eth_getBalance).

    Uses the same RPC endpoint as tagclaw-wallet: bsc-dataseed2.binance.org.
    Returns balance in ether (float) or None on failure.
    """
    import urllib.request

    rpc_url = "https://bsc-dataseed2.binance.org"
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [wallet_address, "latest"],
        "id": 1,
    })
    req = urllib.request.Request(
        rpc_url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        hex_val = data.get("result")
        if not hex_val or hex_val == "0x0":
            return 0.0
        return int(hex_val, 16) / 1e18
    except Exception:
        return None


def _fetch_bnb_price_usd() -> float | None:
    """Fetch BNB/USD price from TagClaw API (same endpoint as tagclaw-wallet).

    Endpoint: GET https://bsc-api.tagai.fun/tiptag/getETHPrice
    Returns price as float or None on failure.
    """
    import urllib.request

    url = "https://bsc-api.tagai.fun/tiptag/getETHPrice"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
        if not text:
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw = text
        # The endpoint may return a bare number, a string, or a JSON wrapper
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            return float(raw)
        if isinstance(raw, dict):
            for key in ("price", "ethPrice", "bnbPrice", "result", "data"):
                v = _coerce_float(raw.get(key))
                if v is not None:
                    return v
        return None
    except Exception:
        return None


def _extract_portfolio_usd(agent: dict | None, rewards: list[dict]) -> tuple[float | None, str]:
    """Derive a portfolio USD value from /me, token lists, or on-chain fallback.

    Priority order:
      1. Direct USD fields on /me (portfolio_usd, balanceUsd, etc.)
      2. Sum of token-level USD values from /me
      3. On-chain fallback: BNB balance × BNB/USD price

    Returns (value_or_None, source_label).
    """
    if isinstance(agent, dict):
        # --- Priority 1: direct USD fields on /me ---
        for key in ("portfolio_usd", "portfolioUsd", "totalUsd", "total_usd",
                    "balanceUsd", "balance_usd", "usd"):
            val = _coerce_float(agent.get(key))
            if val is not None:
                return val, f"me.{key}"
        # --- Priority 2: sum token-level USD values ---
        tokens = agent.get("tokens") or agent.get("positions") or agent.get("balances")
        if isinstance(tokens, list):
            total = 0.0
            found = False
            for entry in tokens:
                if isinstance(entry, dict):
                    v = _coerce_float(entry.get("value_usd") or entry.get("valueUsd") or entry.get("usd"))
                    if v is not None:
                        total += v
                        found = True
            if found:
                return round(total, 4), "me.tokens[].value_usd"

        # --- Priority 3: on-chain fallback (BNB balance × BNB/USD) ---
        wallet_address = (
            agent.get("eth_addr") or agent.get("ethAddr")
            or agent.get("address") or agent.get("wallet")
        )
        if wallet_address and isinstance(wallet_address, str) and wallet_address.startswith("0x"):
            bnb_balance = _fetch_bnb_balance_onchain(wallet_address)
            if bnb_balance is not None:
                bnb_price = _fetch_bnb_price_usd()
                if bnb_price is not None and bnb_price > 0:
                    portfolio = round(bnb_balance * bnb_price, 4)
                    print(f"[trader-runtime] fallback valuation: {bnb_balance:.6f} BNB × ${bnb_price:.2f} = ${portfolio:.4f}")
                    return portfolio, "onchain_bnb_fallback"

    return None, "unavailable"


def _compute_tas_trade(portfolio_usd: float | None, claimable_usd: float | None
                       ) -> tuple[float | None, dict]:
    """Compute a conservative native TAS_trade on a 0-5 scale.

    Formula (mirrors the dashboard's explainer panes):
        portfolio_norm  = min(portfolio_usd / 50, 1.0)
        claimable_norm  = min(claimable_usd / 5, 1.0)
        tas_trade       = 5.0 × (0.9 × portfolio_norm + 0.1 × claimable_norm)

    Returns (value_or_None, detail_dict). ``value`` is None when the
    portfolio is unavailable — we never fabricate a score from only
    optional inputs.
    """
    if portfolio_usd is None:
        detail = {
            "portfolio_usd_norm": None,
            "claimable_usd_norm": round(min((claimable_usd or 0.0) / CLAIMABLE_USD_BASELINE, 1.0), 4)
                if claimable_usd is not None else None,
        }
        return None, detail

    portfolio_norm = min(portfolio_usd / PORTFOLIO_USD_BASELINE, 1.0)
    claimable_norm = min((claimable_usd or 0.0) / CLAIMABLE_USD_BASELINE, 1.0) if claimable_usd is not None else 0.0
    value = 5.0 * (PORTFOLIO_WEIGHT * portfolio_norm + CLAIMABLE_WEIGHT * claimable_norm)
    value = round(min(value, 5.0), 4)
    return value, {
        "portfolio_usd_norm": round(portfolio_norm, 4),
        "claimable_usd_norm": round(claimable_norm, 4),
    }


def publish_trader_canonical(result: dict, ts_now: str, bundle_ts: str) -> None:
    """Publish canonical runtime JSON files that dashboard and input-packet read.

    Files: tas-trade, reward-status, wallet-snapshot, risk-status,
           measurement-quality.
    Written after every cycle so dashboard never shows stale bootstrap data.
    """
    status = result.get("status", "blocked")
    has_credentials = result.get("has_credentials", False)
    wallet_status = result.get("wallet_status", "unknown")
    agent_data = result.get("_agent")
    rewards_raw = result.get("_rewards") or []
    rewards_source = result.get("_rewards_source")
    signals = result.get("_signals", [])
    decisions = result.get("decisions", [])

    # ── wallet-snapshot.json ─────────────────────────────────────────────
    wallet_snapshot: dict[str, Any] = {
        "schema": "trader.wallet-snapshot.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "bundle_ts": bundle_ts,
        "status": "ok" if wallet_status == "ok" else ("blocked" if not has_credentials else "degraded"),
        "source": "/me",
    }
    if isinstance(agent_data, dict):
        wallet_snapshot["wallet_address"] = (
            agent_data.get("eth_addr") or agent_data.get("ethAddr")
            or agent_data.get("address") or agent_data.get("wallet") or None
        )
        wallet_snapshot["op"] = _coerce_float(agent_data.get("op"))
        wallet_snapshot["vp"] = _coerce_float(agent_data.get("vp"))
        balances_src = agent_data.get("balances") or agent_data.get("tokens")
        if isinstance(balances_src, dict):
            wallet_snapshot["balances"] = balances_src
        elif isinstance(balances_src, list):
            wallet_snapshot["balances"] = {
                str(b.get("tick") or b.get("symbol") or b.get("name") or f"item{i}"):
                    b.get("balance") or b.get("amount") or b.get("value_usd")
                for i, b in enumerate(balances_src) if isinstance(b, dict)
            }
        else:
            wallet_snapshot["balances"] = {}
    else:
        wallet_snapshot["wallet_address"] = None
        wallet_snapshot["balances"] = {}
        wallet_snapshot["op"] = None
        wallet_snapshot["vp"] = None
    atomic_write_json(RUNTIME_TRADER / "wallet-snapshot.json", wallet_snapshot)

    # ── reward-status.json ───────────────────────────────────────────────
    # Accept reward info from either a dedicated /agent/rewards call or
    # embedded fields on /me (some deployments colocate them).
    claimable_list: list[dict] = []
    if isinstance(rewards_raw, list) and rewards_raw:
        claimable_list = [r for r in rewards_raw if isinstance(r, dict)]
    elif isinstance(agent_data, dict):
        embedded = agent_data.get("rewards") or agent_data.get("claimable")
        if isinstance(embedded, list):
            claimable_list = [r for r in embedded if isinstance(r, dict)]

    claimable_total = 0.0
    for r in claimable_list:
        v = _coerce_float(
            r.get("reward_value_usd") or r.get("rewardValueUsd")
            or r.get("usd_value") or r.get("value_usd") or r.get("usd")
        )
        if v is not None:
            claimable_total += v
    claimable_total = round(claimable_total, 4)

    reward_status: dict[str, Any] = {
        "schema": "trader.reward-status.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "checked_at": ts_now,
        "bundle_ts": bundle_ts,
        "status": status,
        "source": rewards_source or ("me.rewards" if claimable_list and not rewards_source else "unavailable"),
        "claimable": claimable_list,
        "claimable_usd_total": claimable_total,
    }
    atomic_write_json(RUNTIME_TRADER / "reward-status.json", reward_status)

    # ── measurement-quality.json ─────────────────────────────────────────
    portfolio_usd, portfolio_source = _extract_portfolio_usd(agent_data, claimable_list)
    price_visibility = "ok" if portfolio_usd is not None else "unknown"
    mq_overall = "ok" if portfolio_usd is not None else ("partial" if has_credentials else "blocked")
    measurement_quality = {
        "schema": "trader.measurement-quality.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "bundle_ts": bundle_ts,
        "overall_status": mq_overall,
        "overall_confidence": 1.0 if portfolio_usd is not None else 0.0,
        "price_visibility": price_visibility,
        "portfolio_source": portfolio_source,
        "actionability": "full" if portfolio_usd is not None else "observe-only",
    }
    atomic_write_json(RUNTIME_TRADER / "measurement-quality.json", measurement_quality)

    # ── tas-trade.json ───────────────────────────────────────────────────
    tas_value, norm_detail = _compute_tas_trade(
        portfolio_usd,
        claimable_total if claimable_list else (0.0 if has_credentials else None),
    )

    if not has_credentials:
        tas_status = "blocked"
        null_reason = "blocked: credentials not configured"
    elif wallet_status != "ok":
        tas_status = "blocked"
        null_reason = "blocked: /me unavailable"
    elif tas_value is None:
        tas_status = "partial"
        null_reason = "partial: portfolio_usd not derived from /me or on-chain fallback — awaiting price visibility"
    else:
        tas_status = "ok"
        null_reason = None

    tas_trade: dict[str, Any] = {
        "schema": "trader.tas-trade.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "bundle_ts": bundle_ts,
        "status": tas_status,
        "value": tas_value,
        "score": tas_value,
        "display_status": tas_status,
        "null_reason": null_reason,
        "source_class": "trader-native",
        "portfolio_usd_raw": portfolio_usd,
        "portfolio_usd_norm": norm_detail.get("portfolio_usd_norm"),
        "claimable_usd_raw": claimable_total if claimable_list else None,
        "claimable_usd_norm": norm_detail.get("claimable_usd_norm"),
        "portfolio_source": portfolio_source,
        "formula": (
            f"tas_trade = 5.0 × ({PORTFOLIO_WEIGHT} × min(portfolio_usd / {PORTFOLIO_USD_BASELINE}, 1.0)"
            f" + {CLAIMABLE_WEIGHT} × min(claimable_usd / {CLAIMABLE_USD_BASELINE}, 1.0))"
        ),
        "risk_flags": [],
        "autonomy_mode": "observe-only",
        "signals_evaluated": len(signals),
        "decisions_count": len(decisions),
        "measurement_quality": {
            "overall_status": mq_overall,
            "price_visibility": price_visibility,
            "portfolio_source": portfolio_source,
        },
    }
    atomic_write_json(RUNTIME_TRADER / "tas-trade.json", tas_trade)

    # ── risk-status.json ─────────────────────────────────────────────────
    risk_flags: list[str] = []
    if not has_credentials:
        risk_flags.append("no_credentials")
    if wallet_status != "ok":
        risk_flags.append("agent_state_unavailable")
    if portfolio_usd is None and has_credentials and wallet_status == "ok":
        risk_flags.append("portfolio_price_visibility_missing")

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

    # ── raw ingest snapshot (P3) ─────────────────────────────────────────
    try:
        publish_trader_raw(agent_data, claimable_list, portfolio_usd,
                           claimable_total, tas_value, tas_status,
                           rewards_source, ts_now, result.get("errors", []))
    except Exception as exc:
        print(f"[trader-runtime] raw snapshot publisher skipped: {exc}")

    print(f"[trader-runtime] Published canonical outputs: wallet-snapshot, reward-status, measurement-quality, tas-trade, risk-status")


def publish_trader_raw(agent: dict | None, rewards: list[dict],
                       portfolio_usd: float | None, claimable_total: float,
                       tas_value: float | None, tas_status: str,
                       rewards_source: str | None, ts_now: str,
                       errors: list) -> None:
    """Write a minimal raw snapshot of trader fetches so the dashboard raw
    panel has at least a truthful artifact on every cycle."""
    RAW_TRADER.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "schema": "raw.trader.fetch-snapshot.v1",
        "generated_at": ts_now,
        "sources": {
            "me": "ok" if agent else "unavailable",
            "rewards": rewards_source or ("unavailable" if not rewards else "me.rewards"),
        },
        "agent_summary": {
            "op": _coerce_float((agent or {}).get("op")),
            "vp": _coerce_float((agent or {}).get("vp")),
            "wallet_address": (
                (agent or {}).get("eth_addr") or (agent or {}).get("ethAddr")
                or (agent or {}).get("address") if agent else None
            ),
        },
        "portfolio_usd_raw": portfolio_usd,
        "claimable_usd_total": claimable_total,
        "rewards_count": len(rewards),
        "tas_trade_status": tas_status,
        "tas_trade_value": tas_value,
        "errors": list(errors or []),
    }
    atomic_write_json(RAW_TRADER / "latest-fetch-snapshot.json", snapshot)


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
