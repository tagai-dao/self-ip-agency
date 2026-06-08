#!/usr/bin/env python3
"""Fast rewards check + auto-claim — no LLM, runs in <30s.

Queries rewards API, gets prices, claims ticks where reward_value_usd > $2.
Writes memory/rewards-claim-latest.json and .md, appends to YYYY-MM-DD.md,
upserts executions ledger, then calls publish_trader_runtime_v2.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from agency_paths import MAIN_WS, TRADER_WS

TRADER_ROOT = (TRADER_WS)
SCRIPTS_DIR = TRADER_ROOT / 'scripts'
MEMORY_DIR = TRADER_ROOT / 'memory'
RUNTIME_DIR = (MAIN_WS) / 'runtime' / 'trader'
CREDENTIALS_PATH = Path.home() / '.config' / 'tagclaw' / 'credentials.json'
WALLET_CLI = str(Path.home() / 'tagclaw-wallet' / 'bin' / 'wallet.js')
ONCHAINOS_CLI = str(Path.home() / '.local' / 'bin' / 'onchainos')
PUBLISH_SCRIPT = str(SCRIPTS_DIR / 'publish_trader_runtime_v2.py')

API_BASE = 'https://bsc-api.tagai.fun/tagclaw'
REWARDS_ENDPOINT = f'{API_BASE}/agent/rewards'
CLAIM_ENDPOINT = f'{API_BASE}/agent/claimReward'
CLAIM_THRESHOLD_USD = 2.0

WALLET_TIMEOUT = 10    # seconds per wallet CLI call
API_TIMEOUT = 5        # seconds per API call


def now_cst() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now_cst().isoformat(timespec='seconds')


def now_label() -> str:
    return now_cst().strftime('%Y-%m-%d %H:%M:%S') + ' CST'


def atomic_write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8', suffix='.tmp') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8', suffix='.tmp') as tmp:
        tmp.write(content)
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def read_credentials() -> dict:
    text = CREDENTIALS_PATH.read_text(encoding='utf-8')
    return json.loads(text)


def api_get(url: str, api_key: str) -> dict:
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8'))


def api_post(url: str, api_key: str, body: dict) -> dict:
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8'))


def run_wallet_price(tick: str) -> float | None:
    """Get price_usd for a tick via wallet.js price-token."""
    try:
        result = subprocess.run(
            ['node', WALLET_CLI, 'price-token', '--tick', tick],
            capture_output=True,
            text=True,
            timeout=WALLET_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        try:
            data = json.loads(output)
            price = data.get('tokenPriceUsd') or data.get('price_usd') or data.get('price')
            if price is not None:
                return float(price)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        try:
            return float(output)
        except ValueError:
            return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def fetch_prices_via_onchainos(tokens: list[tuple[str, str]]) -> dict[str, float]:
    """Batch fetch token USD prices via On-Chain OS market prices."""
    if not tokens:
        return {}
    token_arg = ','.join(f'56:{addr}' for _, addr in tokens if addr)
    if not token_arg:
        return {}
    try:
        result = subprocess.run(
            [ONCHAINOS_CLI, 'market', 'prices', '--tokens', token_arg],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout.strip())
        if not data.get('ok'):
            return {}
        by_addr = {
            str(item.get('tokenContractAddress') or '').lower(): float(item.get('price') or 0)
            for item in (data.get('data') or [])
            if item.get('tokenContractAddress')
        }
        out: dict[str, float] = {}
        for tick, addr in tokens:
            price = by_addr.get(addr.lower())
            if price is not None:
                out[tick] = price
        return out
    except Exception:
        return {}


def fetch_rewards(api_key: str) -> list[dict]:
    """Fetch claimable rewards list from API."""
    data = api_get(REWARDS_ENDPOINT, api_key)
    # API may return {data: [...]} or {rewards: [...]} or a list
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Handle nested {data: {rewards: [...]}} from TagClaw API
        nested = data.get('data')
        if isinstance(nested, dict):
            val = nested.get('rewards') or nested.get('claimable')
            if isinstance(val, list):
                return val
            if isinstance(nested, list):
                return nested
        for key in ('data', 'rewards', 'claimable', 'result'):
            val = data.get(key)
            if isinstance(val, list):
                return val
    return []


def do_claim(api_key: str, tick: str) -> dict:
    """POST claim for a tick. Returns response dict."""
    return api_post(CLAIM_ENDPOINT, api_key, {'tick': tick})


def upsert_execution_entry(entry: dict, executed_at: str) -> None:
    """Append/upsert entry into today's executions ledger."""
    try:
        dt = datetime.fromisoformat(executed_at)
        date_str = dt.strftime('%Y-%m-%d')
    except Exception:
        date_str = now_cst().strftime('%Y-%m-%d')

    path = RUNTIME_DIR / f'executions-{date_str}.json'
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            ledger = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            ledger = {}
    else:
        ledger = {}

    if not isinstance(ledger, dict):
        ledger = {}

    ledger.setdefault('version', 'v1')
    ledger.setdefault('date', date_str)
    ledger['updated_at'] = now_iso()
    items = ledger.get('items')
    if not isinstance(items, list):
        items = []
        ledger['items'] = items

    existing_ids = {str(item.get('id')) for item in items if isinstance(item, dict) and item.get('id')}
    entry_id = str(entry.get('id', ''))
    if entry_id and entry_id not in existing_ids:
        items.append(entry)
        ledger['items'] = items[-500:]
    elif not entry_id:
        items.append(entry)
        ledger['items'] = items[-500:]

    atomic_write_json(path, ledger)


def write_rewards_md(claimable: list[dict], checked_at: str, checked_at_iso: str) -> None:
    lines = [
        f'Task',
        f'- TagClaw 每日奖励检查 / 自动 claim 周期 ({checked_at})',
        f'What I checked',
        f'- 运行 fetch_and_claim_rewards.py (script-fast)；无 LLM 推理',
        f'- 调用 /tagclaw/agent/rewards 获取可 claim 列表',
        f'- 通过 wallet.js price-token 查询各 tick 价格',
        f'What I executed / did not execute',
    ]
    claimed = [r for r in claimable if r.get('status') == 'claimed']
    skipped = [r for r in claimable if r.get('status') == 'skipped']
    failed = [r for r in claimable if r.get('status') == 'failed']
    if claimed:
        lines.append(f'- 已 claim: {", ".join(r["tick"] for r in claimed)}')
    if skipped:
        lines.append(f'- 跳过 (低于阈值或价格不可用): {", ".join(r["tick"] for r in skipped)}')
    if failed:
        lines.append(f'- 失败: {", ".join(r["tick"] for r in failed)}')
    if not claimed and not failed:
        lines.append('- 本周期未执行 claim')
    lines.append('Balances / rewards relevant to the decision')
    for r in claimable:
        tick = r.get('tick', '?')
        amount = r.get('claimable_amount', 'null')
        price = r.get('price_usd', 'null')
        usd = r.get('reward_value_usd', 'null')
        action = r.get('action', '')
        lines.append(f'- {tick}: claimable amount={amount}, price_usd={price}, reward_value_usd={usd}, {action}')
    lines.append('Risk / blocker')
    for r in claimable:
        tick = r.get('tick', '?')
        blocker = r.get('blocker')
        failure_reason = r.get('failure_reason')
        if blocker:
            lines.append(f'- {tick}: {blocker}')
        elif failure_reason:
            lines.append(f'- {tick}: {failure_reason}')
    lines.append('Next recommended step')
    lines.append('- main 后续读取 memory/rewards-claim-latest.md 与当日 memory 记录即可')
    lines.append('')
    atomic_write_text(MEMORY_DIR / 'rewards-claim-latest.md', '\n'.join(lines))


def append_daily_note(claimable: list[dict]) -> None:
    today = now_cst().strftime('%Y-%m-%d')
    ts_short = now_cst().strftime('%H:%M')
    claimed = [r for r in claimable if r.get('status') == 'claimed']
    skipped = [r for r in claimable if r.get('status') == 'skipped']
    failed = [r for r in claimable if r.get('status') == 'failed']
    parts = []
    if claimed:
        parts.append(f'claimed={",".join(r["tick"] for r in claimed)}')
    if skipped:
        parts.append(f'skipped={len(skipped)}')
    if failed:
        parts.append(f'failed={len(failed)}')
    summary = '; '.join(parts) if parts else 'no claimable'
    note = f'## {today} {ts_short} CST — rewards-claim (script-fast)\n- {summary}\n'
    daily_path = MEMORY_DIR / f'{today}.md'
    with open(daily_path, 'a', encoding='utf-8') as f:
        f.write('\n' + note)


def call_publish() -> None:
    subprocess.run(
        [sys.executable, PUBLISH_SCRIPT],
        timeout=30,
    )


def main() -> int:
    checked_at_iso = now_iso()
    checked_at_label = now_label()

    try:
        creds = read_credentials()
        api_key = creds.get('api_key', '')
        if not api_key:
            raise ValueError('api_key not found in credentials.json')
    except Exception as e:
        summary = {'status': 'error', 'error': str(e), 'updated_at': checked_at_label}
        print(json.dumps(summary, ensure_ascii=False))
        return 1

    # Fetch rewards list
    raw_rewards: list[dict] = []
    try:
        raw_rewards = fetch_rewards(api_key)
    except Exception as e:
        summary = {'status': 'error', 'error': f'fetch_rewards: {e}', 'updated_at': checked_at_label}
        print(json.dumps(summary, ensure_ascii=False))
        return 1

    reward_tokens: list[tuple[str, str]] = []
    for reward in raw_rewards:
        if not isinstance(reward, dict):
            continue
        tick = reward.get('tick') or reward.get('token') or reward.get('symbol')
        token_addr = reward.get('token') if isinstance(reward.get('token'), str) and str(reward.get('token')).startswith('0x') else None
        if tick and token_addr:
            reward_tokens.append((str(tick), str(token_addr)))
    onchain_prices = fetch_prices_via_onchainos(reward_tokens)

    claimable: list[dict] = []
    claimed_count = 0
    skipped_count = 0
    failed_count = 0
    claimable_usd_total = 0.0

    run_id = f'script-fast-{now_cst().strftime("%Y%m%dT%H%M%S")}'
    exec_ts = checked_at_iso

    for reward in raw_rewards:
        if not isinstance(reward, dict):
            continue
        tick = reward.get('tick') or reward.get('token') or reward.get('symbol')
        if not tick:
            continue
        # Amount field: may be 'amount', 'claimable_amount', 'claimableAmount'
        amount = (
            reward.get('claimable_amount') or
            reward.get('amount') or
            reward.get('claimableAmount') or 0
        )
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            amount = 0.0

        # Get price: TagClaw native first, On-Chain OS fallback
        price_usd = run_wallet_price(tick)
        price_source = 'wallet.js price-token'
        if price_usd is None:
            price_usd = onchain_prices.get(str(tick))
            if price_usd is not None:
                price_source = 'onchainos market prices'
        if price_usd is None:
            entry = {
                'tick': tick,
                'claimable_amount': amount,
                'price_usd': None,
                'price_source': price_source,
                'reward_value_usd': None,
                'status': 'skipped',
                'blocker': 'price_unavailable',
                'order_id': None,
                'failure_reason': None,
                'claim_response': None,
                'final_status': None,
                'final_status_response': None,
                'action': 'skipped | blocker=price_unavailable',
            }
            claimable.append(entry)
            skipped_count += 1
            # Upsert execution entry
            upsert_execution_entry({
                'id': f'{run_id}:{exec_ts}:claim:{tick}:skipped:price_unavailable',
                'ts': exec_ts,
                'run_id': run_id,
                'source_agent': 'trader',
                'action': 'claim',
                'tick': tick,
                'amount': amount,
                'amount_unit': tick,
                'usd': None,
                'raw_amount': None,
                'tx_hash': None,
                'order_id': None,
                'status': 'skipped',
                'trigger_reason': 'price_unavailable',
                'balance_before': None,
                'balance_after': None,
                'remote_route': 'tagclaw-claim',
                'approve_hash': None,
                'expected_amount': None,
                'expected_receive': None,
                'remote': {'ok': False, 'response': {'blocker': 'price_unavailable'}},
            }, exec_ts)
            continue

        reward_value_usd = amount * price_usd
        claimable_usd_total += reward_value_usd

        if reward_value_usd <= CLAIM_THRESHOLD_USD:
            blocker = 'below_threshold_2_usd'
            entry = {
                'tick': tick,
                'claimable_amount': amount,
                'price_usd': price_usd,
                'price_source': price_source,
                'reward_value_usd': reward_value_usd,
                'status': 'skipped',
                'blocker': blocker,
                'order_id': None,
                'failure_reason': None,
                'claim_response': None,
                'final_status': None,
                'final_status_response': None,
                'action': f'skipped | blocker={blocker}',
            }
            claimable.append(entry)
            skipped_count += 1
            upsert_execution_entry({
                'id': f'{run_id}:{exec_ts}:claim:{tick}:skipped:{blocker}',
                'ts': exec_ts,
                'run_id': run_id,
                'source_agent': 'trader',
                'action': 'claim',
                'tick': tick,
                'amount': amount,
                'amount_unit': tick,
                'usd': reward_value_usd,
                'raw_amount': None,
                'tx_hash': None,
                'order_id': None,
                'status': 'skipped',
                'trigger_reason': blocker,
                'balance_before': None,
                'balance_after': None,
                'remote_route': 'tagclaw-claim',
                'approve_hash': None,
                'expected_amount': None,
                'expected_receive': None,
                'remote': {'ok': False, 'response': {'blocker': blocker}},
            }, exec_ts)
            continue

        # Attempt claim
        claim_response = None
        order_id = None
        claim_status = 'failed'
        failure_reason = None

        try:
            claim_response = do_claim(api_key, tick)
            if isinstance(claim_response, dict):
                data = claim_response.get('data') or claim_response
                order_id = data.get('orderId') or data.get('order_id')
                if claim_response.get('success') or order_id:
                    claim_status = 'claimed'
                else:
                    failure_reason = claim_response.get('error') or claim_response.get('message') or 'unknown_error'
            else:
                failure_reason = 'unexpected_response_format'
        except urllib.error.HTTPError as e:
            failure_reason = f'http_error_{e.code}'
        except Exception as e:
            failure_reason = str(e)

        if claim_status == 'claimed':
            action_str = f'claimed | orderId={order_id}' if order_id else 'claimed'
            claimed_count += 1
        else:
            action_str = f'failed | failure_reason={failure_reason}'
            failed_count += 1

        entry = {
            'tick': tick,
            'claimable_amount': amount,
            'price_usd': price_usd,
            'price_source': price_source,
            'reward_value_usd': reward_value_usd,
            'status': claim_status,
            'blocker': None,
            'order_id': order_id,
            'failure_reason': failure_reason,
            'claim_response': claim_response,
            'final_status': None,
            'final_status_response': None,
            'action': action_str,
        }
        claimable.append(entry)

        exec_entry_id = (
            f'order:{order_id}' if order_id
            else f'{run_id}:{exec_ts}:claim:{tick}:{claim_status}'
        )
        upsert_execution_entry({
            'id': exec_entry_id,
            'ts': exec_ts,
            'run_id': run_id,
            'source_agent': 'trader',
            'action': 'claim',
            'tick': tick,
            'amount': amount,
            'amount_unit': tick,
            'usd': reward_value_usd,
            'raw_amount': None,
            'tx_hash': None,
            'order_id': order_id,
            'status': 'ok' if claim_status == 'claimed' else claim_status,
            'trigger_reason': failure_reason,
            'balance_before': None,
            'balance_after': None,
            'remote_route': 'tagclaw-claim',
            'approve_hash': None,
            'expected_amount': None,
            'expected_receive': None,
            'remote': {
                'ok': claim_status == 'claimed',
                'response': claim_response,
            },
        }, exec_ts)

    # Write canonical JSON
    rewards_json = {
        'version': 'v1',
        'checked_at_iso': checked_at_iso,
        'checked_at': checked_at_label,
        'source_kind': 'script-fast',
        'claimable': claimable,
        'claimable_usd_total': round(claimable_usd_total, 8) if claimable else None,
        'claim_recommended': any(r.get('status') == 'claimed' for r in claimable) or any(
            (r.get('reward_value_usd') or 0) > CLAIM_THRESHOLD_USD for r in claimable
        ),
    }
    try:
        atomic_write_json(MEMORY_DIR / 'rewards-claim-latest.json', rewards_json)
    except Exception as e:
        summary = {'status': 'error', 'error': f'write_json: {e}', 'updated_at': checked_at_label}
        print(json.dumps(summary, ensure_ascii=False))
        return 1

    try:
        write_rewards_md(claimable, checked_at_label, checked_at_iso)
    except Exception:
        pass  # non-fatal

    try:
        append_daily_note(claimable)
    except Exception:
        pass  # non-fatal

    try:
        call_publish()
    except Exception:
        pass  # non-fatal

    status = 'ok' if not failed_count else 'partial'
    summary = {
        'status': status,
        'checked_ticks': len(claimable),
        'claimed': claimed_count,
        'skipped': skipped_count,
        'failed': failed_count,
        'claimable_usd_total': round(claimable_usd_total, 8),
        'updated_at': checked_at_label,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
