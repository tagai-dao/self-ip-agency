#!/usr/bin/env python3
"""economic_reward.py — the real, USD-denominated reward signal for agent evolution.

The agency's total economic value is:

    total_economic_value_usd = portfolio_usd + claimable_usd

  portfolio_usd  — on-chain wallet value (authoritative: tas-trade.json:portfolio_usd_raw)
  claimable_usd  — accrued-but-unclaimed rewards (reward-status.json:claimable_usd_total)

Claiming a reward just relocates value (claimable -> portfolio, minus gas), so the SUM
above counts each dollar exactly once: no double-count, no key-mismatch zeros.

The reward over a window [t0, t1] is the CHANGE in that value, decomposed so that
market noise is separated from what the agent actually did:

    d_total = market_pnl + action_pnl + reward_accrual

  market_pnl     — price moved on holdings the agent did NOT touch   (NOISE — strip this)
  action_pnl     — value added/removed by the agent's buys/sells     (agent-caused)
  reward_accrual — new rewards earned in the window (d claimable)     (agent-caused)

  attributable_usd = action_pnl + reward_accrual   <-- the reward an arm should be judged on

Rewarding `attributable_usd` (not `d_total`) is the credit-assignment fix: an arm is not
praised because BNB pumped, only for economic value the agency's own actions produced.

Per-asset decomposition (exact, sums to the asset's total delta):
    d_value(asset) = bal0*(p1-p0)        # price effect on initial holding  -> market
                   + (bal1-bal0)*p1      # quantity effect at new price     -> action
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE') or (Path.home() / '.openclaw' / 'workspace'))
RUNTIME = WORKSPACE / 'runtime'
TRADER = RUNTIME / 'trader'


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read a JSON object. Returns (data, error). NEVER swallows silently — the whole
    point of this module is that a broken source must be visible, not coerced to 0."""
    if not path.exists():
        return None, f'missing: {path}'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        return None, f'unreadable {path}: {e}'
    if not isinstance(data, dict):
        return None, f'not-an-object: {path}'
    return data, None


def _num(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def snapshot(ts: str | None = None) -> dict[str, Any]:
    """Capture the agency's economic state right now. Records per-source success so a
    flat zero caused by a broken feed is never mistaken for a true zero balance."""
    ts = ts or _now_iso()
    sources_ok: dict[str, bool] = {}
    errors: list[str] = []

    tas_trade, err = _read_json(TRADER / 'tas-trade.json')
    sources_ok['tas-trade'] = tas_trade is not None
    if err:
        errors.append(err)
    portfolio_usd = _num((tas_trade or {}).get('portfolio_usd_raw'))
    if portfolio_usd is None:
        portfolio_usd = _num((tas_trade or {}).get('onchain_total_usd'))

    reward_status, err = _read_json(TRADER / 'reward-status.json')
    sources_ok['reward-status'] = reward_status is not None
    if err:
        errors.append(err)
    claimable_usd = _num((reward_status or {}).get('claimable_usd_total'))

    # Per-asset {balance, price_usd, value_usd} for market/action decomposition.
    baseline, err = _read_json(TRADER / 'portfolio-baseline.json')
    sources_ok['portfolio-baseline'] = baseline is not None
    if err:
        errors.append(err)
    assets: dict[str, dict[str, float]] = {}
    for sym, a in ((baseline or {}).get('assets') or {}).items():
        if not isinstance(a, dict):
            continue
        assets[sym] = {
            'balance': _num(a.get('balance')) or 0.0,
            'price_usd': _num(a.get('price_usd')) or 0.0,
            'value_usd': _num(a.get('value_usd')) or 0.0,
        }

    total = (portfolio_usd or 0.0) + (claimable_usd or 0.0)
    return {
        'ts': ts,
        'portfolio_usd': portfolio_usd,
        'claimable_usd': claimable_usd,
        'total_economic_value_usd': round(total, 6),
        'assets': assets,
        'sources_ok': sources_ok,
        'errors': errors,
        'complete': portfolio_usd is not None and claimable_usd is not None,
    }


def window_reward(snap0: dict[str, Any], snap1: dict[str, Any]) -> dict[str, Any]:
    """Decomposed economic reward for the window [snap0.ts, snap1.ts].

    `attributable_usd` (action + reward accrual, market noise removed) is the signal an
    arm should be judged on."""
    a0 = snap0.get('assets') or {}
    a1 = snap1.get('assets') or {}
    market_pnl = 0.0
    action_pnl = 0.0
    per_asset: dict[str, dict[str, float]] = {}
    for sym in set(a0) | set(a1):
        bal0 = (a0.get(sym) or {}).get('balance', 0.0)
        bal1 = (a1.get(sym) or {}).get('balance', 0.0)
        p0 = (a0.get(sym) or {}).get('price_usd', 0.0)
        p1 = (a1.get(sym) or {}).get('price_usd', 0.0)
        # If an asset only appears on one side, fall back to the known price so the
        # decomposition stays exact (no phantom price effect on a zero holding).
        if p0 == 0.0 and bal0 == 0.0:
            p0 = p1
        if p1 == 0.0 and bal1 == 0.0:
            p1 = p0
        market = bal0 * (p1 - p0)
        action = (bal1 - bal0) * p1
        market_pnl += market
        action_pnl += action
        per_asset[sym] = {'market_usd': round(market, 6), 'action_usd': round(action, 6)}

    c0 = snap0.get('claimable_usd')
    c1 = snap1.get('claimable_usd')
    reward_accrual = (c1 or 0.0) - (c0 or 0.0)

    t0 = snap0.get('total_economic_value_usd') or 0.0
    t1 = snap1.get('total_economic_value_usd') or 0.0
    d_total = t1 - t0
    attributable = action_pnl + reward_accrual
    return {
        'window': [snap0.get('ts'), snap1.get('ts')],
        'd_total_usd': round(d_total, 6),
        'market_pnl_usd': round(market_pnl, 6),
        'action_pnl_usd': round(action_pnl, 6),
        'reward_accrual_usd': round(reward_accrual, 6),
        'attributable_usd': round(attributable, 6),
        'per_asset': per_asset,
        'complete': bool(snap0.get('complete')) and bool(snap1.get('complete')),
    }


if __name__ == '__main__':
    snap = snapshot()
    print(json.dumps(snap, indent=2, ensure_ascii=False))
    if not snap['complete']:
        print('\nWARNING: snapshot incomplete — a reward source failed to read. '
              'A zero here would be a BUG, not a true zero.', file=sys.stderr)
        sys.exit(1)
