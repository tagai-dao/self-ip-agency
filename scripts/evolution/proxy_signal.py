#!/usr/bin/env python3
"""proxy_signal.py — fast leading-indicator collector for the two-timescale evolution loop.

The real economic reward (economic_reward.py) is the truth, but its noise floor means an arm
takes WEEKS to judge. The proxy is the fast half: the agent's own social-standing metrics
from /me — followers, score, rank, curations, vp, op — which respond in days. The scheduler
uses the proxy to pre-screen arms quickly, then only spends economic-gate weeks on the
survivors.

Polarity note: for followers/score/curations/vp/op, HIGHER is better. For `rank`, LOWER is
better (rank 1 beats rank 100), so improvement = -delta(rank).

Same anti-silent-failure discipline as the rest of the module: a failed /me call writes an
explicit error row (complete=False), never a zero that would be mistaken for "no growth".

CLI:
  python3 proxy_signal.py --snapshot         fetch /me now and append a row (needs runtime python+proxy)
  python3 proxy_signal.py --deltas [K]       per-field proxy delta over K-hour windows
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE') or (Path.home() / '.openclaw' / 'workspace'))
RUNTIME = WORKSPACE / 'runtime'
LEDGER_PATH = RUNTIME / 'shared' / 'proxy-signal-ledger.jsonl'

# This agent's /me exposes only these two numeric fields (verified live 2026-05-29):
#   vp — voting power (near-static)
#   op — a continuously-accruing activity/operation score (the only live-moving metric)
# Whether op's *accrual rate* responds to social behavior (vs pure time/stake accrual) is
# the open question the shadow run must answer before op can be trusted as a proxy.
PROXY_FIELDS = ['vp', 'op']
# Higher-is-better for all listed fields (no rank-style "lower is better" metric exists here).
_LOWER_IS_BETTER: set[str] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None


def _num(x: Any) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def fetch_profile() -> dict[str, float]:
    """Live /me profile metrics. Lazy-imports the adapter so this module stays importable on
    Python 3.9 (dev) — only this call needs the runtime's 3.10+ interpreter and proxy."""
    # adapters/tagclaw.py does `from adapters.base import ...`, so WORKSPACE (parent of the
    # adapters/ namespace package) must be importable — runtime scripts get this via cwd.
    sys.path.insert(0, str(WORKSPACE))
    from adapters.tagclaw import TagClawAdapter, extract_me_agent  # type: ignore
    me = extract_me_agent(TagClawAdapter().get_me_raw())
    if not isinstance(me, dict) or not me:
        raise RuntimeError('empty /me response')
    out: dict[str, float] = {}
    for f in PROXY_FIELDS:
        v = _num(me.get(f))
        if v is not None:
            out[f] = v
    if not out:
        raise RuntimeError(f'/me had none of {PROXY_FIELDS}; keys={list(me.keys())}')
    return out


def append_row(row: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')


def snapshot(arm_b: dict[str, Any] | None = None, mode: str = 'baseline',
             cycle_id: str = '') -> dict[str, Any]:
    ts = _now_iso()
    base = {'ts': ts, 'cycle_id': cycle_id, 'mode': mode, 'arm_b': arm_b or {}}
    try:
        metrics = fetch_profile()
        row = {**base, 'metrics': metrics, 'complete': True}
    except Exception as e:
        # Explicit failure row — a missing proxy reading must be visible, not silently zero.
        row = {**base, 'metrics': {}, 'complete': False, 'error': str(e)}
    append_row(row)
    return row


def load_ledger() -> list[dict[str, Any]]:
    if not LEDGER_PATH.exists():
        return []
    rows = []
    for line in LEDGER_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    rows.sort(key=lambda r: r.get('ts') or '')
    return rows


def field_delta(m0: dict[str, float], m1: dict[str, float]) -> dict[str, float]:
    """Raw per-field delta between two metric snapshots."""
    return {f: round(m1[f] - m0[f], 4) for f in PROXY_FIELDS if f in m0 and f in m1}


def improvement(delta: dict[str, float]) -> dict[str, float]:
    """Delta re-signed so positive always means 'better' (rank inverted)."""
    return {f: (-d if f in _LOWER_IS_BETTER else d) for f, d in delta.items()}


def window_deltas(rows: list[dict[str, Any]], k_hours: float) -> list[dict[str, Any]]:
    good = [r for r in rows if r.get('complete') and r.get('metrics')]
    snaps = [(_parse_ts(r['ts']), r) for r in good]
    snaps = [(t, r) for t, r in snaps if t is not None]
    out = []
    for i, (ti, ri) in enumerate(snaps):
        target = ti.timestamp() + k_hours * 3600
        k = i + 1
        while k < len(snaps) and snaps[k][0].timestamp() < target:
            k += 1
        if k >= len(snaps):
            break
        d = field_delta(ri['metrics'], snaps[k][1]['metrics'])
        out.append({'window': [ri['ts'], snaps[k][1]['ts']], 'mode_start': ri.get('mode'),
                    'arm_b': ri.get('arm_b'), 'delta': d, 'improvement': improvement(d)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--snapshot', action='store_true')
    ap.add_argument('--deltas', nargs='?', const='24', default=None)
    args = ap.parse_args()
    if args.snapshot:
        row = snapshot()
        print(json.dumps(row, indent=2, ensure_ascii=False))
        return 0 if row['complete'] else 1
    if args.deltas is not None:
        wds = window_deltas(load_ledger(), float(args.deltas))
        print(f'{len(wds)} proxy windows @ {args.deltas}h')
        for w in wds[-10:]:
            print(json.dumps(w, ensure_ascii=False))
        return 0
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
