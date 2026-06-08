#!/usr/bin/env python3
"""On-chain position monitor for Trader agent.

Uses onchainos CLI to fetch real-time token prices (BSC) and computes:
- Current USD value of each holding
- Price change vs last snapshot (trend)
- Position concentration risk
- Holding trend score for TAS_trade

Output: runtime/trader/onchain-positions.json

Token CA (BSC):
  TagClaw : 0xe7324f2987acd88ee7286eb9dab0ee926ad36a68
  BUIDL   : 0x32ef878D527d860339818571E8DA17005110f04E
  TTAI    : 0x8e11E90B463bf521382E2B88539F053270a3848c

Usage:
  python3 scripts/monitor_onchain_positions.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS, TRADER_WS

TRADER_ROOT = (TRADER_WS)
MAIN_ROOT = (MAIN_WS)
RUNTIME = MAIN_ROOT / 'runtime' / 'trader'
CREDENTIALS = Path.home() / '.config' / 'tagclaw' / 'credentials.json'
WALLET_CLI = str(Path.home() / 'tagclaw-wallet' / 'bin' / 'wallet.js')
BSC_RPC_URL = os.environ.get('TAGCLAW_BNB_RPC') or 'https://bsc-rpc.publicnode.com'

DRY_RUN = '--dry-run' in sys.argv

# BSC token config
TOKENS = {
    'BNB': {
        'address': '',
        'chain': '56',
    },
    'TagClaw': {
        'address': '0xe7324f2987acd88ee7286eb9dab0ee926ad36a68',
        'chain': '56',
    },
    'BUIDL': {
        'address': '0x32ef878D527d860339818571E8DA17005110f04E',
        'chain': '56',
    },
    'TTAI': {
        'address': '0x8e11E90B463bf521382E2B88539F053270a3848c',
        'chain': '56',
    },
}

# Price change thresholds for trend classification
TREND_UP_PCT = 2.0     # +2% → bullish
TREND_DOWN_PCT = -2.0  # -2% → bearish


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


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
    os.replace(temp_name, str(path))


def fetch_balances(address: str) -> dict[str, dict[str, Any]]:
    """Fetch current balances and embedded token prices via onchainos CLI."""
    token_str = ','.join(
        f"{cfg['chain']}:{cfg['address']}"
        for cfg in TOKENS.values()
    )
    try:
        proc = subprocess.run(
            ['onchainos', 'portfolio', 'token-balances', '--chain', 'bsc', '--address', address, '--tokens', token_str],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return {}
        data = json.loads(proc.stdout.strip())
        if not data.get('ok'):
            return {}
        assets = (((data.get('data') or [{}])[0] or {}).get('tokenAssets') or [])
        out: dict[str, dict[str, Any]] = {}
        for asset in assets:
            symbol = str(asset.get('symbol') or '').strip()
            if not symbol:
                continue
            out[symbol] = asset
        return out
    except Exception:
        return {}


def fetch_bnb_price_coingecko() -> float | None:
    """Fallback for native BNB price when onchainos/wallet pricing is unavailable."""
    url = 'https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd'
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        price = (((data or {}).get('binancecoin') or {}).get('usd'))
        if price is None:
            return None
        return float(price)
    except Exception:
        return None


def fetch_prices() -> tuple[dict[str, float], list[str]]:
    """Fetch current prices for all tokens via onchainos CLI, then wallet.js / HTTP fallback."""
    token_str = ','.join(
        f"{cfg['chain']}:{cfg['address']}"
        for cfg in TOKENS.values()
        if cfg['address']
    )
    price_sources: list[str] = []
    prices: dict[str, float] = {}
    inferred_bnb_price: float | None = None
    try:
        proc = subprocess.run(
            ['onchainos', 'market', 'prices', '--tokens', token_str],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout.strip())
            if data.get('ok'):
                # Map address → price
                addr_price: dict[str, float] = {}
                for item in data.get('data', []):
                    addr = item.get('tokenContractAddress', '').lower()
                    price = float(item.get('price') or 0)
                    addr_price[addr] = price

                # Map tick name → price
                for tick, cfg in TOKENS.items():
                    addr = cfg['address'].lower()
                    if addr and addr in addr_price:
                        prices[tick] = addr_price[addr]
                if prices:
                    price_sources.append('onchainos-bsc')
    except Exception:
        pass

    for tick in TOKENS.keys():
        if tick in prices or tick == 'BNB':
            continue
        try:
            proc = subprocess.run(
                ['node', WALLET_CLI, 'price-token', '--tick', tick, '--rpc-url', BSC_RPC_URL],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                continue
            data = json.loads(proc.stdout.strip())
            price = data.get('tokenPriceUsd') or data.get('price_usd') or data.get('price')
            if price is not None:
                prices[tick] = float(price)
                if 'wallet.js-price-fallback' not in price_sources:
                    price_sources.append('wallet.js-price-fallback')
            bnb_price_usd = data.get('bnbPriceUsd') or data.get('bnb_price_usd')
            if bnb_price_usd is not None and inferred_bnb_price is None:
                inferred_bnb_price = float(bnb_price_usd)
        except Exception:
            continue

    if 'BNB' not in prices and inferred_bnb_price is not None:
        prices['BNB'] = inferred_bnb_price
        price_sources.append('wallet.js-bnb-inferred')

    if 'BNB' not in prices:
        bnb_price = fetch_bnb_price_coingecko()
        if bnb_price is not None:
            prices['BNB'] = bnb_price
            price_sources.append('coingecko-bnb-fallback')

    return prices, price_sources


def classify_price_trend(current: float, previous: float) -> str:
    if previous <= 0:
        return 'unknown'
    pct = (current - previous) / previous * 100
    if pct >= TREND_UP_PCT:
        return 'bullish'
    if pct <= TREND_DOWN_PCT:
        return 'bearish'
    return 'neutral'


def compute_holding_trend_score(positions: list[dict]) -> tuple[str, float]:
    """Compute overall holding trend score from position data."""
    trends = [p.get('price_trend') for p in positions]
    bullish = trends.count('bullish')
    bearish = trends.count('bearish')
    neutral = trends.count('neutral')

    if bullish > bearish:
        return 'growing', 0.3
    elif bearish > bullish:
        return 'declining', 0.0
    else:
        return 'stable', 0.1


def main() -> int:
    scanned_at = now_iso()

    wallet_snapshot = read_json(RUNTIME / 'wallet-snapshot.json') or {}
    wallet_address = wallet_snapshot.get('wallet_address') or ''
    if not wallet_address and CREDENTIALS.exists():
        try:
            wallet_address = json.loads(CREDENTIALS.read_text(encoding='utf-8')).get('address') or ''
        except Exception:
            wallet_address = ''

    current_assets = fetch_balances(wallet_address) if wallet_address else {}
    balance_source = 'onchainos-bsc'
    if not current_assets and wallet_address:
        fallback_assets: dict[str, dict[str, Any]] = {}
        for tick, cfg in TOKENS.items():
            try:
                if tick == 'BNB':
                    proc = subprocess.run(
                        ['node', WALLET_CLI, 'balance-bnb', '--address', wallet_address, '--rpc-url', BSC_RPC_URL],
                        capture_output=True, text=True, timeout=30,
                    )
                    if proc.returncode != 0:
                        continue
                    data = json.loads(proc.stdout.strip())
                    fallback_assets[tick] = {
                        'symbol': tick,
                        'balance': data.get('ether') or data.get('wei'),
                        'rawBalance': data.get('wei'),
                    }
                else:
                    proc = subprocess.run(
                        ['node', WALLET_CLI, 'balance-erc20', '--address', wallet_address, '--token', cfg['address'], '--rpc-url', BSC_RPC_URL],
                        capture_output=True, text=True, timeout=30,
                    )
                    if proc.returncode != 0:
                        continue
                    data = json.loads(proc.stdout.strip())
                    fallback_assets[tick] = {
                        'symbol': tick,
                        'balance': data.get('formatted') or data.get('raw'),
                        'rawBalance': data.get('raw'),
                    }
            except Exception:
                continue
        if fallback_assets:
            current_assets = fallback_assets
            balance_source = 'wallet.js-rpc-fallback'
    if not current_assets:
        result = {
            'version': 'v1',
            'status': 'blocked',
            'error': 'onchainos balance fetch failed and wallet.js fallback returned no balances',
            'scanned_at': scanned_at,
            'source_class': 'trader-native',
        }
        if not DRY_RUN:
            atomic_write_json(RUNTIME / 'onchain-positions.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    # Load previous onchain positions for price trend comparison
    prev_positions = read_json(RUNTIME / 'onchain-positions.json') or {}
    prev_prices = {
        p['tick']: p.get('price_usd', 0)
        for p in prev_positions.get('positions', [])
    }

    # Fetch current prices (fallback only; token-balances may embed tokenPrice)
    current_prices, price_sources = fetch_prices()

    # Build position records
    positions = []
    total_usd = 0.0

    for tick, cfg in TOKENS.items():
        asset = current_assets.get(tick) or {}
        balance = float(asset.get('balance') or 0)
        price = float(asset.get('tokenPrice') or current_prices.get(tick, 0.0) or 0.0)
        value_usd = balance * price
        prev_price = prev_prices.get(tick, 0.0)
        price_change_pct = ((price - prev_price) / prev_price * 100) if prev_price > 0 else None
        trend = classify_price_trend(price, prev_price) if prev_price > 0 else 'unknown'

        total_usd += value_usd
        positions.append({
            'tick': tick,
            'address': cfg['address'],
            'chain': 'BSC',
            'balance': balance,
            'price_usd': price,
            'value_usd': round(value_usd, 4),
            'raw_balance': asset.get('rawBalance'),
            'price_change_pct': round(price_change_pct, 2) if price_change_pct is not None else None,
            'price_trend': trend,
        })

    # Concentration risk: largest single position / total
    max_value = max((p['value_usd'] for p in positions), default=0)
    concentration = round(max_value / total_usd, 4) if total_usd > 0 else 0.0
    concentration_risk = 'high' if concentration > 0.7 else ('medium' if concentration > 0.5 else 'low')

    # Overall holding trend
    holding_trend, holding_trend_score = compute_holding_trend_score(positions)

    result = {
        'version': 'v1',
        'status': 'ok',
        'source_class': 'trader-native',
        'scanned_at': scanned_at,
        'wallet_address': wallet_address,
        'total_portfolio_usd': round(total_usd, 4),
        'positions': positions,
        'holding_trend': holding_trend,
        'holding_trend_score': holding_trend_score,
        'concentration': {
            'value': concentration,
            'risk': concentration_risk,
            'note': f'largest position = {round(concentration*100, 1)}% of portfolio',
        },
        'price_source': '+'.join(price_sources) if price_sources else 'unavailable',
        'balance_source': balance_source,
        'tokens_monitored': list(TOKENS.keys()),
        'notes': 'On-chain position monitor via onchainos CLI (BSC)',
    }

    if not DRY_RUN:
        atomic_write_json(RUNTIME / 'onchain-positions.json', result)

    print(json.dumps({
        'status': 'ok',
        'total_usd': result['total_portfolio_usd'],
        'holding_trend': holding_trend,
        'holding_trend_score': holding_trend_score,
        'concentration_risk': concentration_risk,
        'positions': [
            {'tick': p['tick'], 'value_usd': p['value_usd'], 'trend': p['price_trend']}
            for p in positions
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
