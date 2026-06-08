#!/usr/bin/env python3
"""economic_ledger.py — append-only attribution ledger + reward evaluator.

Records, per cycle, the active arm and the agency's economic snapshot, so that an arm's
effect on REAL economic reward can be measured over an attribution window and differenced
against a baseline arm.

  ledger row = {ts, cycle_id, arm_b, mode: baseline|experiment|historical, snapshot}

In shadow mode the arm does not yet drive behavior, so the ledger's first job is to (a)
prove the measurement pipeline works end to end and (b) characterise the reward NOISE
FLOOR — how much reward_accrual / total value swings on its own — which sets the minimum
arm effect that is detectable, and over what window. (Karpathy: measure your noise before
you trust a signal.)

History bootstrap: trader-ledger.json already holds ~17 days of (portfolio_usd,
claimable_usd) per cycle. `backfill()` seeds those as historical-baseline rows so the
noise floor is available immediately.

CLI:
  python3 economic_ledger.py --backfill        seed from trader-ledger.json history
  python3 economic_ledger.py --record          append one live snapshot (mode=baseline)
  python3 economic_ledger.py --noise-floor [K]  report reward distribution over K-hour windows
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import economic_reward as er

WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE') or (Path.home() / '.openclaw' / 'workspace'))
RUNTIME = WORKSPACE / 'runtime'
LEDGER_PATH = RUNTIME / 'shared' / 'evolution-ledger.jsonl'
TRADER_LEDGER = RUNTIME / 'trader' / 'trader-ledger.json'


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None


def append_row(row: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')


def load_ledger() -> list[dict[str, Any]]:
    if not LEDGER_PATH.exists():
        return []
    rows = []
    for line in LEDGER_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    rows.sort(key=lambda r: r.get('ts') or '')
    return rows


def record_cycle(arm_b: dict[str, Any] | None = None, mode: str = 'baseline',
                 cycle_id: str = '') -> dict[str, Any]:
    snap = er.snapshot()
    # Trader data refreshes slower than the record cadence, so consecutive calls
    # often capture an UNCHANGED economic snapshot at a new ts. Flag those as
    # stale_dup so window math doesn't treat a non-moving economy as fresh windows.
    prev_rows = load_ledger()
    stale_dup = False
    if prev_rows:
        prev_snap = prev_rows[-1].get('snapshot') or {}
        stale_dup = (
            prev_snap.get('portfolio_usd') == snap.get('portfolio_usd')
            and prev_snap.get('claimable_usd') == snap.get('claimable_usd')
        )
    row = {
        'ts': snap['ts'], 'cycle_id': cycle_id, 'mode': mode,
        'arm_b': arm_b or {}, 'snapshot': snap,
    }
    if stale_dup:
        row['stale_dup'] = True
    append_row(row)
    return row


def backfill() -> int:
    """Seed historical-baseline rows from trader-ledger.json (portfolio_usd, claimable_usd
    per cycle). These have no per-asset balances, so only d_total and reward_accrual are
    derivable from them — enough to characterise the baseline reward process."""
    if not TRADER_LEDGER.exists():
        print(f'no trader-ledger at {TRADER_LEDGER}', file=sys.stderr)
        return 0
    d = json.loads(TRADER_LEDGER.read_text(encoding='utf-8'))
    existing_ts = {r.get('ts') for r in load_ledger() if r.get('mode') == 'historical'}
    n = 0
    for e in d.get('entries') or []:
        ts = e.get('timestamp') or e.get('cycle_id')
        if not ts or ts in existing_ts:
            continue
        try:
            portfolio = float(e.get('portfolio_usd'))
            claimable = float(e.get('claimable_usd'))
        except (TypeError, ValueError):
            continue
        snap = {
            'ts': ts, 'portfolio_usd': portfolio, 'claimable_usd': claimable,
            'total_economic_value_usd': round(portfolio + claimable, 6),
            'assets': {}, 'sources_ok': {'trader-ledger': True}, 'errors': [],
            'complete': True, 'partial_no_assets': True,
        }
        append_row({'ts': ts, 'cycle_id': e.get('cycle_id', ''), 'mode': 'historical',
                    'arm_b': {}, 'snapshot': snap})
        n += 1
    return n


def window_rewards(rows: list[dict[str, Any]], k_hours: float) -> list[dict[str, Any]]:
    """For each row, pair it with the earliest later row at least k_hours away and compute
    the decomposed reward over that window."""
    snaps = [(_parse_ts(r['snapshot'].get('ts')), r) for r in rows
             if r.get('snapshot') and not r.get('stale_dup')]
    snaps = [(t, r) for t, r in snaps if t is not None]
    out = []
    j = 0
    for i, (ti, ri) in enumerate(snaps):
        # advance j to first snapshot >= ti + k_hours
        target = ti.timestamp() + k_hours * 3600
        k = max(j, i + 1)
        while k < len(snaps) and snaps[k][0].timestamp() < target:
            k += 1
        if k >= len(snaps):
            break
        wr = er.window_reward(ri['snapshot'], snaps[k][1]['snapshot'])
        wr['mode_start'] = ri.get('mode')
        out.append(wr)
    return out


def noise_floor(rows: list[dict[str, Any]], k_hours: float) -> dict[str, Any]:
    wrs = window_rewards(rows, k_hours)
    if not wrs:
        return {'windows': 0, 'note': 'not enough history for this window'}

    def stats(vals: list[float]) -> dict[str, float]:
        vals = [v for v in vals if v is not None]
        if not vals:
            return {}
        s = sorted(vals)
        return {
            'mean': round(statistics.mean(vals), 4),
            'stdev': round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
            'p10': round(s[int(0.1 * (len(s) - 1))], 4),
            'p90': round(s[int(0.9 * (len(s) - 1))], 4),
            'min': round(min(vals), 4), 'max': round(max(vals), 4),
        }

    accrual = [w['reward_accrual_usd'] for w in wrs]
    total = [w['d_total_usd'] for w in wrs]
    acc_stats = stats(accrual)
    # Minimum detectable effect (rough): to separate two arms at ~2 sigma you need the
    # per-window effect to exceed ~2*stdev/sqrt(n_per_arm). Report the 1-window 2-sigma band.
    mde_1win = round(2 * acc_stats.get('stdev', 0.0), 4) if acc_stats else None
    return {
        'k_hours': k_hours, 'windows': len(wrs),
        'reward_accrual_usd': acc_stats,
        'd_total_usd': stats(total),
        'mde_2sigma_single_window_usd': mde_1win,
        'note': 'reward_accrual_usd = social/curator/creator USD earned (the Track B objective). '
                'd_total includes market price noise.',
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--backfill', action='store_true')
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--noise-floor', nargs='?', const='6', default=None,
                    help='report noise floor over K hours (default 6)')
    args = ap.parse_args()

    if args.backfill:
        n = backfill()
        print(f'backfilled {n} historical rows into {LEDGER_PATH}')
    if args.record:
        row = record_cycle()
        print(json.dumps(row['snapshot'], indent=2, ensure_ascii=False))
    if args.noise_floor is not None:
        rows = load_ledger()
        for k in (float(args.noise_floor), 24.0):
            nf = noise_floor(rows, k)
            print(f'\n=== NOISE FLOOR @ {k}h window ({nf.get("windows")} windows) ===')
            print(json.dumps(nf, indent=2, ensure_ascii=False))
    if not (args.backfill or args.record or args.noise_floor is not None):
        ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
