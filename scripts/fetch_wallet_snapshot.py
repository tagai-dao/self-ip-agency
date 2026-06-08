#!/usr/bin/env python3
"""Fast wallet balance snapshot — no LLM, runs in <15s.

Reads BNB + TagClaw/BUIDL/TTAI balances, writes memory/wallet-balance-latest.md,
appends to YYYY-MM-DD.md, then calls publish_trader_runtime_v2.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from agency_paths import TRADER_WS

TRADER_ROOT = (TRADER_WS)
SCRIPTS_DIR = TRADER_ROOT / 'scripts'
MEMORY_DIR = TRADER_ROOT / 'memory'
CREDENTIALS_PATH = Path.home() / '.config' / 'tagclaw' / 'credentials.json'
WALLET_CLI = str(Path.home() / 'tagclaw-wallet' / 'bin' / 'wallet.js')
ONCHAINOS_CLI = str(Path.home() / '.local' / 'bin' / 'onchainos')
PUBLISH_SCRIPT = str(SCRIPTS_DIR / 'publish_trader_runtime_v2.py')

TOKEN_ADDRESSES = {
    'TagClaw': '0xe7324F2987aCd88Ee7286EB9DAb0EE926ad36a68',
    'BUIDL': '0x32ef878D527d860339818571E8DA17005110f04E',
    'TTAI': '0x8e11E90B463bf521382E2B88539F053270a3848c',
}
TOKEN_ORDER = ['BNB', 'TagClaw', 'BUIDL', 'TTAI']

SUBPROCESS_TIMEOUT = 10


def now_cst() -> datetime:
    cst = datetime.now().astimezone()
    return cst


def now_label() -> str:
    return now_cst().strftime('%Y-%m-%d %H:%M') + ' Asia/Shanghai'


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8', suffix='.tmp') as tmp:
        tmp.write(content)
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def read_credentials() -> dict:
    text = CREDENTIALS_PATH.read_text(encoding='utf-8')
    return json.loads(text)


def run_wallet_cmd(args: list[str]) -> str | None:
    """Run a wallet.js command, return stdout on success or None on failure."""
    cmd = ['node', WALLET_CLI] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def parse_bnb_output(output: str | None) -> str | None:
    """Parse wallet.js balance-bnb JSON output: {"wei":"...","ether":"..."}"""
    if not output:
        return None
    try:
        data = json.loads(output)
        return str(data.get('ether') or data.get('wei') or '')
    except (json.JSONDecodeError, KeyError):
        pass
    # Fallback: bare number
    stripped = output.strip()
    try:
        float(stripped)
        return stripped
    except ValueError:
        return None


def parse_erc20_output(output: str | None) -> str | None:
    """Parse wallet.js balance-erc20 JSON output: {"raw":"...","formatted":"...","symbol":"..."}"""
    if not output:
        return None
    try:
        data = json.loads(output)
        return str(data.get('formatted') or data.get('raw') or '')
    except (json.JSONDecodeError, KeyError):
        pass
    stripped = output.strip()
    try:
        float(stripped)
        return stripped
    except ValueError:
        return None


def fetch_bnb_balance(address: str) -> str | None:
    output = run_wallet_cmd(['balance-bnb', '--address', address])
    return parse_bnb_output(output)


def fetch_erc20_balance(address: str, token_addr: str) -> str | None:
    output = run_wallet_cmd(['balance-erc20', '--address', address, '--token', token_addr])
    return parse_erc20_output(output)


def fetch_balances_via_onchainos(address: str) -> dict[str, str | None]:
    """Fetch BNB + configured token balances via On-Chain OS token-balances."""
    token_args = ['56:'] + [f"56:{addr}" for addr in TOKEN_ADDRESSES.values()]
    cmd = [
        ONCHAINOS_CLI,
        'portfolio', 'token-balances',
        '--chain', 'bsc',
        '--address', address,
        '--tokens', ','.join(token_args),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout.strip())
        if not data.get('ok'):
            return {}
        assets = (((data.get('data') or [{}])[0] or {}).get('tokenAssets') or [])
        out: dict[str, str | None] = {tick: None for tick in TOKEN_ORDER}
        for asset in assets:
            symbol = str(asset.get('symbol') or '').strip()
            balance = asset.get('balance')
            if symbol in out and balance is not None:
                out[symbol] = str(balance)
        return out
    except Exception:
        return {}


def write_wallet_balance_md(address: str, bnb: str | None, balances: dict[str, str | None]) -> None:
    ts = now_label()
    lines = [
        '# Wallet Balance Latest',
        '',
        f'- Timestamp: {ts}',
        '- Task: wallet balance check (script-fast)',
        '',
        '## Wallet',
        f'- Wallet address: `{address}`',
        '',
        '## Balances',
        f'- BNB: {bnb if bnb is not None else "null"}',
    ]
    for token, val in balances.items():
        lines.append(f'- {token}: {val if val is not None else "null"}')
    lines += [
        '',
        '## Status',
        '- 此文件由 fetch_wallet_snapshot.py (script-fast) 更新',
        '',
    ]
    atomic_write(MEMORY_DIR / 'wallet-balance-latest.md', '\n'.join(lines))


def append_daily_note(address: str, bnb: str | None, balances: dict[str, str | None]) -> None:
    today = now_cst().strftime('%Y-%m-%d')
    ts_short = now_cst().strftime('%H:%M')
    bal_parts = [f'BNB `{bnb or "null"}`']
    for token, val in balances.items():
        bal_parts.append(f'{token} `{val or "null"}`')
    note = f'## {today} {ts_short} CST — wallet-snapshot (script-fast)\n- Balances: {" | ".join(bal_parts)}\n'
    daily_path = MEMORY_DIR / f'{today}.md'
    with open(daily_path, 'a', encoding='utf-8') as f:
        f.write('\n' + note)


def call_publish() -> None:
    subprocess.run(
        [sys.executable, PUBLISH_SCRIPT],
        timeout=30,
    )


def main() -> int:
    try:
        creds = read_credentials()
        address = creds.get('address', '')
        if not address:
            raise ValueError('address not found in credentials.json')
    except Exception as e:
        summary = {'status': 'error', 'error': str(e), 'updated_at': now_label()}
        print(json.dumps(summary, ensure_ascii=False))
        return 1

    onchain_balances = fetch_balances_via_onchainos(address)
    bnb = onchain_balances.get('BNB') if onchain_balances else fetch_bnb_balance(address)
    token_balances: dict[str, str | None] = {}
    for token_name, token_addr in TOKEN_ADDRESSES.items():
        if onchain_balances.get(token_name) is not None:
            token_balances[token_name] = onchain_balances.get(token_name)
        else:
            token_balances[token_name] = fetch_erc20_balance(address, token_addr)

    try:
        write_wallet_balance_md(address, bnb, token_balances)
    except Exception as e:
        summary = {'status': 'error', 'error': f'write_wallet_balance_md: {e}', 'updated_at': now_label()}
        print(json.dumps(summary, ensure_ascii=False))
        return 1

    try:
        append_daily_note(address, bnb, token_balances)
    except Exception:
        pass  # non-fatal

    try:
        call_publish()
    except Exception:
        pass  # non-fatal

    status = 'ok' if all(v is not None for v in [bnb, *token_balances.values()]) else 'partial'
    summary = {
        'status': status,
        'bnb': bnb,
        'tagclaw': token_balances.get('TagClaw'),
        'buidl': token_balances.get('BUIDL'),
        'ttai': token_balances.get('TTAI'),
        'updated_at': now_label(),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
