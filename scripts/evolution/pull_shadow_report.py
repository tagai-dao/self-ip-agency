#!/usr/bin/env python3
"""pull_shadow_report.py — snapshot of the shadow evolution run for the periodic check-in.

Answers the two questions the shadow phase exists to answer:
  1. Does `op` actually move, and is its *rate* constant (=> time/stake accrual, useless as a
     behavior proxy) or variable and correlated with activity (=> usable fast proxy)?
  2. How many A/B periods have accumulated (toward the ~10/arm the economic gate needs)?

Pure read of the ledgers — no network, no mutation. Run anytime:
  python3 pull_shadow_report.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import economic_ledger as el  # noqa: E402
import proxy_signal as ps  # noqa: E402
import scheduler as sch  # noqa: E402

WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE') or (Path.home() / '.openclaw' / 'workspace'))
SOCIAL_HISTORY = WORKSPACE / 'runtime' / 'shared' / 'social-history.json'


def _social_action_times() -> list[datetime]:
    try:
        items = (json.loads(SOCIAL_HISTORY.read_text(encoding='utf-8')) or {}).get('items') or []
    except (OSError, ValueError):
        return []
    out = []
    for it in items:
        t = ps._parse_ts(it.get('executed_at'))
        if t:
            out.append(t)
    return out


def main() -> int:
    now = datetime.now(timezone.utc)
    print('=' * 70)
    print(f'SHADOW EVOLUTION REPORT  @ {now.strftime("%Y-%m-%dT%H:%MZ")}')
    print('=' * 70)

    # ---- Q2: periods accumulated -------------------------------------------
    st = sch.load_state()
    periods = st.get('periods', [])
    closed = [p for p in periods if p.get('ended_at')]
    n_base = sum(1 for p in closed if p['mode'] == 'baseline')
    n_cand = sum(1 for p in closed if p['mode'] == 'candidate')
    ev = sch.summary()
    print('\n[PERIODS]  toward economic gate (needs ~%d closed per arm)' % sch.MIN_ECON_PERIODS)
    print(f'  total periods    : {len(periods)} ({len(closed)} closed, {len(periods)-len(closed)} open)')
    print(f'  closed baseline  : {n_base}')
    print(f'  closed candidate : {n_cand}')
    print(f'  baseline arm     : {st.get("baseline_arm")}')
    print(f'  candidate arm    : {st.get("candidate_arm")}')
    print(f'  verdict          : {ev["verdict"]} — {ev["reason"]}')
    if st.get('decided'):
        print(f'  decisions so far : {[(d["verdict"], d["candidate"]) for d in st["decided"]]}')

    # ---- Q1: does op move, and is its rate behavior-linked? -----------------
    proxy_rows = [r for r in ps.load_ledger() if r.get('complete') and r.get('metrics')]
    pts = [(ps._parse_ts(r['ts']), r['metrics']) for r in proxy_rows]
    pts = [(t, m) for t, m in pts if t]
    print(f'\n[PROXY]  {len(pts)} complete /me readings')
    if len(pts) < 2:
        print('  not enough readings yet — let it run longer.')
        return 0
    span_h = (pts[-1][0] - pts[0][0]).total_seconds() / 3600
    op0, op1 = pts[0][1].get('op'), pts[-1][1].get('op')
    vp0, vp1 = pts[0][1].get('vp'), pts[-1][1].get('vp')
    print(f'  span             : {span_h:.1f}h')
    print(f'  op   {op0} -> {op1}   (Δ {(op1-op0):+.3f})' if op0 is not None and op1 is not None else '  op: n/a')
    print(f'  vp   {vp0} -> {vp1}   (Δ {(vp1-vp0):+.3f})' if vp0 is not None and vp1 is not None else '  vp: n/a')

    # per-interval op rate (op per hour) + whether it tracks social activity
    action_times = _social_action_times()
    rates, rows = [], []
    for (t0, m0), (t1, m1) in zip(pts, pts[1:]):
        dt_h = (t1 - t0).total_seconds() / 3600
        if dt_h <= 0 or m0.get('op') is None or m1.get('op') is None:
            continue
        rate = (m1['op'] - m0['op']) / dt_h
        n_actions = sum(1 for a in action_times if t0 < a <= t1)
        vp_d = (m1.get('vp') or 0) - (m0.get('vp') or 0)
        rates.append(rate)
        rows.append((t0, t1, rate, n_actions, vp_d))
    if rates:
        mean_r = statistics.mean(rates)
        sd_r = statistics.pstdev(rates) if len(rates) > 1 else 0.0
        cv = (sd_r / mean_r) if mean_r else float('inf')
        print(f'\n[OP RATE]  op/hour across {len(rates)} intervals')
        print(f'  mean={mean_r:.3f}/h  stdev={sd_r:.3f}  CV={cv:.2f}')
        print('  interpretation: low CV (<~0.3) => ~constant => time/stake accrual (BAD proxy);')
        print('                  high CV + tracks actions/vp-spend => activity-linked (GOOD proxy).')
        # crude activity link: compare op-rate in intervals WITH vs WITHOUT social actions
        with_a = [r for _, _, r, na, _ in rows if na > 0]
        no_a = [r for _, _, r, na, _ in rows if na == 0]
        if with_a and no_a:
            print(f'  op/h when social action occurred : mean={statistics.mean(with_a):.3f} (n={len(with_a)})')
            print(f'  op/h when NO social action       : mean={statistics.mean(no_a):.3f} (n={len(no_a)})')
            print('  -> a clear gap here is evidence op responds to behavior.')
        else:
            print('  (not enough action/no-action split yet to test the activity link)')

    # ---- economic shadow rows since shadow start ----------------------------
    econ = el.load_ledger()
    shadow_econ = [r for r in econ if r.get('mode') in ('baseline', 'candidate')]
    print(f'\n[ECONOMIC]  {len(econ)} ledger rows ({len(shadow_econ)} live shadow, rest historical backfill)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
