#!/usr/bin/env python3
"""Shared TAS_social weighting and smoothing helpers.

Design goals for v5:
- Community interaction on posts and PoB reward are the primary drivers.
- @0xNought recognition remains meaningful, but secondary.
- X reco is included as a weak tertiary quality prior.
- Output is smoothed to avoid sharp one-cycle spikes or collapses.
"""

from __future__ import annotations

import math
from typing import Any

TAS_SOCIAL_VERSION = 'v5'

COMMUNITY_WEIGHT = 0.45
POB_WEIGHT = 0.35
ALIGN_WEIGHT = 0.15
XRECO_WEIGHT = 0.05

COMMUNITY_CAP = 5.0
POB_CAP = 5.0
ALIGN_CAP = 5.0
XRECO_CAP = 5.0

COMMUNITY_INTERACTION_BASELINE = 40.0
POB_REWARD_BASELINE_USD = 5.0

SMOOTH_ALPHA_UP = 0.35
SMOOTH_ALPHA_DOWN = 0.2
SMOOTH_MAX_STEP_UP = 0.55
SMOOTH_MAX_STEP_DOWN = 0.4


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def score_from_log_curve(value: float, baseline: float, cap: float = 5.0) -> float:
    """Map a non-negative raw metric to 0..cap with softer log growth."""
    raw = max(0.0, float(value))
    base = max(1e-6, float(baseline))
    if raw <= 0:
        return 0.0
    return clamp(cap * math.log1p(raw) / math.log1p(base), 0.0, cap)


def community_score_from_interactions(total_interactions: float) -> float:
    return score_from_log_curve(total_interactions, COMMUNITY_INTERACTION_BASELINE, COMMUNITY_CAP)


def pob_score_from_reward_usd(pob_reward_usd: float) -> float:
    return score_from_log_curve(pob_reward_usd, POB_REWARD_BASELINE_USD, POB_CAP)


def xreco_score_from_value(xreco_value: Any) -> float:
    return clamp(safe_float(xreco_value, 0.0), 0.0, XRECO_CAP)


def compute_raw_tas_social(
    *,
    community_score: float,
    pob_score: float,
    align_score: float,
    xreco_score: float,
) -> tuple[float, dict[str, float]]:
    components = {
        'community_component': COMMUNITY_WEIGHT * clamp(community_score, 0.0, COMMUNITY_CAP),
        'pob_component': POB_WEIGHT * clamp(pob_score, 0.0, POB_CAP),
        'align_component': ALIGN_WEIGHT * clamp(align_score, 0.0, ALIGN_CAP),
        'xreco_component': XRECO_WEIGHT * clamp(xreco_score, 0.0, XRECO_CAP),
    }
    raw_value = clamp(sum(components.values()), 0.0, 5.0)
    return raw_value, components


def smooth_tas_social(raw_value: float, previous_value: Any) -> tuple[float, dict[str, Any]]:
    prev = safe_float(previous_value, default=-1.0)
    raw = clamp(raw_value, 0.0, 5.0)
    if prev < 0:
        return raw, {
            'method': 'bootstrap',
            'previous_value': None,
            'raw_value': round(raw, 4),
            'smoothed_value': round(raw, 4),
            'alpha': None,
            'delta_cap': None,
        }

    delta = raw - prev
    if delta >= 0:
        alpha = SMOOTH_ALPHA_UP
        delta_cap = SMOOTH_MAX_STEP_UP
    else:
        alpha = SMOOTH_ALPHA_DOWN
        delta_cap = SMOOTH_MAX_STEP_DOWN
    proposed = prev + delta * alpha
    limited_delta = clamp(proposed - prev, -delta_cap, delta_cap)
    smoothed = clamp(prev + limited_delta, 0.0, 5.0)
    return smoothed, {
        'method': 'ema_bounded',
        'previous_value': round(prev, 4),
        'raw_value': round(raw, 4),
        'smoothed_value': round(smoothed, 4),
        'alpha': alpha,
        'delta_cap': delta_cap,
    }


def tas_social_formula_string() -> str:
    return (
        'TAS_social = smooth(min(5.0, '
        f'{COMMUNITY_WEIGHT}×community + '
        f'{POB_WEIGHT}×pob + '
        f'{ALIGN_WEIGHT}×align + '
        f'{XRECO_WEIGHT}×xreco))'
    )
