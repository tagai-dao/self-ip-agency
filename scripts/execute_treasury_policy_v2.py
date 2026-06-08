#!/usr/bin/env python3
"""Execute V2 treasury-policy as the trader treasury worker.

Conservative V2 behavior:
- requires an active treasury-policy
- requires treasury lock acquisition
- selects exact action inside policy envelope
- supports claim execution first; trading remains disabled unless policy allows
- no automatic retries on failure
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS

ROOT = (MAIN_WS)
RUNTIME = ROOT / 'runtime'
# Worker autonomy model (2026-03-25): Trader is self-authorizing via tas-trade.json.
# Main's treasury-policy.json is retained as a reference/override but is NOT the execution gate.
TAS_TRADE = RUNTIME / 'trader' / 'tas-trade.json'
TREASURY_POLICY = RUNTIME / 'main' / 'treasury-policy.json'   # reference only
REWARD_STATUS = RUNTIME / 'trader' / 'reward-status.json'
EXECUTION_RECORD = RUNTIME / 'trader' / 'execution-record.json'
TREASURY_EXECUTION_PLAN = RUNTIME / 'trader' / 'treasury-execution-plan.json'
TREASURY_EXECUTION_RESULT = RUNTIME / 'trader' / 'treasury-execution-result.json'
TREASURY_HISTORY = RUNTIME / 'shared' / 'treasury-history.json'
STRATEGY_PLAN = RUNTIME / 'main' / 'strategy-plan.json'
EXECUTION_LEDGER_DIR = RUNTIME / 'trader'
LOCKS = RUNTIME / 'shared' / 'locks.json'
CREDENTIALS = Path.home() / '.config' / 'tagclaw' / 'credentials.json'
BASE_URL = 'https://bsc-api.tagai.fun/tagclaw'
BUDGET_ALLOCATION = RUNTIME / 'shared' / 'budget-allocation.json'
LOCK_NAME = 'treasury_execution_lock'
LOCK_TTL_SECONDS = 1800
COMMUNITY_HEAT = RUNTIME / 'shared' / 'community-heat.json'


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _override_ticks_from_heat() -> set[str]:
    """Ticks under an active owner priority_override (strategic hold). These are
    NEVER sold — hard guarantee at the execution layer (e.g. BUIDL). Read
    directly from community-heat.json as defense-in-depth on top of the
    decision layer's priority_override_exempt list."""
    out: set[str] = set()
    try:
        heat = read_json(COMMUNITY_HEAT) or {}
    except Exception:
        return out
    po = heat.get('priority_override')
    candidates = po if isinstance(po, list) else ([po] if isinstance(po, dict) else [])
    for o in candidates:
        if isinstance(o, dict) and o.get('active') and o.get('tick'):
            out.add(str(o['tick']))
    return out


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec='seconds')


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    t = s.strip()
    if t.endswith('Z'):
        t = t[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(t)
    except Exception:
        return None


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, path)


def execution_ledger_path(executed_at: str | None = None) -> Path:
    dt = parse_dt(executed_at) or now()
    return EXECUTION_LEDGER_DIR / f"executions-{dt.strftime('%Y-%m-%d')}.json"


def _remote_response(item: dict[str, Any]) -> dict[str, Any]:
    remote = item.get('remote') or {}
    response = remote.get('response') or {}
    return response if isinstance(response, dict) else {}


def _ledger_identifier(item: dict[str, Any], executed_at: str, run_id: str | None, index: int) -> str:
    response = _remote_response(item)
    tx_hash = response.get('hash') or response.get('txHash') or item.get('tx_hash')
    order_id = response.get('orderId') or response.get('order_id') or item.get('order_id')
    if tx_hash:
        return f"tx:{tx_hash}"
    if order_id:
        return f"order:{order_id}"
    base = run_id or 'legacy'
    action = item.get('type') or 'unknown'
    tick = item.get('tick') or 'unknown'
    status = item.get('status') or item.get('result_status') or 'unknown'
    return f"{base}:{executed_at}:{action}:{tick}:{status}:{index}"


def build_execution_ledger_entry(item: dict[str, Any], executed_at: str, run_id: str | None, index: int, source_agent: str = 'trader') -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    action = item.get('type')
    if not action:
        return None
    response = _remote_response(item)
    status = item.get('status') or item.get('result_status')
    tick = item.get('tick')
    tx_hash = response.get('hash') or response.get('txHash') or item.get('tx_hash')
    order_id = response.get('orderId') or response.get('order_id') or item.get('order_id')

    amount = None
    amount_unit = None
    usd_value = None
    if action == 'buy':
        amount = item.get('buy_bnb')
        amount_unit = 'BNB'
        usd_value = item.get('buy_usd')
    elif action == 'sell':
        amount = item.get('sell_amount')
        amount_unit = tick
        usd_value = item.get('sell_usd')
    elif action == 'claim':
        amount = item.get('claimable_amount')
        amount_unit = tick
        usd_value = item.get('estimated_value_usd')

    entry = {
        'id': _ledger_identifier(item, executed_at, run_id, index),
        'ts': executed_at,
        'run_id': run_id,
        'source_agent': source_agent,
        'action': action,
        'tick': tick,
        'amount': amount,
        'amount_unit': amount_unit,
        'usd': usd_value,
        'raw_amount': item.get('raw_amount') or item.get('eth_amount_wei'),
        'tx_hash': tx_hash,
        'order_id': order_id,
        'status': status,
        'trigger_reason': item.get('trigger_reason'),
        'balance_before': item.get('balance_before') or item.get('bnb_balance_before'),
        'balance_after': item.get('balance_after'),
        'remote_route': response.get('route'),
        'approve_hash': response.get('approveHash') or response.get('approve_hash'),
        'expected_amount': response.get('expectedAmount') or response.get('expected_amount'),
        'expected_receive': response.get('expectedReceive') or response.get('expected_receive'),
        'remote': item.get('remote'),
    }
    return entry


def append_execution_ledger(results: list[dict[str, Any]], executed_at: str, run_id: str, source_agent: str = 'trader') -> None:
    path = execution_ledger_path(executed_at)
    ledger = read_json(path) or {'version': 'v1', 'date': path.stem.replace('executions-', ''), 'updated_at': executed_at, 'items': []}
    items = ledger.get('items') if isinstance(ledger.get('items'), list) else []
    existing_ids = {str(item.get('id')) for item in items if isinstance(item, dict) and item.get('id')}

    for index, item in enumerate(results):
        entry = build_execution_ledger_entry(item, executed_at, run_id, index, source_agent=source_agent)
        if not entry:
            continue
        if entry['id'] in existing_ids:
            continue
        items.append(entry)
        existing_ids.add(entry['id'])

    ledger['version'] = 'v1'
    ledger['date'] = path.stem.replace('executions-', '')
    ledger['updated_at'] = executed_at
    ledger['items'] = items[-500:]
    atomic_write_json(path, ledger)


def backfill_execution_ledgers_from_treasury_history(source_agent: str = 'trader') -> list[str]:
    history = read_json(TREASURY_HISTORY) or {}
    written_paths: set[str] = set()
    for index, item in enumerate(history.get('items') or []):
        if not isinstance(item, dict):
            continue
        executed_at = item.get('executed_at') or history.get('updated_at') or iso()
        run_id = item.get('run_id') or 'backfill:treasury-history'
        path = execution_ledger_path(executed_at)
        ledger = read_json(path) or {'version': 'v1', 'date': path.stem.replace('executions-', ''), 'updated_at': executed_at, 'items': []}
        entries = ledger.get('items') if isinstance(ledger.get('items'), list) else []
        existing_ids = {str(entry.get('id')) for entry in entries if isinstance(entry, dict) and entry.get('id')}
        entry = build_execution_ledger_entry(item, executed_at, run_id, index, source_agent=source_agent)
        if not entry or entry['id'] in existing_ids:
            continue
        entries.append(entry)
        ledger['version'] = 'v1'
        ledger['date'] = path.stem.replace('executions-', '')
        ledger['updated_at'] = max(str(ledger.get('updated_at') or executed_at), executed_at)
        ledger['items'] = entries[-500:]
        atomic_write_json(path, ledger)
        written_paths.add(str(path))
    return sorted(written_paths)


def append_treasury_history(results: list[dict[str, Any]], generated_at: str, run_id: str | None = None) -> None:
    history = read_json(TREASURY_HISTORY) or {'version': 'v1', 'updated_at': generated_at, 'items': []}
    items = history.get('items') if isinstance(history.get('items'), list) else []
    treasury_policy = read_json(TREASURY_POLICY) or {}
    strategy_plan = read_json(STRATEGY_PLAN) or {}
    cycle_id = treasury_policy.get('cycle_id') or strategy_plan.get('cycle_id')
    strategy_id = treasury_policy.get('strategy_id') or strategy_plan.get('strategy_id')
    for item in results:
        if not isinstance(item, dict):
            continue
        action_type = item.get('type')
        base = {
            'executed_at': generated_at,
            'run_id': run_id,
            'cycle_id': cycle_id,
            'strategy_id': strategy_id,
        }
        if action_type == 'claim':
            items.append({
                **base,
                'type': 'claim',
                'tick': item.get('tick'),
                'result_status': item.get('status'),
                'estimated_value_usd': item.get('estimated_value_usd'),
                'remote': item.get('remote'),
            })
        elif action_type == 'buy':
            items.append({
                **base,
                'type': 'buy',
                'tick': item.get('tick'),
                'result_status': item.get('status'),
                'eth_amount_wei': item.get('eth_amount_wei'),
                'buy_bnb': item.get('buy_bnb'),
                'buy_usd': item.get('buy_usd'),
                'bnb_price_usd': item.get('bnb_price_usd'),
                'token_price_usd': item.get('token_price_usd'),
                'bnb_balance_before': item.get('bnb_balance_before'),
                'remote': item.get('remote'),
            })
        elif action_type == 'sell':
            items.append({
                **base,
                'type': 'sell',
                'tick': item.get('tick'),
                'result_status': item.get('status'),
                'raw_amount': item.get('raw_amount'),
                'sell_amount': item.get('sell_amount'),
                'sell_usd': item.get('sell_usd'),
                'token_price_usd': item.get('token_price_usd'),
                'balance_before': item.get('balance_before'),
                'trigger_reason': item.get('trigger_reason'),
                'remote': item.get('remote'),
            })
    history['version'] = 'v1'
    history['updated_at'] = generated_at
    history['items'] = items[-200:]
    atomic_write_json(TREASURY_HISTORY, history)


def refresh_runtime_status() -> None:
    script = ROOT / 'scripts' / 'build_runtime_status_v2.py'
    subprocess.run(['python3', str(script)], check=False, capture_output=True, text=True)


def build_treasury_execution_plan(run_id: str, tas_trade: dict[str, Any], reward_status: dict[str, Any], selected_action: str | None = None, actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    guardrails = tas_trade.get('guardrails') or {}
    return {
        'version': 'v1',
        'plan_kind': 'treasury-execution-plan',
        'agent': 'trader',
        'executor': 'trader',
        'execution_owner': 'trader',
        'control_plane': 'main',
        'run_id': run_id,
        'generated_at': iso(),
        'source_class': 'trader-execution-plane',
        'control_ref': 'runtime/main/treasury-policy.json',
        'guidance_ref': 'runtime/main/trader-guidance.json',
        'autonomy_ref': 'runtime/trader/tas-trade.json',
        'reward_status_ref': 'runtime/trader/reward-status.json',
        'status': 'ready' if (actions or selected_action not in {None, 'hold', 'no-trade'}) else 'hold',
        'autonomy_mode': tas_trade.get('autonomy_mode', 'conservative'),
        'strategy_action': tas_trade.get('strategy_action'),
        'planning_focus': tas_trade.get('planning_focus'),
        'payload': {
            'recommended_actions': list(tas_trade.get('recommended_actions') or []),
            'selected_action': selected_action,
            'action_count': len(actions or []),
            'claimable_count': len(reward_status.get('claimable') or []) if isinstance(reward_status, dict) else 0,
            'guardrails': guardrails,
        },
        'notes': 'Trader-owned treasury execution plan derived from tas-trade autonomy + reward status.',
    }


def write_treasury_execution_result(out: dict[str, Any]) -> None:
    atomic_write_json(EXECUTION_RECORD, out)
    projected = dict(out)
    projected['result_kind'] = 'treasury-execution-result'
    projected['executor'] = 'trader'
    projected['execution_owner'] = 'trader'
    projected['control_plane'] = 'main'
    projected['source_class'] = 'trader-execution-plane'
    projected['control_ref'] = 'runtime/main/treasury-policy.json'
    projected['guidance_ref'] = 'runtime/main/trader-guidance.json'
    projected['legacy_result_ref'] = 'runtime/trader/execution-record.json'
    projected['plan_ref'] = 'runtime/trader/treasury-execution-plan.json'
    atomic_write_json(TREASURY_EXECUTION_RESULT, projected)


def load_api_key() -> str:
    data = read_json(CREDENTIALS)
    if not data or not data.get('api_key'):
        raise RuntimeError(f'missing api_key in {CREDENTIALS}')
    api_key = str(data['api_key']).strip()
    if not api_key or api_key.upper() in {'DUMMY', 'REPLACE_ME', 'PLACEHOLDER'}:
        raise RuntimeError(f'invalid placeholder api_key in {CREDENTIALS}')
    if not api_key.startswith('tagclaw_'):
        raise RuntimeError(f'invalid api_key format in {CREDENTIALS}')
    return api_key


def tagclaw_get(api_key: str, endpoint: str, params: dict | None = None) -> dict[str, Any]:
    """Read from TagClaw via curl subprocess (GET)."""
    url = f'{BASE_URL}/{endpoint}'
    if params:
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{url}?{query}'
    try:
        proc = subprocess.run(
            ['curl', '-sS', url,
             '-H', f'Authorization: Bearer {api_key}',
             '-H', 'Accept: application/json'],
            capture_output=True, text=True, timeout=30,
        )
        raw = (proc.stdout or '').strip()
        if proc.returncode != 0:
            return {'ok': False, 'error': (proc.stderr or raw or 'curl GET failed').strip()}
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {'raw': raw}
        if isinstance(parsed, dict) and parsed.get('success') is True:
            return {'ok': True, 'response': parsed}
        return {'ok': False, 'error': raw or 'unknown error', 'response': parsed}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def tagclaw_post(api_key: str, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Write to TagClaw via curl subprocess.

    Reason: align treasury worker with the stable direct API path already used by
    the social worker, and avoid urllib-specific write-path failures.
    """
    body = json.dumps(payload, ensure_ascii=False)
    cmd = [
        'curl', '-sS',
        '-X', 'POST', f'{BASE_URL}/{endpoint}',
        '-H', f'Authorization: Bearer {api_key}',
        '-H', 'Content-Type: application/json',
        '-d', body,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        raw = (proc.stdout or '').strip()
        if proc.returncode != 0:
            return {'ok': False, 'status': None, 'error': (proc.stderr or raw or 'curl failed').strip()}
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {'raw': raw}
        if isinstance(parsed, dict) and parsed.get('success') is True:
            return {'ok': True, 'status': 200, 'response': parsed}
        err_text = raw if raw else (proc.stderr or 'unknown error')
        return {'ok': False, 'status': parsed.get('status') if isinstance(parsed, dict) else None, 'error': err_text, 'response': parsed}
    except Exception as e:
        return {'ok': False, 'status': None, 'error': str(e)}


def acquire_lock(run_id: str) -> bool:
    locks = read_json(LOCKS) or {'version': 'v1'}
    current = (locks.get(LOCK_NAME) or {}) if isinstance(locks, dict) else {}
    state = current.get('state')
    expires_at = parse_dt(current.get('expires_at'))
    if state == 'acquired' and expires_at and expires_at > now():
        return False
    locks[LOCK_NAME] = {
        'state': 'acquired',
        'owner': 'trader',
        'run_id': run_id,
        'acquired_at': iso(),
        'expires_at': iso(now() + timedelta(seconds=LOCK_TTL_SECONDS)),
    }
    atomic_write_json(LOCKS, locks)
    return True


def release_lock() -> None:
    locks = read_json(LOCKS) or {'version': 'v1'}
    locks[LOCK_NAME] = {
        'state': 'unlocked',
        'owner': None,
        'run_id': None,
        'acquired_at': None,
        'expires_at': None,
    }
    atomic_write_json(LOCKS, locks)


def get_bnb_balance_ether() -> float | None:
    """Fetch current BNB balance in ether."""
    try:
        proc = subprocess.run(
            ['node', str(Path.home() / 'tagclaw-wallet' / 'bin' / 'wallet.js'),
             'balance-bnb', '--address', '0x2eaAB48Bb77DF7963731b11cBaed98B3c0baFE78'],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout.strip())
        return float(data.get('ether', 0))
    except Exception:
        return None


def get_token_price(tick: str) -> dict[str, Any] | None:
    """Fetch token price via wallet.js price-token."""
    try:
        proc = subprocess.run(
            ['node', str(Path.home() / 'tagclaw-wallet' / 'bin' / 'wallet.js'),
             'price-token', '--tick', tick],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout.strip())
    except Exception:
        return None


def load_private_key() -> str | None:
    """Load private key from credentials at runtime. Never persist."""
    data = read_json(CREDENTIALS)
    if not data:
        return None
    pk = data.get('privateKey') or data.get('private_key')
    if not pk or not str(pk).startswith('0x'):
        return None
    return str(pk)


def execute_buy(tick: str, eth_amount_wei: int, private_key: str) -> dict[str, Any]:
    """Execute a buy-token via wallet.js. Returns result dict."""
    cmd = [
        'node', str(Path.home() / 'tagclaw-wallet' / 'bin' / 'wallet.js'),
        'buy-token',
        '--private-key', private_key,
        '--tick', tick,
        '--eth-amount', str(eth_amount_wei),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        raw = (proc.stdout or '').strip()
        if proc.returncode != 0:
            return {'ok': False, 'error': (proc.stderr or raw or 'wallet.js buy-token failed').strip()}
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {'raw': raw}
        return {'ok': True, 'response': parsed}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def choose_buy_target(payload: dict[str, Any], reward_status: dict[str, Any], bnb_balance: float) -> dict[str, Any] | None:
    """Choose which tick to buy and how much, based on policy constraints."""
    allowed_ticks = payload.get('allowed_ticks') or []
    max_budget_usd = float(payload.get('max_budget_usd', 0))
    min_bnb_reserve = float(payload.get('min_bnb_reserve', 0.005))

    if max_budget_usd <= 0 or not allowed_ticks:
        return None

    available_bnb = bnb_balance - min_bnb_reserve
    if available_bnb <= 0:
        return None

    # Pick the tick with the highest reward accumulation (signal of value)
    best_tick = None
    best_reward_usd = 0.0
    for item in reward_status.get('claimable') or []:
        tick = item.get('tick')
        if tick not in allowed_ticks:
            continue
        try:
            usd = float(item.get('reward_value_usd', 0))
        except Exception:
            usd = 0.0
        if usd > best_reward_usd:
            best_reward_usd = usd
            best_tick = tick

    if not best_tick:
        # Fallback: pick first allowed tick
        best_tick = allowed_ticks[0]

    # Get current price to compute buy amount
    price_info = get_token_price(best_tick)
    if not price_info:
        return None

    bnb_price_usd = float(price_info.get('bnbPriceUsd', 0))
    if bnb_price_usd <= 0:
        return None

    # How much BNB for max_budget_usd?
    buy_bnb = max_budget_usd / bnb_price_usd
    # Cap at available BNB
    buy_bnb = min(buy_bnb, available_bnb)
    # Convert to wei (18 decimals)
    buy_wei = int(buy_bnb * 1e18)

    # P3: buy_small cap — limit to 0.001 BNB (~$0.5) regardless of budget
    strategy_exp = read_json(RUNTIME / 'shared' / 'strategy-experiment.json') or {}
    _credit_strategy = ((strategy_exp.get('track_a') or {}).get('current_arm') or {}).get('credit_strategy', 'hold')
    if _credit_strategy == 'buy_small':
        BUY_SMALL_WEI = int(0.001 * 10**18)
        buy_wei = min(buy_wei, BUY_SMALL_WEI)

    if buy_wei <= 0:
        return None

    buy_usd = buy_bnb * bnb_price_usd

    return {
        'tick': best_tick,
        'eth_amount_wei': buy_wei,
        'buy_bnb': buy_bnb,
        'buy_usd': buy_usd,
        'bnb_price_usd': bnb_price_usd,
        'token_price_usd': float(price_info.get('tokenPriceUsd', 0)),
    }


def count_today_executions(ledger_path: Path | None = None) -> dict[str, Any]:
    """Read today's execution ledger and return counts by type and tick.

    Returns a dict with keys: total, sells, buys, claims, by_tick.
    Resilient: if the file is missing or malformed, returns zero counts.
    """
    empty: dict[str, Any] = {'total': 0, 'sells': 0, 'buys': 0, 'claims': 0, 'by_tick': {}}
    try:
        path = ledger_path or execution_ledger_path()
        data = read_json(path)
        if not isinstance(data, dict):
            return empty
        items = data.get('items')
        if not isinstance(items, list):
            return empty
        total = 0
        sells = 0
        buys = 0
        claims = 0
        by_tick: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            action = item.get('action') or item.get('type')
            tick = str(item.get('tick') or '')
            total += 1
            if action == 'sell':
                sells += 1
            elif action == 'buy':
                buys += 1
            elif action == 'claim':
                claims += 1
            if tick:
                by_tick[tick] = by_tick.get(tick, 0) + 1
        return {'total': total, 'sells': sells, 'buys': buys, 'claims': claims, 'by_tick': by_tick}
    except Exception:
        return empty


def choose_actions(policy: dict[str, Any], reward_status: dict[str, Any], treasury_history: dict[str, Any] | None = None) -> tuple[str | None, list[dict[str, Any]]]:
    payload = policy.get('payload') or {}
    if payload.get('execution_allowed') is not True:
        return None, []

    actions: list[dict[str, Any]] = []
    selected_action: str | None = None

    previous_below_min: dict[str, float] = {}
    if isinstance(treasury_history, dict):
        for item in treasury_history.get('items') or []:
            if not isinstance(item, dict) or item.get('type') != 'claim':
                continue
            if item.get('result_status') != 'blocked':
                continue
            remote = item.get('remote') or {}
            err = str((remote.get('response') or {}).get('error', '') or remote.get('error', '')).lower()
            if 'below minimum' not in err:
                continue
            tick = item.get('tick')
            try:
                est = float(item.get('estimated_value_usd'))
            except Exception:
                est = None
            if tick and est is not None:
                previous_below_min[str(tick)] = max(est, previous_below_min.get(str(tick), 0.0))

    # Priority 1: Claims
    if payload.get('claims_allowed'):
        claimable_items = []
        for item in reward_status.get('claimable') or []:
            tick = item.get('tick')
            try:
                usd = float(item.get('reward_value_usd'))
            except Exception:
                usd = None
            if not tick or usd is None or usd <= 0:
                continue
            prior_failed_usd = previous_below_min.get(str(tick))
            if prior_failed_usd is not None and usd <= prior_failed_usd:
                continue
            claimable_items.append({'type': 'claim', 'tick': tick, 'estimated_value_usd': usd})
        if claimable_items:
            selected_action = 'claim'
            actions.extend(claimable_items)

    # Per-day limits (read once for both sell and buy guards)
    today_counts = count_today_executions()
    today_total: int = today_counts['total']
    today_sells: int = today_counts['sells']
    today_by_tick: dict[str, int] = today_counts['by_tick']
    max_trades_per_day: int = int(payload.get('max_trades_per_day', 2))
    max_sells_per_day: int = int(payload.get('max_sells_per_day', 1))
    max_same_tick_trades_per_day: int = int(payload.get('max_same_tick_trades_per_day', 1))

    # Priority 2: Sell (if sell triggers are met)
    if selected_action is None and payload.get('trading_allowed'):
        allowed_actions = payload.get('allowed_actions') or []
        if 'sell' in allowed_actions:
            trigger_info = check_sell_triggers(payload, reward_status)
            if trigger_info.get('should_sell'):
                sell_target = choose_sell_target(payload, reward_status, trigger_info)
                if sell_target:
                    sell_tick = sell_target['tick']
                    tick_count = today_by_tick.get(str(sell_tick), 0)
                    if today_sells >= max_sells_per_day:
                        print(f'[choose_actions] sell skipped: today_sells={today_sells} >= max_sells_per_day={max_sells_per_day}')
                    elif today_total >= max_trades_per_day:
                        print(f'[choose_actions] sell skipped: today_total={today_total} >= max_trades_per_day={max_trades_per_day}')
                    elif tick_count >= max_same_tick_trades_per_day:
                        print(f'[choose_actions] sell skipped: today_by_tick[{sell_tick}]={tick_count} >= max_same_tick_trades_per_day={max_same_tick_trades_per_day}')
                    else:
                        selected_action = 'sell'
                        actions.append({
                            'type': 'sell',
                            'tick': sell_tick,
                            'raw_amount': sell_target['raw_amount'],
                            'sell_amount': sell_target['sell_amount'],
                            'sell_usd': sell_target['sell_usd'],
                            'token_price_usd': sell_target['token_price_usd'],
                            'balance_before': sell_target['balance_before'],
                            'trigger_reason': sell_target['trigger_reason'],
                        })

    # Priority 3: Buy (only if no claims or sells to execute)
    if selected_action is None and payload.get('trading_allowed'):
        allowed_actions = payload.get('allowed_actions') or []
        if 'buy' in allowed_actions:
            bnb_balance = get_bnb_balance_ether()
            if bnb_balance is not None:
                buy_target = choose_buy_target(payload, reward_status, bnb_balance)
                if buy_target:
                    buy_tick = buy_target['tick']
                    tick_count = today_by_tick.get(str(buy_tick), 0)
                    if today_total >= max_trades_per_day:
                        print(f'[choose_actions] buy skipped: today_total={today_total} >= max_trades_per_day={max_trades_per_day}')
                        selected_action = 'no-trade'
                    elif tick_count >= max_same_tick_trades_per_day:
                        print(f'[choose_actions] buy skipped: today_by_tick[{buy_tick}]={tick_count} >= max_same_tick_trades_per_day={max_same_tick_trades_per_day}')
                        selected_action = 'no-trade'
                    else:
                        selected_action = 'buy'
                        actions.append({
                            'type': 'buy',
                            'tick': buy_tick,
                            'eth_amount_wei': buy_target['eth_amount_wei'],
                            'buy_bnb': buy_target['buy_bnb'],
                            'buy_usd': buy_target['buy_usd'],
                            'bnb_price_usd': buy_target['bnb_price_usd'],
                            'token_price_usd': buy_target['token_price_usd'],
                            'bnb_balance_before': bnb_balance,
                        })
                else:
                    selected_action = 'no-trade'
            else:
                selected_action = 'no-trade'
        else:
            selected_action = 'no-trade'

    if selected_action is None:
        selected_action = 'hold'

    return selected_action, actions


def get_erc20_balance(token_address: str) -> float | None:
    """Fetch ERC20 token balance (formatted, not raw)."""
    try:
        proc = subprocess.run(
            ['node', str(Path.home() / 'tagclaw-wallet' / 'bin' / 'wallet.js'),
             'balance-erc20', '--address', '0x2eaAB48Bb77DF7963731b11cBaed98B3c0baFE78',
             '--token', token_address],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout.strip())
        return float(data.get('formatted', 0))
    except Exception:
        return None


# Token contract addresses for sell operations
TOKEN_CONTRACTS = {
    'TagClaw': '0xe7324f2987acd88ee7286eb9dab0ee926ad36a68',
    'BUIDL': '0x32ef878D527d860339818571E8DA17005110f04E',
    'TTAI': '0x8e11E90B463bf521382E2B88539F053270a3848c',
}


def execute_sell(tick: str, raw_amount: str, private_key: str) -> dict[str, Any]:
    """Execute a sell-token via wallet.js. Returns result dict."""
    cmd = [
        'node', str(Path.home() / 'tagclaw-wallet' / 'bin' / 'wallet.js'),
        'sell-token',
        '--private-key', private_key,
        '--tick', tick,
        '--amount', str(raw_amount),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        raw = (proc.stdout or '').strip()
        if proc.returncode != 0:
            return {'ok': False, 'error': (proc.stderr or raw or 'wallet.js sell-token failed').strip()}
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {'raw': raw}
        return {'ok': True, 'response': parsed}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def execute_sells(actions: list[dict[str, Any]], private_key: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for action in actions:
        tick = action.get('tick')
        raw_amount = action.get('raw_amount')
        if not tick or not raw_amount:
            results.append({'type': 'sell', 'status': 'blocked', 'error': 'missing tick or raw_amount'})
            continue
        resp = execute_sell(tick, raw_amount, private_key)
        results.append({
            'type': 'sell',
            'tick': tick,
            'raw_amount': raw_amount,
            'sell_amount': action.get('sell_amount'),
            'sell_usd': action.get('sell_usd'),
            'token_price_usd': action.get('token_price_usd'),
            'balance_before': action.get('balance_before'),
            'trigger_reason': action.get('trigger_reason'),
            'status': 'ok' if resp['ok'] else 'blocked',
            'remote': resp,
        })
    return results


def check_sell_triggers(payload: dict[str, Any], reward_status: dict[str, Any]) -> dict[str, Any]:
    """Check if sell conditions are met based on TAS_social and reward concentration."""
    sell_triggers = payload.get('sell_triggers') or {}
    if not sell_triggers:
        return {'should_sell': False, 'reason': 'no sell_triggers configured'}

    tas_social_below = float(sell_triggers.get('tas_social_below', 0.3))
    reward_concentration_above = float(sell_triggers.get('reward_concentration_above', 0.6))

    # Read current TAS_social from runtime.
    # Canonical source is bookmarker runtime; main runtime is a compatibility mirror.
    tas_social_data = read_json(RUNTIME / 'main' / 'tas-social.json') or {}
    if tas_social_data.get('value') is None:
        tas_social_data = read_json(RUNTIME / 'bookmarker' / 'tas-social.json') or {}
    current_tas_social = tas_social_data.get('value')

    reasons = []

    # Trigger 1: TAS_social is low (community engagement with agent is declining)
    if current_tas_social is not None and current_tas_social < tas_social_below:
        reasons.append(f'TAS_social={current_tas_social:.2f} < threshold={tas_social_below}')

    # Trigger 2: Reward concentration is high
    # High concentration = one tick dominates rewards, suggesting imbalanced exposure
    claimable = reward_status.get('claimable') or []
    if claimable:
        total_usd = sum(float(c.get('reward_value_usd', 0)) for c in claimable)
        if total_usd > 0:
            max_usd = max(float(c.get('reward_value_usd', 0)) for c in claimable)
            concentration = max_usd / total_usd
            if concentration > reward_concentration_above:
                top_tick = max(claimable, key=lambda c: float(c.get('reward_value_usd', 0))).get('tick', '?')
                reasons.append(f'reward_concentration={concentration:.2f} > threshold={reward_concentration_above} (dominated by {top_tick})')

    # Trigger 3 (P1 2026-05-28): risk-management stop-loss. The decision layer
    # flags heat-declining, override-exempt positions that are down beyond the
    # drawdown threshold. Honor it here so the sell actually executes.
    stop_loss_ticks = [s for s in (sell_triggers.get('stop_loss_ticks') or []) if isinstance(s, dict) and s.get('tick')]
    if stop_loss_ticks:
        _dd = ', '.join(f"{s['tick']} {float(s.get('drawdown_3d', 0)):.0%}" for s in stop_loss_ticks)
        reasons.append(f'STOP-LOSS: {_dd} (drawdown≤{sell_triggers.get("stop_loss_drawdown_pct")}; override-exempt)')

    return {
        'should_sell': len(reasons) > 0,
        'reasons': reasons,
        'tas_social_current': current_tas_social,
        'stop_loss_ticks': [s.get('tick') for s in stop_loss_ticks],
    }


def choose_sell_target(payload: dict[str, Any], reward_status: dict[str, Any], trigger_info: dict[str, Any]) -> dict[str, Any] | None:
    """Choose which tick to sell and how much."""
    allowed_ticks = payload.get('allowed_ticks') or []
    max_sell_usd = float(payload.get('max_sell_usd', 0))
    max_position_change_pct = float(payload.get('max_position_change_pct', 10))
    sell_triggers = payload.get('sell_triggers') or {}
    min_holding_usd = float(sell_triggers.get('min_holding_usd', 1.0))

    # P1 2026-05-28: NEVER sell a tick under an active owner priority_override
    # (strategic hold, e.g. BUIDL). Hard guarantee at the execution layer,
    # independent of upstream triggers. Owner decision: "豁免 BUIDL，止损只管其他仓".
    override_exempt = set(sell_triggers.get('priority_override_exempt') or [])
    override_exempt |= _override_ticks_from_heat()
    # If a risk-management stop-loss flagged specific ticks, sell THOSE (the
    # bleeding positions); otherwise fall back to the most-overweight tick.
    stop_loss_ticks = [s.get('tick') for s in (sell_triggers.get('stop_loss_ticks') or [])
                       if isinstance(s, dict) and s.get('tick')]
    candidate_ticks = [t for t in allowed_ticks if t not in override_exempt]
    if stop_loss_ticks:
        candidate_ticks = [t for t in candidate_ticks if t in stop_loss_ticks]

    if max_sell_usd <= 0 or not candidate_ticks:
        return None

    # Find the tick with the highest holding value (most overweight)
    best_tick = None
    best_value_usd = 0.0
    best_balance = 0.0
    best_price = 0.0

    for tick in candidate_ticks:
        contract = TOKEN_CONTRACTS.get(tick)
        if not contract:
            continue

        balance = get_erc20_balance(contract)
        if balance is None or balance <= 0:
            continue

        price_info = get_token_price(tick)
        if not price_info:
            continue

        token_price_usd = float(price_info.get('tokenPriceUsd', 0))
        if token_price_usd <= 0:
            continue

        holding_usd = balance * token_price_usd
        if holding_usd < min_holding_usd:
            continue

        if holding_usd > best_value_usd:
            best_value_usd = holding_usd
            best_tick = tick
            best_balance = balance
            best_price = token_price_usd

    if not best_tick:
        return None

    # How much to sell: min(max_sell_usd, max_position_change_pct of holding)
    max_by_pct = best_balance * (max_position_change_pct / 100.0)
    max_by_usd = max_sell_usd / best_price if best_price > 0 else 0

    sell_amount = min(max_by_pct, max_by_usd)
    if sell_amount <= 0:
        return None

    sell_usd = sell_amount * best_price
    # Convert to raw amount (18 decimals)
    raw_amount = str(int(sell_amount * 1e18))

    return {
        'tick': best_tick,
        'raw_amount': raw_amount,
        'sell_amount': sell_amount,
        'sell_usd': sell_usd,
        'token_price_usd': best_price,
        'balance_before': best_balance,
        'holding_usd_before': best_value_usd,
        'trigger_reason': '; '.join(trigger_info.get('reasons', [])),
    }


def execute_buys(actions: list[dict[str, Any]], private_key: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for action in actions:
        tick = action.get('tick')
        eth_amount_wei = action.get('eth_amount_wei')
        if not tick or not eth_amount_wei:
            results.append({'type': 'buy', 'status': 'blocked', 'error': 'missing tick or eth_amount_wei'})
            continue
        resp = execute_buy(tick, eth_amount_wei, private_key)
        results.append({
            'type': 'buy',
            'tick': tick,
            'eth_amount_wei': eth_amount_wei,
            'buy_bnb': action.get('buy_bnb'),
            'buy_usd': action.get('buy_usd'),
            'bnb_price_usd': action.get('bnb_price_usd'),
            'token_price_usd': action.get('token_price_usd'),
            'bnb_balance_before': action.get('bnb_balance_before'),
            'status': 'ok' if resp['ok'] else 'blocked',
            'remote': resp,
        })
    return results


def poll_claim_status(api_key: str, tick: str, max_polls: int = 3,
                      poll_interval: float = 5.0) -> dict[str, Any] | None:
    """Poll GET /tagclaw/agent/claimStatus until order is confirmed or gives up.

    Returns the status data dict or None if polling failed / timed out.
    """
    import time
    for _ in range(max_polls):
        resp = tagclaw_get(api_key, 'agent/claimStatus', {'tick': tick})
        if resp.get('ok'):
            data = (resp.get('response') or {}).get('data') or {}
            order_status = data.get('status') or ''
            # Terminal states: confirmed / failed / not found
            if order_status in ('confirmed', 'success', 'done', 'failed', 'error'):
                return data
            if not data.get('hasOrder', True):
                # No pending order — either never started or already settled
                return data
        time.sleep(poll_interval)
    return None


def execute_claims(api_key: str, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for action in actions:
        tick = action.get('tick')
        if not tick:
            results.append({'type': 'claim', 'status': 'blocked', 'error': 'missing tick'})
            continue
        resp = tagclaw_post(api_key, 'agent/claimReward', {'tick': tick})
        # After initiating claim, poll claimStatus to confirm gasless order state
        claim_status_data: dict[str, Any] | None = None
        if resp.get('ok'):
            claim_status_data = poll_claim_status(api_key, tick)
        results.append({
            'type': 'claim',
            'tick': tick,
            'status': 'ok' if resp['ok'] else 'blocked',
            'estimated_value_usd': action.get('estimated_value_usd'),
            'remote': resp,
            'claim_status': claim_status_data,
        })
    return results


def build_policy_from_tas_trade(tas_trade: dict, reward_status: dict) -> dict:
    """Build a treasury-policy-compatible payload from Trader's tas-trade.json.

    Replaces Main's treasury-policy as the execution gate.
    Reads guardrails and recommended_actions from tas-trade (which already mirrors dispatch-config).
    """
    autonomy_mode = tas_trade.get('autonomy_mode', 'conservative')
    recommended = set(tas_trade.get('recommended_actions') or [])
    guardrails = tas_trade.get('guardrails') or {}
    sell_triggered = bool(tas_trade.get('sell_triggered', False))

    # P1-4: Claim vs trade policy split
    # Claims are zero-risk (collecting earned rewards) → allowed even in conservative mode.
    # Buy/sell are risk-bearing → require autonomy_mode != 'conservative'.
    claims_in_recommended = 'claim' in recommended
    trading_in_recommended = ('buy' in recommended or 'sell' in recommended)
    execution_allowed = claims_in_recommended or (autonomy_mode != 'conservative' and trading_in_recommended)

    # --- Budget hard constraint: cap USD amounts from budget-allocation.json ---
    budget_doc = read_json(BUDGET_ALLOCATION) or {}
    trader_alloc = ((budget_doc.get('allocations') or {}).get('trader') or {})
    budget_usd_cap = float(trader_alloc.get('usd_budget', 0))
    raw_max_budget = guardrails.get('max_budget_usd', 2.0)
    raw_max_sell = guardrails.get('max_sell_usd', 2.0)
    enforced_max_budget = min(float(raw_max_budget), budget_usd_cap) if budget_usd_cap > 0 else float(raw_max_budget)
    enforced_max_sell = min(float(raw_max_sell), budget_usd_cap) if budget_usd_cap > 0 else float(raw_max_sell)

    return {
        'intent_kind': 'treasury-policy',
        'target_agent': 'trader',
        'status': 'active' if execution_allowed else 'revoked',
        'source': 'tas-trade-autonomy',
        'autonomy_mode': autonomy_mode,
        'payload': {
            'execution_allowed': execution_allowed,
            'claims_allowed': guardrails.get('allow_claims', True) and 'claim' in recommended,
            'trading_allowed': guardrails.get('allow_trading', True) and ('buy' in recommended or 'sell' in recommended),
            'max_budget_usd': enforced_max_budget,
            'max_sell_usd': enforced_max_sell,
            'max_trades_per_day': guardrails.get('max_trades_per_day', 2),
            'max_sells_per_day': 1,
            'max_same_tick_trades_per_day': 1,
            'min_claimable_usd': guardrails.get('min_claimable_usd', 2.0),  # guidance-driven threshold
            'allowed_ticks': ['BUIDL', 'TagClaw', 'TTAI'],
            'priority_order': ['claim', 'sell', 'buy', 'hold'],
            'sell_triggers': tas_trade.get('sell_trigger_detail') or {},
            'sell_triggered': sell_triggered,
            'min_bnb_reserve': 0.005,
        }
    }


def _extract_budget_enforcement(policy: dict[str, Any]) -> dict[str, Any]:
    """Extract budget enforcement metadata from the autonomy-derived policy."""
    budget_doc = read_json(BUDGET_ALLOCATION) or {}
    trader_alloc = ((budget_doc.get('allocations') or {}).get('trader') or {})
    budget_usd_cap = float(trader_alloc.get('usd_budget', 0))
    payload = policy.get('payload') or {}
    return {
        'enforced': True,
        'budget_usd_cap': budget_usd_cap,
        'enforced_max_budget_usd': float(payload.get('max_budget_usd', 0)),
        'enforced_max_sell_usd': float(payload.get('max_sell_usd', 0)),
        'source': 'runtime/shared/budget-allocation.json',
    }


def main() -> int:
    run_id = f'trader-exec-{now().strftime("%Y%m%dT%H%M%S")}'
    backfill_execution_ledgers_from_treasury_history()

    # Worker autonomy model: gate on tas-trade.json (Trader-owned), not Main's treasury-policy
    tas_trade = read_json(TAS_TRADE) or {}
    reward_status = read_json(REWARD_STATUS) or {}
    previous_execution = read_json(EXECUTION_RECORD) or {}
    treasury_history = read_json(TREASURY_HISTORY) or {}

    if not tas_trade:
        atomic_write_json(TREASURY_EXECUTION_PLAN, build_treasury_execution_plan(run_id, {}, reward_status, None, []))
        out = {
            'version': 'v2', 'agent': 'trader', 'run_id': run_id, 'status': 'blocked', 'generated_at': iso(),
            'autonomy_ref': 'runtime/trader/tas-trade.json', 'lock_name': LOCK_NAME, 'selected_action': None,
            'results': [], 'summary': {'attempted': 0, 'succeeded': 0, 'failed': 0}, 'notes': 'missing tas-trade.json'
        }
        write_treasury_execution_result(out)
        refresh_runtime_status()
        print(json.dumps({'status': out['status'], 'reason': out['notes']}, ensure_ascii=False))
        return 1

    autonomy_mode = tas_trade.get('autonomy_mode', 'conservative')
    recommended_actions = set(tas_trade.get('recommended_actions') or [])
    # P1-4: Claim vs trade policy split — conservative mode with claims in recommended_actions
    # should NOT bail early. Only bail if truly nothing to do.
    if not recommended_actions:
        atomic_write_json(TREASURY_EXECUTION_PLAN, build_treasury_execution_plan(run_id, tas_trade, reward_status, None, []))
        out = {
            'version': 'v2', 'agent': 'trader', 'run_id': run_id, 'status': 'noop', 'generated_at': iso(),
            'autonomy_ref': 'runtime/trader/tas-trade.json', 'lock_name': LOCK_NAME, 'selected_action': None,
            'results': [], 'summary': {'attempted': 0, 'succeeded': 0, 'failed': 0},
            'notes': f'autonomy_mode={autonomy_mode} with no recommended_actions; nothing executed',
            'autonomy_mode': autonomy_mode, 'autonomy_reason': tas_trade.get('autonomy_reason'),
        }
        write_treasury_execution_result(out)
        refresh_runtime_status()
        print(json.dumps({'status': 'noop', 'reason': out['notes']}, ensure_ascii=False))
        return 0

    # Build policy from tas-trade autonomy (compatible with choose_actions interface)
    policy = build_policy_from_tas_trade(tas_trade, reward_status)

    if not acquire_lock(run_id):
        atomic_write_json(TREASURY_EXECUTION_PLAN, build_treasury_execution_plan(run_id, tas_trade, reward_status, None, []))
        out = {
            'version': 'v2', 'agent': 'trader', 'run_id': run_id, 'status': 'blocked', 'generated_at': iso(),
            'autonomy_ref': 'runtime/trader/tas-trade.json', 'lock_name': LOCK_NAME, 'selected_action': None,
            'results': [], 'summary': {'attempted': 0, 'succeeded': 0, 'failed': 0},
            'notes': 'treasury execution lock is currently held by another run'
        }
        write_treasury_execution_result(out)
        refresh_runtime_status()
        print(json.dumps({'status': out['status'], 'reason': out['notes']}, ensure_ascii=False))
        return 1

    try:
        selected_action, actions = choose_actions(policy, reward_status, treasury_history)
        atomic_write_json(TREASURY_EXECUTION_PLAN, build_treasury_execution_plan(run_id, tas_trade, reward_status, selected_action, actions))
        if selected_action in {None, 'hold', 'no-trade'}:
            hold_note = f'autonomy_mode={autonomy_mode} but no executable treasury action selected'
            history_items = treasury_history.get('items') or [] if isinstance(treasury_history, dict) else []
            if history_items and all(
                isinstance(item, dict)
                and item.get('type') == 'claim'
                and item.get('result_status') == 'blocked'
                and 'below minimum' in str(((item.get('remote') or {}).get('response') or {}).get('error', '') or (item.get('remote') or {}).get('error', '')).lower()
                for item in history_items[-3:]
            ):
                hold_note = 'all claimable rewards remain below the per-tick minimum threshold; holding instead of retrying claim'
            out = {
                'version': 'v2', 'agent': 'trader', 'run_id': run_id, 'status': 'ok', 'generated_at': iso(),
                'autonomy_ref': 'runtime/trader/tas-trade.json', 'lock_name': LOCK_NAME, 'selected_action': selected_action,
                'autonomy_mode': autonomy_mode, 'results': [], 'summary': {'attempted': 0, 'succeeded': 0, 'failed': 0},
                'notes': hold_note
            }
            write_treasury_execution_result(out)
            refresh_runtime_status()
            print(json.dumps({'status': out['status'], 'selected_action': selected_action}, ensure_ascii=False))
            return 0

        if selected_action == 'claim':
            api_key = load_api_key()
            results = execute_claims(api_key, actions)
        elif selected_action == 'sell':
            private_key = load_private_key()
            if not private_key:
                results = [{'type': 'sell', 'status': 'blocked', 'error': 'privateKey not found in credentials'}]
            else:
                results = execute_sells(actions, private_key)
        elif selected_action == 'buy':
            private_key = load_private_key()
            if not private_key:
                results = [{'type': 'buy', 'status': 'blocked', 'error': 'privateKey not found in credentials'}]
            else:
                results = execute_buys(actions, private_key)
        else:
            results = [{'type': selected_action, 'status': 'blocked', 'error': 'execution path not implemented yet'}]

        succeeded = sum(1 for r in results if r.get('status') == 'ok')
        failed = sum(1 for r in results if r.get('status') != 'ok')
        status = 'ok' if failed == 0 else ('partial' if succeeded > 0 else 'blocked')
        generated_at = iso()
        out = {
            'version': 'v2',
            'agent': 'trader',
            'run_id': run_id,
            'status': status,
            'generated_at': generated_at,
            'autonomy_ref': 'runtime/trader/tas-trade.json',
            'autonomy_mode': autonomy_mode,
            'autonomy_reason': tas_trade.get('autonomy_reason'),
            'lock_name': LOCK_NAME,
            'selected_action': selected_action,
            'results': results,
            'summary': {'attempted': len(results), 'succeeded': succeeded, 'failed': failed},
            'notes': f'trader self-authorized execution (autonomy_mode={autonomy_mode})',
            'budget_enforcement': _extract_budget_enforcement(policy),
        }
        write_treasury_execution_result(out)
        append_treasury_history(results, generated_at, run_id=run_id)
        append_execution_ledger(results, generated_at, run_id=run_id)
        refresh_runtime_status()
        print(json.dumps({'status': status, 'selected_action': selected_action, 'attempted': len(results),
                          'succeeded': succeeded, 'failed': failed, 'autonomy_mode': autonomy_mode}, ensure_ascii=False))
        return 0 if status in {'ok', 'partial'} else 1
    finally:
        release_lock()
        refresh_runtime_status()


if __name__ == '__main__':
    raise SystemExit(main())
