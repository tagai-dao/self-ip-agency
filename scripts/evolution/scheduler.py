#!/usr/bin/env python3
"""scheduler.py — two-timescale, baseline-controlled arm scheduler for Track B (social).

Why not the old epsilon-greedy bandit: the arm space is tiny (6 arms) and the economic
reward's noise floor means each arm needs WEEKS to judge. Switching arms hourly against
that noise is hopeless. Instead we run a disciplined sequential A/B:

  - One incumbent BASELINE arm and one CANDIDATE challenger at a time.
  - Time is sliced into PERIODS (default 24h). Periods alternate baseline / candidate so
    time-varying exogenous noise (protocol distributions, market) cancels out.
  - FAST proxy pre-screen: after a few candidate periods, drop a candidate that is clearly
    worse on the fast leading indicator (proxy_signal) — don't spend economic-gate weeks on it.
  - SLOW economic gate: a candidate is only PROMOTED to baseline if its real attributable
    USD earning beats baseline at ~2 sigma over enough periods. Real money is the only
    promotion authority.

SHADOW MODE: until the arm is wired into post_config (cutover), assigning an arm does not
change behavior, so review() will sit at CONTINUE (no measurable arm effect) — that is the
*correct* shadow outcome. The same code measures real effects once live. The shadow run's
job is to prove the machinery end to end and measure the in-situ noise floor.

CLI:
  python3 scheduler.py --status     show state
  python3 scheduler.py --record     assign the active arm for now + log shadow snapshots
  python3 scheduler.py --review     evaluate candidate vs baseline, maybe promote/discard
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import economic_ledger as el
import economic_reward as er
import proxy_signal as ps

WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE') or (Path.home() / '.openclaw' / 'workspace'))
STATE_PATH = WORKSPACE / 'runtime' / 'shared' / 'evolution-scheduler-state.json'

# P4 (2026-06-06): post_timing was DEAD (no executor consumes it) — the shadow was
# comparing arms with NO behavioral effect, so the proxy pre-screen discarded on pure
# noise. Dropped so the shadow tests only the WIRED lever (engagement_mode, now fed via
# run_main_runtime post_config → execute_social_intent_v2). Re-add post_timing only once
# a real post scheduler consumes it.
ENGAGEMENT_MODES = ['none', 'reply_to_top_agents']
BASELINE_ARM = {'engagement_mode': 'none'}

PERIOD_HOURS = 24.0
MIN_PERIOD_SECONDS = 22 * 3600  # anti-flutter: don't promote/discard before a period matures
                                # (fixes the 2026-06-04 1-second period collapse)
PRIMARY_PROXY = 'op'           # only live-moving /me metric; confirm it's behavior-sensitive in shadow
MIN_PROXY_PERIODS = 3          # candidate periods before the fast pre-screen can fire
MIN_ECON_PERIODS = 10          # per arm, before the economic gate can decide (noise-floor driven)
PROXY_DISCARD_MARGIN = 0.0     # candidate proxy mean must be >= baseline - margin to survive screen
ECON_Z = 2.0                   # ~2 sigma to promote/discard on real earning


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _arm_key(a: dict[str, Any]) -> str:
    return str(a.get('engagement_mode'))


def arm_space() -> list[dict[str, Any]]:
    return [{'engagement_mode': em} for em in ENGAGEMENT_MODES]


def default_state() -> dict[str, Any]:
    others = [a for a in arm_space() if _arm_key(a) != _arm_key(BASELINE_ARM)]
    return {
        'version': 'v1',
        'baseline_arm': dict(BASELINE_ARM),
        'candidate_arm': others[0] if others else None,
        'untested_arms': others[1:],
        'period_hours': PERIOD_HOURS,
        'periods': [],          # {period_id, arm, mode, started_at, ended_at}
        'decided': [],          # {candidate, verdict, proxy, economic, at}
        'updated_at': _iso(_now()),
    }


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return default_state()
    try:
        d = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else default_state()
    except (OSError, ValueError):
        return default_state()


def save_state(st: dict[str, Any]) -> None:
    st['updated_at'] = _iso(_now())
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=str(STATE_PATH.parent), suffix='.tmp',
                                     delete=False, encoding='utf-8') as f:
        json.dump(st, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, STATE_PATH)


def assign(st: dict[str, Any], now: datetime | None = None) -> tuple[dict[str, Any], str]:
    """Return (arm, mode) for the current period, rolling over a new period if the last one
    expired. Alternates baseline/candidate so exogenous noise differences cancel."""
    now = now or _now()
    periods = st.setdefault('periods', [])
    open_p = periods[-1] if periods and periods[-1].get('ended_at') is None else None
    if open_p is not None:
        start = ps._parse_ts(open_p['started_at'])
        if start and (now.timestamp() - start.timestamp()) < st.get('period_hours', PERIOD_HOURS) * 3600:
            return open_p['arm'], open_p['mode']
        open_p['ended_at'] = _iso(now)  # close the expired period

    # Roll a new period: alternate mode; candidate only if one exists.
    last_mode = periods[-1]['mode'] if periods else 'candidate'
    has_candidate = st.get('candidate_arm') is not None
    mode = 'baseline' if (last_mode == 'candidate' or not has_candidate) else 'candidate'
    if mode == 'candidate' and not has_candidate:
        mode = 'baseline'
    arm = dict(st['baseline_arm']) if mode == 'baseline' else dict(st['candidate_arm'])
    periods.append({'period_id': len(periods), 'arm': arm, 'mode': mode,
                    'started_at': _iso(now), 'ended_at': None})
    return arm, mode


def record(cycle_id: str = '', now: datetime | None = None) -> dict[str, Any]:
    """Shadow step run each cycle: pick the active arm and log economic + proxy snapshots
    tagged with that arm. Does NOT change behavior until cutover wires the arm."""
    st = load_state()
    now = now or _now()
    arm, mode = assign(st, now)
    save_state(st)
    el.record_cycle(arm_b=arm, mode=mode, cycle_id=cycle_id)
    ps.snapshot(arm_b=arm, mode=mode, cycle_id=cycle_id)
    return {'arm': arm, 'mode': mode, 'period_id': st['periods'][-1]['period_id']}


def _nearest(rows: list[dict[str, Any]], target: datetime, ts_key=lambda r: r.get('ts')):
    best, best_d = None, None
    for r in rows:
        t = ps._parse_ts(ts_key(r))
        if not t:
            continue
        d = abs(t.timestamp() - target.timestamp())
        if best_d is None or d < best_d:
            best, best_d = r, d
    return best


def _period_metrics(period: dict[str, Any], econ_rows, proxy_rows) -> dict[str, Any]:
    """Economic attributable_usd and proxy primary improvement realised during a period."""
    t0 = ps._parse_ts(period.get('started_at'))
    t1 = ps._parse_ts(period.get('ended_at'))
    if not (t0 and t1):
        return {}
    e0 = _nearest([r for r in econ_rows if r.get('snapshot')], t0, lambda r: r['snapshot'].get('ts'))
    e1 = _nearest([r for r in econ_rows if r.get('snapshot')], t1, lambda r: r['snapshot'].get('ts'))
    out: dict[str, Any] = {}
    if e0 and e1:
        out['attributable_usd'] = er.window_reward(e0['snapshot'], e1['snapshot'])['attributable_usd']
    p_rows = [r for r in proxy_rows if r.get('complete') and r.get('metrics')]
    p0 = _nearest(p_rows, t0)
    p1 = _nearest(p_rows, t1)
    if p0 and p1:
        imp = ps.improvement(ps.field_delta(p0['metrics'], p1['metrics']))
        out['proxy'] = imp.get(PRIMARY_PROXY)
    return out


def summary() -> dict[str, Any]:
    """Read-only evaluation: per-arm economic + proxy stats and the would-be verdict, with NO
    state mutation. Safe to call from a dashboard GET. review() wraps this to apply decisions."""
    st = load_state()
    econ_rows = el.load_ledger()
    proxy_rows = ps.load_ledger()
    closed = [p for p in st.get('periods', []) if p.get('ended_at')]

    def gather(mode: str, key: str):
        vals = []
        for p in closed:
            if p['mode'] != mode:
                continue
            m = _period_metrics(p, econ_rows, proxy_rows)
            if m.get(key) is not None:
                vals.append(m[key])
        return vals

    econ_b, econ_c = gather('baseline', 'attributable_usd'), gather('candidate', 'attributable_usd')
    prox_b, prox_c = gather('baseline', 'proxy'), gather('candidate', 'proxy')

    report: dict[str, Any] = {
        'baseline_arm': st['baseline_arm'], 'candidate_arm': st.get('candidate_arm'),
        'n_baseline_periods': len(econ_b), 'n_candidate_periods': len(econ_c),
        'economic': {'baseline_mean': _mean(econ_b), 'candidate_mean': _mean(econ_c)},
        'proxy': {'baseline_mean': _mean(prox_b), 'candidate_mean': _mean(prox_c),
                  'metric': PRIMARY_PROXY},
        'verdict': 'CONTINUE', 'reason': 'accumulating periods',
    }

    if st.get('candidate_arm') is None:
        report['verdict'] = 'IDLE'
        report['reason'] = 'no candidate — all arms explored'
        return report

    # Fast proxy pre-screen: drop a clearly-worse candidate early.
    if (len(prox_c) >= MIN_PROXY_PERIODS and prox_b and prox_c
            and _mean(prox_c) < _mean(prox_b) - PROXY_DISCARD_MARGIN
            and len(econ_c) < MIN_ECON_PERIODS):
        report['verdict'] = 'DISCARD'
        report['reason'] = f'proxy pre-screen: candidate {PRIMARY_PROXY} mean ' \
                           f'{_mean(prox_c):.4f} < baseline {_mean(prox_b):.4f}'
        return report

    # Slow economic gate.
    if len(econ_b) >= MIN_ECON_PERIODS and len(econ_c) >= MIN_ECON_PERIODS:
        z = _welch_z(econ_c, econ_b)
        report['economic']['z'] = round(z, 3) if z is not None else None
        if z is not None and z >= ECON_Z:
            report['verdict'] = 'PROMOTE'
            report['reason'] = f'economic gate: candidate beats baseline at z={z:.2f}'
        elif z is not None and z <= -ECON_Z:
            report['verdict'] = 'DISCARD'
            report['reason'] = f'economic gate: candidate worse at z={z:.2f}'
    return report


def review() -> dict[str, Any]:
    """Evaluate and APPLY any promote/discard decision (mutates state). The shadow launchd
    job calls this; the dashboard calls summary() instead."""
    report = summary()
    if report['verdict'] in ('PROMOTE', 'DISCARD'):
        # Anti-flutter gate: _decide() force-closes the open period. If we let it
        # fire on a period only seconds old (assign opens → review decides → _decide
        # closes, all in one cycle), we manufacture 1-second garbage periods and
        # discard candidates on ~0 data — the 2026-06-04 collapse. Hold until the
        # current period has matured.
        st = load_state()
        periods = st.get('periods') or []
        open_p = periods[-1] if periods and periods[-1].get('ended_at') is None else None
        if open_p is not None:
            start = ps._parse_ts(open_p.get('started_at'))
            if start and (_now().timestamp() - start.timestamp()) < MIN_PERIOD_SECONDS:
                report['verdict'] = 'HOLD'
                report['hold_reason'] = 'current period younger than MIN_PERIOD_SECONDS'
                return report
        _decide(st, report)
    return report


def _decide(st: dict[str, Any], report: dict[str, Any]) -> None:
    """Apply PROMOTE/DISCARD: update baseline/candidate, advance to next untested arm."""
    if report['verdict'] == 'PROMOTE':
        st['baseline_arm'] = dict(st['candidate_arm'])
    st.setdefault('decided', []).append({
        'candidate': st.get('candidate_arm'), 'verdict': report['verdict'],
        'reason': report['reason'], 'economic': report['economic'],
        'proxy': report['proxy'], 'at': _iso(_now()),
    })
    untested = st.setdefault('untested_arms', [])
    # never re-test the (possibly new) baseline
    untested = [a for a in untested if _arm_key(a) != _arm_key(st['baseline_arm'])]
    st['candidate_arm'] = untested.pop(0) if untested else None
    st['untested_arms'] = untested
    # fresh comparison: close any open period so the next assign starts clean
    for p in st.get('periods', []):
        if p.get('ended_at') is None:
            p['ended_at'] = _iso(_now())
    save_state(st)


def _mean(v: list[float]):
    return round(statistics.mean(v), 4) if v else None


def _welch_z(a: list[float], b: list[float]):
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = statistics.variance(a), statistics.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return 0.0
    return (statistics.mean(a) - statistics.mean(b)) / se


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--review', action='store_true')
    ap.add_argument('--cycle', action='store_true',
                    help='record + review in one shot (launchd shadow entrypoint)')
    args = ap.parse_args()
    if args.status:
        print(json.dumps(load_state(), indent=2, ensure_ascii=False))
    if args.record:
        print(json.dumps(record(), indent=2, ensure_ascii=False))
    if args.review:
        print(json.dumps(review(), indent=2, ensure_ascii=False))
    if args.cycle:
        rec = record()
        rev = review()
        print(json.dumps({'ts': _iso(_now()), 'recorded': rec,
                          'verdict': rev['verdict'], 'reason': rev['reason'],
                          'n_baseline': rev['n_baseline_periods'],
                          'n_candidate': rev['n_candidate_periods']}, ensure_ascii=False))
    if not (args.status or args.record or args.review or args.cycle):
        ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
