#!/usr/bin/env python3
"""Publish trader-native shadow outputs for de-bridging phase 2."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS, TRADER_WS

TRADER_ROOT = (TRADER_WS)
MAIN_ROOT = (MAIN_WS)
RUNTIME = MAIN_ROOT / 'runtime'
SHADOW = RUNTIME / 'trader-shadow'

WALLET_RE = re.compile(r"^-\s+Wallet(?: address)?:\s+`?([^`\n]+)`?\s*$", re.MULTILINE)
REWARD_LINE_RE = re.compile(
    r"^\s*-\s+(?P<tick>[A-Za-z0-9_]+):\s+claimable\s+`(?P<amount>[^`]+)`\s+\|\s+price_usd\s+`(?P<price>[^`]+)`\s+\|\s+reward_value_usd\s+`(?P<usd>[^`]+)`\s+\|\s+(?P<action>[^\n]+)$",
    re.MULTILINE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding='utf-8')


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, path)


def normalize_status(value: str | None, default: str = 'stale') -> str:
    if value in {'ok', 'partial', 'blocked', 'stale'}:
        return value
    if value in {'error', 'failed', 'fail'}:
        return 'blocked'
    return default


def parse_markdown_balances(text: str | None) -> tuple[str | None, dict[str, str], dict[str, str]]:
    wallet = None
    balances: dict[str, str] = {}
    rewards: dict[str, str] = {}
    if not text:
        return wallet, balances, rewards
    wallet_match = WALLET_RE.search(text)
    if wallet_match:
        wallet = wallet_match.group(1)

    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('## Balances'):
            section = 'balances'
            continue
        if stripped.startswith('## Claimable rewards snapshot'):
            section = 'rewards'
            continue
        if stripped.startswith('## '):
            section = None
            continue
        m = re.match(r"^-\s+([A-Za-z0-9_]+):\s+`?([^`]+?)`?\s*$", stripped)
        if not m or not section:
            continue
        key, val = m.group(1), m.group(2)
        if section == 'balances':
            balances[key] = val
        elif section == 'rewards':
            rewards[key] = val
    return wallet, balances, rewards


def main() -> int:
    memory = TRADER_ROOT / 'memory'
    wallet_md = read_text(memory / 'wallet-balance-latest.md')
    rewards_md = read_text(memory / 'rewards-claim-latest.md')
    tas_trade = read_json(memory / 'tas-trade-latest.json') or {}
    execution_record = read_json(RUNTIME / 'trader' / 'execution-record.json') or {}

    wallet_address, balances, _ = parse_markdown_balances(wallet_md)
    reward_items: list[dict[str, Any]] = []
    claimable_total = 0.0
    for m in REWARD_LINE_RE.finditer(rewards_md or ''):
        tick = m.group('tick')
        amount = m.group('amount')
        price = m.group('price')
        usd = m.group('usd')
        action = m.group('action').strip()
        try:
            usd_num = float(usd)
            claimable_total += usd_num
        except ValueError:
            usd_num = None
        reward_items.append({
            'tick': tick,
            'claimable_amount': amount,
            'price_usd': price,
            'reward_value_usd': usd_num,
            'action': action,
        })

    generated_at = tas_trade.get('computed_at') or now_iso()
    trader_status = normalize_status(tas_trade.get('status'), default='stale')
    risk_flags = ['tas_trade_partial'] if trader_status == 'partial' else []
    for blocker in tas_trade.get('blockers') or []:
        risk_flags.append(str(blocker))
    risk_flags = risk_flags[:10]

    wallet_snapshot = {
        'version': 'v1',
        'updated_at': generated_at,
        'status': 'ok' if balances else 'blocked',
        'source_class': 'native-trader-shadow',
        'wallet_address': wallet_address,
        'balances': balances,
        'notes': 'native trader shadow output from workspace-trader memory/wallet-balance-latest.md',
    }
    reward_status = {
        'version': 'v1',
        'updated_at': generated_at,
        'status': trader_status,
        'source_class': 'native-trader-shadow',
        'claimable': reward_items,
        'claimable_usd_total': round(claimable_total, 8) if reward_items else None,
        'notes': 'native trader shadow output from workspace-trader memory/rewards-claim-latest.md',
    }
    tas_trade_out = {
        'version': 'v1',
        'updated_at': generated_at,
        'status': normalize_status(tas_trade.get('status'), default='stale'),
        'source_class': 'native-trader-shadow',
        'value': tas_trade.get('value'),
        'summary': tas_trade.get('summary'),
        'notes': 'native trader shadow output from workspace-trader memory/tas-trade-latest.json',
    }
    risk_status = {
        'version': 'v1',
        'updated_at': generated_at,
        'status': normalize_status(tas_trade.get('status'), default='stale'),
        'source_class': 'native-trader-shadow',
        'risk_flags': risk_flags,
        'notes': 'native trader shadow output from trader TAS_trade blockers and warnings',
    }
    latest = {
        'version': 'v1',
        'agent': 'trader-shadow',
        'status': trader_status,
        'generated_at': generated_at,
        'outputs': {
            'wallet_snapshot_ref': 'runtime/trader-shadow/wallet-snapshot.json',
            'reward_status_ref': 'runtime/trader-shadow/reward-status.json',
            'tas_trade_ref': 'runtime/trader-shadow/tas-trade.json',
            'risk_status_ref': 'runtime/trader-shadow/risk-status.json',
            'execution_ref': 'runtime/trader/execution-record.json',
            'last_execution_status': execution_record.get('status'),
        },
        'notes': 'native trader shadow outputs for de-bridging phase 2',
    }

    atomic_write_json(SHADOW / 'wallet-snapshot.json', wallet_snapshot)
    atomic_write_json(SHADOW / 'reward-status.json', reward_status)
    atomic_write_json(SHADOW / 'tas-trade.json', tas_trade_out)
    atomic_write_json(SHADOW / 'risk-status.json', risk_status)
    atomic_write_json(SHADOW / 'latest.json', latest)

    print(json.dumps({'status': 'ok', 'path': str(SHADOW), 'trader_status': trader_status}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
