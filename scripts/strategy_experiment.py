#!/usr/bin/env python3
"""strategy_experiment.py — dual-track A/B arm framework for strategy experiments.

Track A: curator/VP strategy (credit_strategy, vp_strategy, target_selection)
Track B: social posting strategy (post_timing, engagement_mode, target_agents)

Main Agent calls run_cycle() each heartbeat. Results written to
runtime/shared/strategy-experiment.json (atomic write).
"""
from __future__ import annotations

import json
import os
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SHARED_RUNTIME = ROOT / 'runtime' / 'shared'
EXPERIMENT_PATH = SHARED_RUNTIME / 'strategy-experiment.json'

_TRACK_A_CREDIT_STRATEGIES = ['hold', 'buy_small', 'add_lp']
_TRACK_A_VP_STRATEGIES = ['conservative', 'balanced', 'aggressive']
_TRACK_A_TARGET_SELECTIONS = ['any', 'high_activity_only']

_TRACK_B_POST_TIMINGS = ['peak_activity', 'off_peak', 'post_sync']
_TRACK_B_ENGAGEMENT_MODES = ['none', 'reply_to_top_agents']
_TRACK_B_TARGET_AGENTS = ['foxclaw', 'clawdiai', 'alita']

MAX_ARM_HISTORY = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _default_experiment() -> dict[str, Any]:
    return {
        'version': 'v1',
        'updated_at': _now_iso(),
        'track_a': {
            'current_arm': {'credit_strategy': 'hold', 'vp_strategy': 'balanced', 'target_selection': 'any'},
            'last_arm': None, 'arm_history': [], 'best_arm': None, 'epsilon': 0.1,
        },
        'track_b': {
            'current_arm': {'post_timing': 'peak_activity', 'engagement_mode': 'reply_to_top_agents', 'target_agents': list(_TRACK_B_TARGET_AGENTS)},
            'last_arm': None, 'arm_history': [], 'best_arm': None, 'epsilon': 0.2,
        },
        'cycle_count': 0,
        'last_cycle_id': None,
    }


def load_experiment() -> dict[str, Any]:
    if not EXPERIMENT_PATH.exists():
        return _default_experiment()
    try:
        data = json.loads(EXPERIMENT_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else _default_experiment()
    except Exception:
        return _default_experiment()


def save_experiment(exp: dict[str, Any]) -> None:
    exp['updated_at'] = _now_iso()
    for track in ('track_a', 'track_b'):
        if track in exp and 'arm_history' in exp[track]:
            if len(exp[track]['arm_history']) > MAX_ARM_HISTORY:
                exp[track]['arm_history'] = exp[track]['arm_history'][-MAX_ARM_HISTORY:]
    EXPERIMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=str(EXPERIMENT_PATH.parent), suffix='.tmp', delete=False, encoding='utf-8') as f:
        json.dump(exp, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, EXPERIMENT_PATH)


def evaluate_track_a(exp, tas_delta, curator_reward_usd, vp_spent):
    track = exp.get('track_a') or {}
    history = track.get('arm_history') or []
    reward_per_vp = curator_reward_usd / max(vp_spent, 1.0)
    if history:
        last_entry = history[-1]
        last_reward_per_vp = last_entry.get('curator_reward_per_vp') or 0.0
        if reward_per_vp >= last_reward_per_vp:
            return 'reinforce'
        consecutive_declines = 0
        for entry in reversed(history[-3:]):
            idx = history.index(entry)
            entry_prev = history[idx - 1] if idx > 0 else None
            if entry_prev and (entry.get('curator_reward_per_vp') or 0) < (entry_prev.get('curator_reward_per_vp') or 0):
                consecutive_declines += 1
            else:
                break
        if consecutive_declines >= 2:
            return 'reset'
        return 'switch_target'
    return 'reinforce'


def evaluate_track_b(exp, tas_social_delta, creator_reward_usd, posts_count, curators_attracted):
    track = exp.get('track_b') or {}
    history = track.get('arm_history') or []
    if curators_attracted > 0:
        return 'reinforce'
    consecutive_no_curators = sum(1 for entry in reversed(history[-2:]) if (entry.get('curators_attracted') or 0) == 0)
    if consecutive_no_curators >= 2:
        return 'switch_engagement'
    if history:
        return 'switch_timing'
    return 'reinforce'


def _random_arm_a():
    return {'credit_strategy': random.choice(_TRACK_A_CREDIT_STRATEGIES), 'vp_strategy': random.choice(_TRACK_A_VP_STRATEGIES), 'target_selection': random.choice(_TRACK_A_TARGET_SELECTIONS)}


def _random_arm_b():
    return {'post_timing': random.choice(_TRACK_B_POST_TIMINGS), 'engagement_mode': random.choice(_TRACK_B_ENGAGEMENT_MODES), 'target_agents': list(_TRACK_B_TARGET_AGENTS)}


def select_next_arm_a(exp, verdict):
    track = exp.get('track_a') or {}
    current = dict(track.get('current_arm') or {})
    epsilon = float(track.get('epsilon') or 0.1)
    if verdict == 'reinforce':
        return _random_arm_a() if random.random() < epsilon else dict(current)
    elif verdict == 'switch_target':
        options = [t for t in _TRACK_A_TARGET_SELECTIONS if t != current.get('target_selection')]
        new_arm = dict(current)
        new_arm['target_selection'] = random.choice(options) if options else 'any'
        return new_arm
    return _random_arm_a()


def select_next_arm_b(exp, verdict):
    track = exp.get('track_b') or {}
    current = dict(track.get('current_arm') or {})
    epsilon = float(track.get('epsilon') or 0.2)
    if verdict == 'reinforce':
        return _random_arm_b() if random.random() < epsilon else dict(current)
    elif verdict == 'switch_timing':
        options = [t for t in _TRACK_B_POST_TIMINGS if t != current.get('post_timing')]
        new_arm = dict(current)
        new_arm['post_timing'] = random.choice(options) if options else 'peak_activity'
        return new_arm
    else:
        options = [m for m in _TRACK_B_ENGAGEMENT_MODES if m != current.get('engagement_mode')]
        new_arm = dict(current)
        new_arm['engagement_mode'] = random.choice(options) if options else 'none'
        return new_arm


def run_cycle(tas_delta=0.0, tas_social_delta=0.0, curator_reward_usd=0.0, vp_spent=0.0, creator_reward_usd=0.0, posts_count=0, curators_attracted=0, cycle_id=''):
    exp = load_experiment()
    track_a = exp.setdefault('track_a', _default_experiment()['track_a'])
    track_b = exp.setdefault('track_b', _default_experiment()['track_b'])

    verdict_a = evaluate_track_a(exp, tas_delta, curator_reward_usd, vp_spent)
    verdict_b = evaluate_track_b(exp, tas_social_delta, creator_reward_usd, posts_count, curators_attracted)

    track_a.setdefault('arm_history', []).append({
        'arm': dict(track_a.get('current_arm') or {}),
        'curator_reward_per_vp': round(curator_reward_usd / max(vp_spent, 1.0), 6),
        'tas_delta': round(tas_delta, 4), 'verdict': verdict_a, 'cycle_id': cycle_id,
    })
    track_b.setdefault('arm_history', []).append({
        'arm': dict(track_b.get('current_arm') or {}),
        'creator_reward_per_post': round(creator_reward_usd / max(posts_count, 1), 6),
        'curators_attracted': curators_attracted,
        'tas_social_delta': round(tas_social_delta, 4), 'verdict': verdict_b, 'cycle_id': cycle_id,
    })

    track_a['last_arm'] = dict(track_a.get('current_arm') or {})
    track_b['last_arm'] = dict(track_b.get('current_arm') or {})
    track_a['current_arm'] = select_next_arm_a(exp, verdict_a)
    track_b['current_arm'] = select_next_arm_b(exp, verdict_b)

    if track_a['arm_history']:
        best_a = max(track_a['arm_history'], key=lambda e: e.get('curator_reward_per_vp') or 0)
        track_a['best_arm'] = best_a.get('arm')
    if track_b['arm_history']:
        best_b = max(track_b['arm_history'], key=lambda e: e.get('creator_reward_per_post') or 0)
        track_b['best_arm'] = best_b.get('arm')

    exp['cycle_count'] = (exp.get('cycle_count') or 0) + 1
    exp['last_cycle_id'] = cycle_id
    save_experiment(exp)

    return {'track_a': track_a['current_arm'], 'track_b': track_b['current_arm'], 'verdict_a': verdict_a, 'verdict_b': verdict_b, 'cycle_count': exp['cycle_count']}


if __name__ == '__main__':
    import sys
    result = run_cycle(cycle_id='manual-test')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0)
