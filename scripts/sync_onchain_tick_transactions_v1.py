#!/usr/bin/env python3
"""sync_onchain_tick_transactions_v1.py — Trader-owned raw tx collector for trending ticks.

Contract-native resolution pipeline:
  1. Resolve tick → contract address
     Primary: OnChainOS token search by symbol on BSC
     Fallback: TagAI /community/detail?tick=<tick> → token field
  2. Validate contract → OnChainOS token info (confirm name/symbol)
  3. Fetch trade history → OnChainOS token trades (real DEX data)
  4. Persist raw → raw/onchain-token-transation/<tick>/

Source precedence:
  OnChainOS DEX trades > TagAI API (/community/tradeList is unreliable, returns [])

Sync modes:
  - First run (no state file): fetch up to 500 trades per tick.
  - Subsequent runs: incremental sync (100 trades, dedup against prior).

Usage:
  python3 scripts/sync_onchain_tick_transactions_v1.py            # normal sync
  python3 scripts/sync_onchain_tick_transactions_v1.py --dry-run  # print plan, no writes
  python3 scripts/sync_onchain_tick_transactions_v1.py --force-backfill  # force max fetch
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS, TRADER_WS

# ── paths ────────────────────────────────────────────────────
MAIN_ROOT = (MAIN_WS)
TRADER_ROOT = (TRADER_WS)
RAW_ROOT = MAIN_ROOT / 'raw' / 'onchain-token-transation'
STATE_DIR = TRADER_ROOT / 'state' / 'onchain-tick-sync'
CREDS_PATH = Path(os.path.expanduser('~/.config/tagclaw/credentials.json'))
CONTRACT_CACHE_PATH = MAIN_ROOT / 'raw' / 'onchain-token-transation' / 'contract-map.json'

# Wiki trending ticks source
TICKS_TRENDING_PATH = MAIN_ROOT / 'wiki' / 'tagclaw-platform' / 'raw' / 'ticks_trending.json'

TAGAI_BASE_URL = 'https://bsc-api.tagai.fun'
ONCHAINOS_BIN = 'onchainos'

# Trade limits
BACKFILL_LIMIT = 500   # max trades on first sync
INCREMENTAL_LIMIT = 100  # trades per incremental sync

DRY_RUN = '--dry-run' in sys.argv
FORCE_BACKFILL = '--force-backfill' in sys.argv

# ── helpers ──────────────────────────────────────────────────

def atomic_write(path: Path, data: Any) -> None:
    """Atomic JSON write: tmp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, suffix='.tmp',
                                     delete=False, encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, path)


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def load_trending_ticks(n: int = 15) -> list[str]:
    """Load trending ticks from wiki snapshot."""
    data = load_json(TICKS_TRENDING_PATH)
    ticks = data.get('data', {}).get('ticks', [])
    return [t['tick'] for t in ticks[:n] if isinstance(t, dict) and t.get('tick')]


def get_api_headers() -> dict[str, str]:
    """Load API key from credentials for TagAI fallback."""
    creds = json.loads(CREDS_PATH.read_text(encoding='utf-8'))
    api_key = creds.get('api_key') or creds.get('apiKey') or creds.get('token')
    if not api_key:
        print('WARN: api_key not found in credentials (TagAI fallback disabled)', file=sys.stderr)
        return {}
    return {'Authorization': f'Bearer {api_key}'}


def load_tick_state(tick: str) -> dict:
    """Load per-tick sync state (watermark, last_sync_at, etc.)."""
    path = STATE_DIR / f'{tick}.json'
    return load_json(path)


def save_tick_state(tick: str, state: dict) -> None:
    """Save per-tick sync state."""
    if DRY_RUN:
        return
    path = STATE_DIR / f'{tick}.json'
    atomic_write(path, state)


def load_contract_cache() -> dict[str, dict]:
    """Load cached tick→contract mappings."""
    data = load_json(CONTRACT_CACHE_PATH)
    return data if isinstance(data, dict) else {}


def save_contract_cache(cache: dict[str, dict]) -> None:
    """Save tick→contract cache."""
    if DRY_RUN:
        return
    atomic_write(CONTRACT_CACHE_PATH, cache)


# ── contract resolution ─────────────────────────────────────

def run_onchainos(args: list[str]) -> dict | None:
    """Run onchainos CLI and parse JSON output."""
    cmd = [ONCHAINOS_BIN] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f'  onchainos ERR: {r.stderr.strip()[:200]}', file=sys.stderr)
            return None
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        print(f'  onchainos TIMEOUT: {" ".join(cmd)}', file=sys.stderr)
        return None
    except json.JSONDecodeError:
        print(f'  onchainos bad JSON: {r.stdout[:200]}', file=sys.stderr)
        return None
    except Exception as e:
        print(f'  onchainos ERR: {e}', file=sys.stderr)
        return None


def resolve_contract_onchainos(tick: str) -> dict | None:
    """Primary: resolve tick → contract via OnChainOS token search on BSC.

    Returns {'address': '0x...', 'name': str, 'symbol': str, 'source': 'onchainos_search'}
    or None if not found / ambiguous.
    """
    result = run_onchainos(['token', 'search', '--query', tick, '--chains', 'bsc'])
    if not result or not result.get('ok'):
        return None

    hits = result.get('data', [])
    if not hits:
        return None

    # Find exact or close match by symbol/name (case-insensitive)
    tick_lower = tick.lower()
    best: dict | None = None
    for h in hits:
        sym = (h.get('tokenSymbol') or '').lower()
        name = (h.get('tokenName') or '').lower()
        addr = h.get('tokenContractAddress', '')
        if not addr:
            continue
        # Exact symbol match is best
        if sym == tick_lower:
            best = h
            break
        # Exact name match is good
        if name == tick_lower and best is None:
            best = h

    if not best:
        return None

    return {
        'address': best['tokenContractAddress'],
        'name': best.get('tokenName', ''),
        'symbol': best.get('tokenSymbol', ''),
        'source': 'onchainos_search',
    }


def resolve_contract_tagai(tick: str, headers: dict) -> dict | None:
    """Fallback: resolve tick → contract via TagAI /community/detail API.

    Returns {'address': '0x...', 'name': str, 'symbol': str, 'source': 'tagai_community_detail'}
    or None if not found.
    """
    if not headers:
        return None

    import requests
    url = f'{TAGAI_BASE_URL}/community/detail'
    try:
        r = requests.get(url, params={'tick': tick}, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f'  tagai fallback ERR for {tick}: {e}', file=sys.stderr)
        return None

    if not isinstance(data, dict):
        return None

    token_addr = data.get('token', '')
    if not token_addr or not token_addr.startswith('0x') or len(token_addr) != 42:
        return None

    return {
        'address': token_addr,
        'name': data.get('name', tick),
        'symbol': tick,
        'source': 'tagai_community_detail',
    }


def validate_contract(address: str, tick: str) -> dict | None:
    """Validate contract via OnChainOS token info. Returns token info or None."""
    result = run_onchainos(['token', 'info', '--address', address, '--chain', 'bsc'])
    if not result or not result.get('ok'):
        return None

    tokens = result.get('data', [])
    if not tokens:
        return None

    info = tokens[0]
    return {
        'address': info.get('tokenContractAddress', address),
        'name': info.get('tokenName', ''),
        'symbol': info.get('tokenSymbol', ''),
        'decimals': info.get('decimal', '18'),
        'chain': 'bsc',
        'chain_index': info.get('chainIndex', '56'),
    }


def resolve_tick_contract(tick: str, headers: dict, cache: dict) -> dict | None:
    """Full resolution pipeline: cache → tagai (authoritative) → onchainos search → validate.

    Source precedence for contract resolution:
      1. Cache (previously validated)
      2. TagAI /community/detail (authoritative for TagAI ecosystem ticks)
      3. OnChainOS token search (fallback for ticks not in TagAI)

    TagAI is primary because OnChainOS search returns generic BSC tokens —
    common symbols like CLAW/AGENT/BULL can match wrong tokens.
    TagAI /community/detail returns the exact contract deployed for that tick.

    Returns validated contract info dict or None.
    """
    # Check cache first
    cached = cache.get(tick)
    if cached and cached.get('address') and cached.get('validated'):
        print(f'  [{tick}] contract from cache: {cached["address"][:12]}...{cached["address"][-6:]}')
        return cached

    # Primary: TagAI community/detail (authoritative for TagAI ecosystem ticks)
    resolved = resolve_contract_tagai(tick, headers)
    resolution_source = 'tagai_community_detail'

    # Fallback: OnChainOS search (for ticks not registered in TagAI)
    if not resolved:
        resolved = resolve_contract_onchainos(tick)
        resolution_source = 'onchainos_search'

    if not resolved:
        print(f'  [{tick}] contract resolution FAILED (both sources)')
        return None

    print(f'  [{tick}] resolved via {resolution_source}: {resolved["address"][:12]}...{resolved["address"][-6:]}')

    # Validate on OnChainOS
    validated = validate_contract(resolved['address'], tick)
    if not validated:
        print(f'  [{tick}] contract validation FAILED on OnChainOS')
        return None

    # Sanity check: does the validated token match the tick?
    v_sym = (validated.get('symbol') or '').lower()
    v_name = (validated.get('name') or '').lower()
    tick_lower = tick.lower()
    if v_sym != tick_lower and v_name != tick_lower:
        print(f'  [{tick}] WARN: validated token name/symbol mismatch: '
              f'symbol={validated["symbol"]}, name={validated["name"]} — proceeding with caution')

    entry = {
        'address': validated['address'],
        'name': validated['name'],
        'symbol': validated['symbol'],
        'decimals': validated.get('decimals', '18'),
        'chain': 'bsc',
        'resolution_source': resolution_source,
        'validated': True,
        'validated_at': datetime.now(timezone.utc).isoformat(),
    }

    # Update cache
    cache[tick] = entry
    return entry


# ── trade fetchers (OnChainOS) ──────────────────────────────

def fetch_trades_onchainos(address: str, limit: int = 100) -> list[dict]:
    """Fetch DEX trade history via OnChainOS token trades."""
    result = run_onchainos([
        'token', 'trades',
        '--address', address,
        '--chain', 'bsc',
        '--limit', str(min(limit, 500)),
    ])
    if not result or not result.get('ok'):
        return []

    return result.get('data', [])


# ── core sync logic ──────────────────────────────────────────

def sync_tick(headers: dict, tick: str, contract: dict, now: datetime) -> dict:
    """Sync one tick: fetch trades via OnChainOS, append to raw, update state."""
    state = load_tick_state(tick)
    is_backfill = FORCE_BACKFILL or not state.get('backfill_completed')
    mode = 'backfill' if is_backfill else 'incremental'

    limit = BACKFILL_LIMIT if is_backfill else INCREMENTAL_LIMIT

    result = {
        'tick': tick,
        'contract': contract['address'],
        'resolution_source': contract.get('resolution_source', 'unknown'),
        'mode': mode,
        'trades_fetched': 0,
        'raw_files_written': [],
        'errors': [],
    }

    print(f'  [{tick}] mode={mode} contract={contract["address"][:12]}... limit={limit}')

    # ── Fetch trades from OnChainOS ──
    all_trades = fetch_trades_onchainos(contract['address'], limit=limit)
    result['trades_fetched'] = len(all_trades)

    if not all_trades:
        print(f'  [{tick}] OnChainOS returned 0 trades')

    # ── Write raw files (append-only snapshots) ──
    ts_slug = now.strftime('%Y%m%dT%H%M%SZ')
    tick_raw_dir = RAW_ROOT / tick

    if all_trades and not DRY_RUN:
        trades_path = tick_raw_dir / f'trades-{ts_slug}.json'
        payload = {
            '_meta': {
                'tick': tick,
                'contract': contract['address'],
                'chain': 'bsc',
                'fetched_at': now.isoformat(),
                'mode': mode,
                'source': f'onchainos token trades --address {contract["address"]} --chain bsc',
                'source_type': 'onchainos_dex_trades',
                'resolution_source': contract.get('resolution_source', 'unknown'),
                'trade_count': len(all_trades),
                'token_name': contract.get('name', ''),
                'token_symbol': contract.get('symbol', ''),
            },
            'trades': all_trades,
        }
        atomic_write(trades_path, payload)
        result['raw_files_written'].append(str(trades_path))
        print(f'  [{tick}] wrote {len(all_trades)} trades → {trades_path.name}')
    elif all_trades and DRY_RUN:
        print(f'  [{tick}] [dry-run] would write {len(all_trades)} trades')

    # ── Update state ──
    got_data = result['trades_fetched'] > 0
    new_state = {
        'tick': tick,
        'contract': contract['address'],
        'last_sync_at': now.isoformat(),
        'last_mode': mode,
        'trades_fetched': result['trades_fetched'],
        'backfill_completed': state.get('backfill_completed', False) or (is_backfill and got_data),
        'sync_count': state.get('sync_count', 0) + 1,
        'last_outcome': 'data' if got_data else 'empty',
        'source': 'onchainos_dex_trades',
    }
    save_tick_state(tick, new_state)

    return result


# ── main ─────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc)
    print(f'[onchain-tick-sync] started at {now.isoformat()}')
    print(f'  dry_run={DRY_RUN} force_backfill={FORCE_BACKFILL}')
    print(f'  source: OnChainOS DEX trades (contract-native)')

    ticks = load_trending_ticks()
    if not ticks:
        print('ERROR: no trending ticks found', file=sys.stderr)
        sys.exit(1)

    print(f'  trending ticks ({len(ticks)}): {", ".join(ticks)}')

    headers = get_api_headers()
    contract_cache = load_contract_cache()

    # Phase 1: Resolve all tick → contract mappings
    print('\n── Phase 1: Contract Resolution ──')
    resolved: dict[str, dict] = {}
    unresolved: list[str] = []
    for tick in ticks:
        contract = resolve_tick_contract(tick, headers, contract_cache)
        if contract:
            resolved[tick] = contract
        else:
            unresolved.append(tick)

    # Save updated cache
    save_contract_cache(contract_cache)

    print(f'\n  resolved: {len(resolved)}/{len(ticks)} ticks')
    if unresolved:
        print(f'  unresolved: {", ".join(unresolved)}')

    if not resolved:
        print('ERROR: no contracts resolved for any tick', file=sys.stderr)
        manifest = {
            'sync_at': now.isoformat(),
            'status': 'blocked',
            'blocked_reason': 'contract resolution failed for all ticks',
            'ticks_attempted': len(ticks),
            'unresolved': unresolved,
        }
        if not DRY_RUN:
            atomic_write(RAW_ROOT / 'manifest.json', manifest)
        print(json.dumps({'status': 'blocked', 'blocked_reason': manifest['blocked_reason']}))
        sys.exit(2)

    # Phase 2: Fetch trades for each resolved tick
    print('\n── Phase 2: Trade Fetch (OnChainOS) ──')
    results: list[dict] = []
    total_trades = 0
    total_files: list[str] = []
    errors: list[str] = []

    for tick, contract in resolved.items():
        r = sync_tick(headers, tick, contract, now)
        results.append(r)
        total_trades += r['trades_fetched']
        total_files.extend(r['raw_files_written'])
        errors.extend(r['errors'])
        # Rate limit between ticks
        time.sleep(0.5)

    # ── Determine outcome ──
    if total_trades > 0 and not errors:
        status = 'ok'
    elif total_trades > 0 and errors:
        status = 'partial'
    elif total_trades == 0 and not errors:
        status = 'blocked'
    else:
        status = 'blocked'

    # ── Write manifest ──
    manifest = {
        'sync_at': now.isoformat(),
        'mode': 'force_backfill' if FORCE_BACKFILL else 'auto',
        'dry_run': DRY_RUN,
        'status': status,
        'source': 'onchainos_dex_trades',
        'source_precedence': [
            'onchainos token search (primary contract resolution)',
            'tagai /community/detail (fallback contract resolution)',
            'onchainos token info (contract validation)',
            'onchainos token trades (trade data)',
        ],
        'ticks_attempted': len(ticks),
        'ticks_resolved': len(resolved),
        'ticks_unresolved': unresolved,
        'total_trades_fetched': total_trades,
        'total_raw_files_written': len(total_files),
        'raw_files': total_files,
        'errors': errors,
        'blocked_reason': ('OnChainOS returned no trades for resolved contracts'
                          if status == 'blocked' else None),
        'contract_map': {tick: c['address'] for tick, c in resolved.items()},
        'per_tick': results,
    }

    if not DRY_RUN:
        manifest_path = RAW_ROOT / 'manifest.json'
        atomic_write(manifest_path, manifest)
        print(f'\n  manifest → {manifest_path}')

    print(f'\n[onchain-tick-sync] done: {status} '
          f'| {len(resolved)}/{len(ticks)} resolved | {total_trades} trades | {len(total_files)} files')

    # Output summary JSON for caller
    summary = {
        'status': status,
        'ticks_resolved': len(resolved),
        'ticks_attempted': len(ticks),
        'total_trades': total_trades,
        'files_written': len(total_files),
        'errors': errors,
    }
    if status == 'blocked':
        summary['blocked_reason'] = manifest['blocked_reason']
    if unresolved:
        summary['unresolved_ticks'] = unresolved
    print(json.dumps(summary))

    # Exit non-zero when no usable data was collected
    if status == 'blocked':
        sys.exit(2)


if __name__ == '__main__':
    main()
