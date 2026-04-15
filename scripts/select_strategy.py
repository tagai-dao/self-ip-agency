#!/usr/bin/env python3
"""Main strategy selector — autoresearch-style hill-climbing over TAS.

Reads strategy logs, distills best/worst guidance combinations,
then outputs next-cycle guidance for Bookmarker and Trader.

Three search modes:
  EXPLORE  — vary 1-2 params from current best (consecutive wins or sparse history)
  EXPLOIT  — revert to historically best guidance (after a loss)
  SIGNAL   — override topic_directive when x-trend has strong new signal

Usage:
  python3 scripts/select_strategy.py           # print next guidance
  python3 scripts/select_strategy.py --stats   # print log stats + best combos
  python3 scripts/select_strategy.py --apply   # write guidance files directly
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT    = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'runtime'
MEMORY  = ROOT / 'memory'

MAIN_STRATEGY_LOG = MEMORY / 'main-strategy-log.jsonl'
BK_STRATEGY_LOG   = MEMORY / 'bookmarker-strategy-log.jsonl'
TR_STRATEGY_LOG   = MEMORY / 'trader-strategy-log.jsonl'

BOOKMARKER_GUIDANCE = RUNTIME / 'main' / 'bookmarker-guidance.json'
TRADER_GUIDANCE     = RUNTIME / 'main' / 'trader-guidance.json'
STRATEGY_PLAN       = RUNTIME / 'main' / 'strategy-plan.json'
BUDGET_ALLOCATION   = RUNTIME / 'shared' / 'budget-allocation.json'

# ── Search spaces ──
BK_SEARCH_SPACE: dict[str, list] = {
    'signal_priority':         ['align_first', 'community_first', 'balanced'],
    'topic_directive':         ['settlement_primitive', 'agent_economy', 'desoc_protocol',
                                'token_coordination', 'tagclaw_ecosystem'],
    'interaction_budget_vp':   ['low', 'mid', 'high'],
    'action_emphasis':         ['post_new', 'curate_heavy', 'reply_focus'],
    'interaction_target_mode': ['high_engagement_authors', 'high_vp_curators',
                                'owner_adjacent', 'trending_tick_authors', 'reciprocity_history'],
    'recognition_aware':       ['true', 'false'],
}

TR_SEARCH_SPACE: dict[str, list] = {
    'claim_patience':        ['eager', 'standard', 'patient'],
    'claim_threshold_usd':   [0.5, 1.0, 2.0, 3.0],
    'claim_frequency_mode':  ['aggressive', 'standard'],
    'portfolio_target_tick': ['auto', 'BUIDL', 'TagClaw', 'TTAI'],
    'focus_action':          ['claim_priority', 'accumulate', 'rebalance'],
    'risk_mode':             ['conservative', 'standard', 'aggressive'],
}

TR_PATIENCE_MAP = {
    'eager':    (0.5, 'aggressive'),
    'standard': (2.0, 'standard'),
    'patient':  (5.0, 'standard'),
}


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


def load_log(path: Path, n: int = 50) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').strip().split('\n')
    entries = []
    for line in lines[-n:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return entries


def analyze_log(entries: list[dict], delta_key: str) -> dict:
    if not entries:
        return {'best_combinations': [], 'worst_combinations': [], 'win_rate': 0, 'recent_trend': [], 'total_cycles': 0}

    param_stats: dict[str, dict[str, tuple[float, int]]] = defaultdict(lambda: defaultdict(lambda: (0.0, 0)))

    for entry in entries:
        guidance = entry.get('guidance') or {}
        delta = entry.get(delta_key) or 0.0
        if delta is None:
            continue
        for param, value in guidance.items():
            if param.startswith('_'):
                continue
            key = str(value)
            s, c = param_stats[param][key]
            param_stats[param][key] = (s + delta, c + 1)

    best, worst = [], []
    for param, values in param_stats.items():
        for val, (total_delta, count) in values.items():
            avg = round(total_delta / count, 4)
            entry_data = {
                'param': param, 'value': val,
                'avg_delta': avg, 'sample_count': count,
                'confidence': 'high' if count >= 3 else ('medium' if count >= 2 else 'low'),
            }
            if avg > 0:
                best.append(entry_data)
            elif avg < 0:
                worst.append(entry_data)

    best.sort(key=lambda x: (-x['avg_delta'], -x['sample_count']))
    worst.sort(key=lambda x: (x['avg_delta'], -x['sample_count']))

    recent = entries[-5:]
    recent_trend = [e.get(delta_key) or 0.0 for e in recent]
    kept = sum(1 for e in entries if e.get('kept'))
    win_rate = round(kept / len(entries), 3)

    return {
        'best_combinations': best[:8],
        'worst_combinations': worst[:5],
        'win_rate': win_rate,
        'recent_trend': recent_trend,
        'total_cycles': len(entries),
    }


def pick_mode(entries: list[dict], delta_key: str) -> str:
    if len(entries) < 2:
        return 'baseline'
    recent = entries[-3:]
    deltas = [e.get(delta_key) or 0.0 for e in recent]
    last_delta = deltas[-1] if deltas else 0.0
    if last_delta < 0:
        return 'exploit'
    if all(d > 0 for d in deltas):
        return 'explore'
    return 'explore'


def reconstruct_best_guidance(entries: list[dict]) -> dict | None:
    if not entries:
        return None
    improved = [e for e in entries if (e.get('kept') or False)]
    if not improved:
        return None
    best = max(improved, key=lambda e: e.get('delta_social') or e.get('delta_trade') or 0)
    return best.get('guidance')


def generate_bookmarker_guidance(entries, analysis, mode, x_trend_kws, community_scan, tas_social):
    candidates = (community_scan or {}).get('curation_candidates', [])
    top_authors = list({c['author'] for c in candidates[:6]
                        if c.get('author')})[:3]

    topic_map = {
        'settlement': 'settlement_primitive', 'agent': 'agent_economy',
        'swarm': 'agent_economy', 'desoc': 'desoc_protocol',
        'token': 'token_coordination', 'tagclaw': 'tagclaw_ecosystem',
    }
    topic = 'agent_economy'
    for kw in x_trend_kws[:5]:
        for key, val in topic_map.items():
            if key in kw.lower():
                topic = val
                break

    if mode == 'exploit':
        best = reconstruct_best_guidance(entries)
        if best:
            g = dict(best)
            g['_source'] = 'exploit_best'
            g['suggested_targets'] = top_authors
            g['topic_directive'] = topic
            return g

    if mode == 'baseline':
        return {
            'signal_priority': 'balanced',
            'topic_directive': topic,
            'interaction_budget_vp': 'mid',
            'action_emphasis': 'curate_heavy',
            'interaction_target_mode': 'high_engagement_authors',
            'suggested_targets': top_authors,
            '_source': 'baseline',
        }

    # EXPLORE
    best_per_param: dict[str, str] = {}
    for combo in analysis.get('best_combinations', []):
        p, v = combo['param'], combo['value']
        if p not in best_per_param:
            best_per_param[p] = v

    last_guidance = entries[-1].get('guidance', {}) if entries else {}

    base = {
        'signal_priority': best_per_param.get('signal_priority',
                           last_guidance.get('signal_priority', 'balanced')),
        'topic_directive': topic,
        'interaction_budget_vp': best_per_param.get('interaction_budget_vp',
                                 last_guidance.get('interaction_budget_vp', 'mid')),
        'action_emphasis': best_per_param.get('action_emphasis',
                           last_guidance.get('action_emphasis', 'curate_heavy')),
        'interaction_target_mode': best_per_param.get('interaction_target_mode',
                                   last_guidance.get('interaction_target_mode', 'high_engagement_authors')),
        'suggested_targets': top_authors,
    }

    mutate_candidates = list(set(BK_SEARCH_SPACE.keys()))
    mutate_param = random.choice(mutate_candidates)
    space = BK_SEARCH_SPACE[mutate_param]
    current_val = base.get(mutate_param, space[0])
    other_vals = [v for v in space if v != str(current_val)]
    if other_vals:
        base[mutate_param] = random.choice(other_vals)

    base['_source'] = f'explore_mutate_{mutate_param}'
    return base


def generate_trader_guidance(entries, analysis, mode, onchain, reward_status, tas_trade):
    positions = (onchain or {}).get('positions', [])
    portfolio_tick = 'auto'
    if positions:
        min_pos = min(positions, key=lambda p: p.get('value_usd', 0))
        portfolio_tick = min_pos.get('tick', 'auto')

    claimable = float((reward_status or {}).get('claimable_usd_total') or 0)

    if mode == 'exploit':
        best = reconstruct_best_guidance(entries)
        if best:
            g = dict(best)
            g['_source'] = 'exploit_best'
            g['portfolio_target_tick'] = portfolio_tick
            return g

    if mode == 'baseline':
        threshold = 0.5 if claimable >= 3.0 else 2.0
        return {
            'claim_patience': 'eager' if claimable >= 3.0 else 'standard',
            'claim_threshold_usd': threshold,
            'claim_frequency_mode': 'standard',
            'portfolio_target_tick': portfolio_tick,
            'focus_action': 'claim_priority',
            'risk_mode': 'standard',
            '_source': 'baseline',
        }

    # EXPLORE
    best_per_param: dict[str, Any] = {}
    for combo in analysis.get('best_combinations', []):
        p, v = combo['param'], combo['value']
        if p not in best_per_param:
            best_per_param[p] = v

    last_guidance = entries[-1].get('guidance', {}) if entries else {}
    base: dict[str, Any] = {
        'claim_patience': best_per_param.get('claim_patience',
                          last_guidance.get('claim_patience', 'standard')),
        'claim_threshold_usd': float(best_per_param.get('claim_threshold_usd',
                               last_guidance.get('claim_threshold_usd', 2.0))),
        'claim_frequency_mode': best_per_param.get('claim_frequency_mode',
                                last_guidance.get('claim_frequency_mode', 'standard')),
        'portfolio_target_tick': portfolio_tick,
        'focus_action': best_per_param.get('focus_action',
                        last_guidance.get('focus_action', 'claim_priority')),
        'risk_mode': best_per_param.get('risk_mode',
                     last_guidance.get('risk_mode', 'standard')),
    }

    mutate_param = random.choice(['claim_patience', 'risk_mode', 'focus_action'])
    if mutate_param in TR_SEARCH_SPACE:
        space = TR_SEARCH_SPACE[mutate_param]
        other_vals = [v for v in space if str(v) != str(base.get(mutate_param))]
        if other_vals:
            base[mutate_param] = random.choice(other_vals)

    if 'claim_patience' in base:
        patience = str(base.get('claim_patience', 'standard'))
        threshold, freq = TR_PATIENCE_MAP.get(patience, (2.0, 'standard'))
        base['claim_threshold_usd'] = threshold
        base['claim_frequency_mode'] = freq

    base['_source'] = f'explore_mutate_{mutate_param}'
    return base


def select_strategy(apply: bool = False) -> dict:
    bk_entries = load_log(BK_STRATEGY_LOG, 50)
    tr_entries = load_log(TR_STRATEGY_LOG, 50)
    bk_analysis = analyze_log(bk_entries, 'delta_social')
    tr_analysis = analyze_log(tr_entries, 'delta_trade')
    bk_mode = pick_mode(bk_entries, 'delta_social')
    tr_mode = pick_mode(tr_entries, 'delta_trade')

    community_scan = read_json(RUNTIME / 'bookmarker' / 'community-scan.json')
    x_trend_raw = (read_json(RUNTIME / 'bookmarker' / 'x-trend-latest.json') or {}).get('keywords', [])
    x_trend_kws = [(kw['term'] if isinstance(kw, dict) else str(kw)) for kw in x_trend_raw if kw]
    reward_status = read_json(RUNTIME / 'trader' / 'reward-status.json')
    onchain = read_json(RUNTIME / 'trader' / 'onchain-positions.json')

    bk_tas = float((read_json(RUNTIME / 'bookmarker' / 'tas-social.json') or {}).get('value') or 0)
    tr_tas = float((read_json(RUNTIME / 'trader' / 'tas-trade.json') or {}).get('value') or 0)

    bk_guidance = generate_bookmarker_guidance(bk_entries, bk_analysis, bk_mode, x_trend_kws, community_scan, bk_tas)
    tr_guidance = generate_trader_guidance(tr_entries, tr_analysis, tr_mode, onchain, reward_status, tr_tas)

    generated_at = now_iso()

    bk_doc = {
        'version': 'v3', 'generated_at': generated_at,
        'source_agent': 'main', 'experiment_mode': bk_mode,
        'guidance_source': 'strategy-derived',
        'worker_target_metric': 'TAS_social',
        'cycle_stats': {
            'total_cycles': bk_analysis['total_cycles'],
            'win_rate': bk_analysis['win_rate'],
            'recent_trend': bk_analysis['recent_trend'],
        },
        'best_param_combinations': bk_analysis['best_combinations'][:3],
        'guidance': bk_guidance,
        'notes': f'select_strategy v3; mode={bk_mode}; win_rate={bk_analysis["win_rate"]}',
    }

    tr_doc = {
        'version': 'v3', 'generated_at': generated_at,
        'source_agent': 'main', 'experiment_mode': tr_mode,
        'guidance_source': 'strategy-derived',
        'worker_target_metric': 'TAS_trade',
        'cycle_stats': {
            'total_cycles': tr_analysis['total_cycles'],
            'win_rate': tr_analysis['win_rate'],
            'recent_trend': tr_analysis['recent_trend'],
        },
        'best_param_combinations': tr_analysis['best_combinations'][:3],
        'guidance': tr_guidance,
        'notes': f'select_strategy v3; mode={tr_mode}; win_rate={tr_analysis["win_rate"]}',
    }

    if apply:
        atomic_write_json(BOOKMARKER_GUIDANCE, bk_doc)
        atomic_write_json(TRADER_GUIDANCE, tr_doc)

    return {
        'bookmarker': {'mode': bk_mode, 'guidance': bk_guidance, 'win_rate': bk_analysis['win_rate']},
        'trader': {'mode': tr_mode, 'guidance': tr_guidance, 'win_rate': tr_analysis['win_rate']},
    }


def print_stats():
    for label, log_path, delta_key in [
        ('Bookmarker', BK_STRATEGY_LOG, 'delta_social'),
        ('Trader', TR_STRATEGY_LOG, 'delta_trade'),
    ]:
        entries = load_log(log_path, 50)
        if not entries:
            print(f'\n{label}: no log yet')
            continue
        analysis = analyze_log(entries, delta_key)
        mode = pick_mode(entries, delta_key)
        print(f'\n-- {label} --')
        print(f'  cycles={analysis["total_cycles"]}  win_rate={analysis["win_rate"]:.0%}  mode={mode}')
        print(f'  recent_trend={analysis["recent_trend"]}')
        if analysis['best_combinations']:
            print('  BEST param values:')
            for c in analysis['best_combinations'][:4]:
                print(f'    {c["param"]}={c["value"]:20s}  avg_delta={c["avg_delta"]:+.4f}  n={c["sample_count"]}')


def main() -> int:
    args = set(sys.argv[1:])
    if '--stats' in args:
        print_stats()
        return 0
    result = select_strategy(apply='--apply' in args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if '--apply' in args:
        print(f'\nGuidance files written: {BOOKMARKER_GUIDANCE}, {TRADER_GUIDANCE}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
