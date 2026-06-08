#!/usr/bin/env python3
"""strategy_experiment.py — dual-track A/B arm framework for strategy experiments.

Track A: curator/treasury strategy (credit_strategy) — WIRED to execute_treasury_policy_v2
Track B: social posting strategy (engagement_mode, target_agents) — WIRED via run_main_runtime post_config
(2026-06-06: dropped DEAD levers vp_strategy/target_selection/post_timing — see arm-space note below)

Main Agent calls run_cycle() each heartbeat. Results written to
runtime/shared/strategy-experiment.json (atomic write).
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    # P3 (2026-06-06): wiki-knowledge priors (target_agents ← affinity, arm bias
    # ← decision outcomes). Graceful fallback to uniform-random if unavailable.
    import wiki_priors as _wp  # type: ignore
except Exception:  # pragma: no cover - keep the bandit working without priors
    _wp = None

ROOT = Path(__file__).resolve().parent.parent
SHARED_RUNTIME = ROOT / 'runtime' / 'shared'
EXPERIMENT_PATH = SHARED_RUNTIME / 'strategy-experiment.json'

# P2 (2026-06-06): dropped DEAD levers that no executor reads — they were
# "optimized" against noise with zero behavioral effect (open loop / fake learning).
# Dropped: vp_strategy, target_selection, post_timing. Dropped inert value add_lp
# (no code branch). Remaining levers are all WIRED (verify_arm_causality gate).
#   credit_strategy → execute_treasury_policy_v2.py
#   engagement_mode + target_agents → run_main_runtime post_config → execute_social_intent_v2.py
_TRACK_A_CREDIT_STRATEGIES = ['hold', 'buy_small']

_TRACK_B_ENGAGEMENT_MODES = ['none', 'reply_to_top_agents']
_TRACK_B_TARGET_AGENTS = ['foxclaw', 'clawdiai', 'alita']
# P-C (2026-06-06): content framing lever — WIRED to build_wiki_grounded_drafts_v1
# (chooses which concept section to mine: key-insight statements vs open questions).
# Evolves by TAS_social delta + decision-outcome bias (wiki_priors), like the others.
_TRACK_B_CONTENT_ANGLES = ['insight', 'open_question']

MAX_ARM_HISTORY = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _default_experiment() -> dict[str, Any]:
    return {
        'version': 'v1',
        'updated_at': _now_iso(),
        'track_a': {
            'current_arm': {'credit_strategy': 'hold'},
            'last_arm': None, 'arm_history': [], 'best_arm': None, 'epsilon': 0.2,
        },
        'track_b': {
            'current_arm': {'engagement_mode': 'reply_to_top_agents', 'target_agents': list(_TRACK_B_TARGET_AGENTS), 'content_angle': 'insight'},
            'last_arm': None, 'arm_history': [], 'best_arm': None, 'epsilon': 0.3,
        },
        'cycle_count': 0,
        'last_cycle_id': None,
        'coupling_alpha': 0.5,
    }


def load_experiment() -> dict[str, Any]:
    if not EXPERIMENT_PATH.exists():
        return _default_experiment()
    try:
        data = json.loads(EXPERIMENT_PATH.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return _default_experiment()
        # Migration: fill missing coupling_alpha in existing data
        if data.get('coupling_alpha') is None:
            data['coupling_alpha'] = 0.5
        return data
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
    if not history:
        return 'reinforce'
    D = 0.003  # delta threshold — increased sensitivity
    if tas_delta > D:
        return 'reinforce'
    if tas_delta < -D:
        recent = history[-3:]
        if len(recent) == 3 and all((e.get('tas_delta') or 0.0) <= 0 for e in recent):
            return 'reset'
        return 'reset'
    # abs(tas_delta) <= D: flat — count consecutive flat cycles (arm needs ~5h to prove itself)
    flat_count = 0
    for entry in reversed(history):
        if abs(entry.get('tas_delta') or 0.0) <= D:
            flat_count += 1
        else:
            break
    if flat_count >= 30:
        return 'switch_target'
    return 'reinforce'


def evaluate_track_b(exp, tas_social_delta, creator_reward_usd, posts_count, curators_attracted):
    track = exp.get('track_b') or {}
    history = track.get('arm_history') or []
    if not history:
        return 'reinforce'
    D = 0.003  # delta threshold — increased sensitivity
    if tas_social_delta > D:
        return 'reinforce'
    if tas_social_delta < -D:
        return 'switch_engagement'
    # abs(tas_social_delta) <= D: flat — count consecutive flat cycles (arm needs ~5h to prove itself)
    flat_count = 0
    for entry in reversed(history):
        if abs(entry.get('tas_social_delta') or 0.0) <= D:
            flat_count += 1
        else:
            break
    if flat_count >= 30:
        return 'switch_engagement'
    return 'reinforce'


def _target_agents() -> list:
    """Top agents by author affinity (wiki prior); falls back to the static list."""
    if _wp is not None:
        try:
            return _wp.top_affinity_agents(3, fallback=_TRACK_B_TARGET_AGENTS)
        except Exception:
            pass
    return list(_TRACK_B_TARGET_AGENTS)


def _biased_choice(values, *, agent=None, kind=None):
    """Wiki-decision-outcome-weighted pick; uniform random if priors unavailable."""
    if _wp is not None:
        try:
            return _wp.weighted_choice(values, _wp.decision_outcome_weights(values, agent=agent, kind=kind))
        except Exception:
            pass
    return random.choice(values)


def _random_arm_a():
    return {'credit_strategy': _biased_choice(_TRACK_A_CREDIT_STRATEGIES, agent='trader')}


def _random_arm_b():
    return {'engagement_mode': _biased_choice(_TRACK_B_ENGAGEMENT_MODES, agent='bookmarker'),
            'target_agents': _target_agents(),
            'content_angle': _biased_choice(_TRACK_B_CONTENT_ANGLES, agent='bookmarker')}


def select_next_arm_a(exp, verdict):
    track = exp.get('track_a') or {}
    current = dict(track.get('current_arm') or {})
    epsilon = float(track.get('epsilon') or 0.2)
    history = track.get('arm_history') or []
    if verdict == 'reinforce':
        return _random_arm_a() if random.random() < epsilon else dict(current)
    elif verdict == 'switch_target':
        new_arm = dict(current)
        credit_options = [c for c in _TRACK_A_CREDIT_STRATEGIES if c != current.get('credit_strategy')]
        new_arm['credit_strategy'] = random.choice(credit_options) if credit_options else random.choice(_TRACK_A_CREDIT_STRATEGIES)
        return new_arm
    elif verdict == 'reset':
        recent_arms = [tuple(sorted(e.get('arm', {}).items())) for e in history[-5:]]
        for _ in range(20):
            candidate = _random_arm_a()
            candidate_key = tuple(sorted(candidate.items()))
            if candidate_key not in recent_arms:
                return candidate
        return _random_arm_a()
    return _random_arm_a()


def select_next_arm_b(exp, verdict):
    track = exp.get('track_b') or {}
    current = dict(track.get('current_arm') or {})
    epsilon = float(track.get('epsilon') or 0.2)
    if verdict == 'reinforce':
        return _random_arm_b() if random.random() < epsilon else dict(current)
    # Any switch verdict flips engagement_mode — the only live Track-B lever after
    # post_timing was dropped (no scheduler consumer). target_agents stays fixed.
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

    # Phase 2: only evaluate and potentially switch arms every 3rd cycle,
    # giving each arm time to accumulate measurable TAS delta.
    _cycle_num = exp.get('cycle_count', 0)
    if _cycle_num > 0 and _cycle_num % 3 != 0:
        # Middle cycles: append current data but keep same arms, no evaluation
        track_a.setdefault('arm_history', []).append({
            'arm': dict(track_a.get('current_arm') or {}),
            'curator_reward_per_vp': round(curator_reward_usd / max(vp_spent, 1.0), 6),
            'tas_delta': round(tas_delta, 4), 'verdict': 'hold', 'cycle_id': cycle_id,
        })
        track_b.setdefault('arm_history', []).append({
            'arm': dict(track_b.get('current_arm') or {}),
            'creator_reward_per_post': round(creator_reward_usd / max(posts_count, 1), 6),
            'curators_attracted': curators_attracted,
            'tas_social_delta': round(tas_social_delta, 4), 'verdict': 'hold', 'cycle_id': cycle_id,
        })
        exp['cycle_count'] = _cycle_num + 1
        exp['last_cycle_id'] = cycle_id
        save_experiment(exp)
        return {'track_a': track_a['current_arm'], 'track_b': track_b['current_arm'],
                'verdict_a': 'hold', 'verdict_b': 'hold', 'cycle_count': _cycle_num + 1,
                'note': 'hold-cycle-no-eval'}

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
