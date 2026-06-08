#!/usr/bin/env python3
"""Normalize strategy-experiment.json to v2 schema.

Injects: experiment_metadata, parameter_space, pruning_policy.
Truncates arm_history to most recent 30 entries (FIFO).
Uses atomic write (tmpfile + os.replace).

P2-remaining task — 2026-04-09
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from agency_paths import MAIN_WS

FILE = (MAIN_WS / 'runtime' / 'shared' / 'strategy-experiment.json')
MAX_ARM_HISTORY = 30


def normalize() -> None:
    data = json.loads(FILE.read_text(encoding='utf-8'))

    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # 1. Version upgrade
    data['version'] = 'v2'
    data['schema_version'] = 'v2'

    # 2. experiment_metadata
    data['experiment_metadata'] = {
        'experiment_id': 'strategy-exp-v2',
        'schema_version': 'v2',
        'normalized_at': now_iso,
        'target_metric': 'TAS',
        'sub_metrics': ['TAS_social', 'TAS_trade'],
        'design_notes': (
            'AutoResearch paradigm: each heartbeat = one experiment. '
            'Keep arm if TAS improves, discard if declines.'
        ),
    }

    # 3. parameter_space
    data['parameter_space'] = {
        'track_a': {
            'description': 'trader 行为参数空间（执行维度）',
            'dimensions': {
                'credit_strategy': {
                    'type': 'categorical',
                    'values': ['add_lp', 'buy_only', 'stake_only', 'claim_first', 'hold'],
                    'current': (data.get('track_a', {}).get('current_arm', {}).get('credit_strategy', 'add_lp')),
                    'notes': 'P1 新增：stake_only / claim_first',
                },
                'vp_strategy': {
                    'type': 'categorical',
                    'values': ['aggressive', 'conservative', 'adaptive'],
                    'current': (data.get('track_a', {}).get('current_arm', {}).get('vp_strategy', 'aggressive')),
                },
                'target_selection': {
                    'type': 'categorical',
                    'values': ['any', 'heat_rising_only', 'top3_trending', 'community_aligned'],
                    'current': (data.get('track_a', {}).get('current_arm', {}).get('target_selection', 'any')),
                    'notes': 'P1 新增：heat_rising_only / community_aligned（依赖 community-heat.json）',
                },
                'heat_signal_weight': {
                    'type': 'float',
                    'range': [0.0, 1.0],
                    'default': 0.5,
                    'current': 0.5,
                    'notes': 'P1 新增：community-heat 信号在 trader 决策中的权重（α=0 表示忽略热度信号）',
                },
                'claim_threshold_usd': {
                    'type': 'float',
                    'range': [1.0, 10.0],
                    'default': 2.0,
                    'current': 2.0,
                    'notes': 'P0 改为 $2，P1 耦合协议可覆盖',
                },
            },
        },
        'track_b': {
            'description': 'bookmarker 行为参数空间（内容/社交维度）',
            'dimensions': {
                'post_timing': {
                    'type': 'categorical',
                    'values': ['post_sync', 'post_async', 'post_peak_hours'],
                    'current': (data.get('track_b', {}).get('current_arm', {}).get('post_timing', 'post_sync')),
                },
                'engagement_mode': {
                    'type': 'categorical',
                    'values': ['reply_to_top_agents', 'curate_only', 'post_and_curate', 'reply_and_curate'],
                    'current': (data.get('track_b', {}).get('current_arm', {}).get('engagement_mode', 'reply_to_top_agents')),
                },
                'target_agents': {
                    'type': 'list',
                    'valid_pool': ['foxclaw', 'clawdiai', 'alita', 'clawdbot', '0xNought'],
                    'current': (data.get('track_b', {}).get('current_arm', {}).get('target_agents', ['foxclaw', 'clawdiai', 'alita'])),
                    'notes': '可以是空列表（表示不定向回复）',
                },
                'pob_weight': {
                    'type': 'float',
                    'range': [0.0, 0.5],
                    'default': 0.3,
                    'current': 0.3,
                    'notes': 'P0 新增：pob_reward_score 在 TAS_social 中的权重（0.5×align + 0.2×community + 0.3×pob）',
                },
                'align_weight': {
                    'type': 'float',
                    'range': [0.3, 0.7],
                    'default': 0.5,
                    'current': 0.5,
                    'notes': 'P0 新增：align_score 权重',
                },
            },
        },
        'cross_agent': {
            'description': '跨 Agent 耦合参数（P1 新增）',
            'dimensions': {
                'coupling_alpha': {
                    'type': 'float',
                    'range': [0.0, 1.0],
                    'default': 0.5,
                    'current': 0.5,
                    'notes': '0=完全独立，0.5=中度耦合，1.0=完全协同（trader 买哪里 bookmarker 就发那里）',
                },
            },
        },
    }

    # 4. pruning_policy
    data['pruning_policy'] = {
        'arm_history_max': MAX_ARM_HISTORY,
        'pruning_method': 'FIFO',
        'keep_rule': 'TAS_delta > 0 → reinforce; TAS_delta < 0 → discard next cycle; TAS_delta == 0 → conservative_explore',
        'discard_rule': '3 consecutive flat/decline cycles → force arm switch',
        'notes': 'arm_history 超过 max_length 时，从头部裁剪最旧的条目',
    }

    # 5. Truncate arm_history (FIFO, keep most recent 30)
    for track in ('track_a', 'track_b'):
        if track in data and 'arm_history' in data[track]:
            before = len(data[track]['arm_history'])
            data[track]['arm_history'] = data[track]['arm_history'][-MAX_ARM_HISTORY:]
            after = len(data[track]['arm_history'])
            if before != after:
                print(f'{track}: arm_history truncated {before} → {after}')

    # 6. Atomic write
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w', dir=FILE.parent, suffix='.tmp', delete=False, encoding='utf-8'
    ) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        tmpname = f.name
    os.replace(tmpname, FILE)

    print(f'normalized OK — version: {data["version"]}')
    print(f'  track_a arm_history: {len(data["track_a"]["arm_history"])}')
    print(f'  track_b arm_history: {len(data["track_b"]["arm_history"])}')
    print(f'  has parameter_space: {"parameter_space" in data}')
    print(f'  has pruning_policy: {"pruning_policy" in data}')
    print(f'  coupling_alpha: {data["parameter_space"]["cross_agent"]["dimensions"]["coupling_alpha"]["current"]}')


if __name__ == '__main__':
    normalize()
