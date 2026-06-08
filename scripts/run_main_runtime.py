#!/usr/bin/env python3
"""Native runtime-first main cycle for V2.

This replaces the earlier main projection bridge as the preferred way to build:
- runtime/main/latest.json
- runtime/main/last-decision.json
- runtime/main/tas-latest.json
- runtime/main/social-intent.json
- runtime/main/treasury-policy.json
- runtime/main/runtime-health.json

Inputs are runtime-first, with narrow legacy fallback only where a native runtime
value does not yet exist.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from runtime_utils import (
    analyze_social_action_selection,
    atomic_write_json,
    compute_daily_consumption,
    compute_main_mode,
    gate_allows_mode,
    normalize_status,
    parse_dt,
    read_json,
    recent_executed_target_keys,
    recent_noop_curate_target_keys,
)

ROOT = Path(os.environ.get("OPENCLAW_WORKSPACE") or str(Path.home() / ".openclaw" / "workspace"))
RUNTIME = ROOT / 'runtime'


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def infer_tas_status(values: list[float | None]) -> str:
    if all(v is None for v in values):
        return 'blocked'
    if any(v is None for v in values):
        return 'partial'
    return 'ok'


def safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def _mode_rank(mode: str) -> int:
    order = {
        'blocked-runtime': 0,
        'conservative': 1,
        'vp-flush': 2,
        'vp-drain': 3,
        'active': 4,
        'mid-active': 5,
        'super-active': 6,
    }
    return order.get(str(mode or ''), -1)


def _status_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(v) for v in value if v is not None]
        if items:
            return items
    if value is None:
        return list(default)
    return [str(value)]


def _social_policy_for_mode(mode: str) -> dict[str, Any]:
    if mode == 'blocked-runtime':
        return {
            'enabled': False,
            'min_mode': 'blocked-runtime',
            'require_bookmarker_status_in': ['ok'],
            'require_candidates': True,
            'require_drafts': True,
            'breaker_enabled': True,
            'cooldown_hours': 24,
            'max_total_actions': 0,
            'intent_ttl_minutes': 240,
            'action_mix_order': ['post', 'reply', 'curate', 'like'],
            'max_per_type': {'post': 0, 'reply': 0, 'curate': 0, 'like': 0},
            'posture': 'disabled',
        }
    if mode == 'conservative':
        return {
            'enabled': False,
            'min_mode': 'conservative',
            'require_bookmarker_status_in': ['ok'],
            'require_candidates': True,
            'require_drafts': True,
            'breaker_enabled': True,
            'cooldown_hours': 24,
            'max_total_actions': 0,
            'intent_ttl_minutes': 240,
            'action_mix_order': ['post', 'reply', 'curate', 'like'],
            'max_per_type': {'post': 0, 'reply': 0, 'curate': 0, 'like': 0},
            'posture': 'hold-by-default',
        }
    if mode == 'vp-flush':
        return {
            'enabled': True,
            'min_mode': 'vp-flush',
            'require_bookmarker_status_in': ['ok'],
            'require_candidates': True,
            'require_drafts': True,
            'breaker_enabled': True,
            'cooldown_hours': 24,
            'max_total_actions': 12,
            'intent_ttl_minutes': 240,
            'action_mix_order': ['curate', 'reply', 'post', 'like'],
            'max_per_type': {'post': 2, 'reply': 3, 'curate': 8, 'like': 5},
            'posture': 'active-vp-flush',
        }
    if mode == 'vp-drain':
        return {
            'enabled': True,
            'min_mode': 'vp-drain',
            'require_bookmarker_status_in': ['ok'],
            'require_candidates': True,
            'require_drafts': True,
            'breaker_enabled': True,
            'cooldown_hours': 24,
            'max_total_actions': 1,
            'intent_ttl_minutes': 240,
            'action_mix_order': ['curate', 'reply', 'post', 'like'],
            'max_per_type': {'post': 1, 'reply': 1, 'curate': 1, 'like': 1},
            'posture': 'limited-vp-drain',
        }
    if mode == 'super-active':
        return {
            'enabled': True,
            'min_mode': 'active',
            'require_bookmarker_status_in': ['ok'],
            'require_candidates': True,
            'require_drafts': True,
            'breaker_enabled': True,
            'cooldown_hours': 24,
            'max_total_actions': 15,
            'intent_ttl_minutes': 240,
            'action_mix_order': ['post', 'reply', 'curate', 'like'],
            'max_per_type': {'post': 2, 'reply': 5, 'curate': 8, 'like': 8},
            'posture': 'broad-super-active',
        }
    if mode in ('active', 'mid-active'):
        return {
            'enabled': True,
            'min_mode': 'active',
            'require_bookmarker_status_in': ['ok'],
            'require_candidates': True,
            'require_drafts': True,
            'breaker_enabled': True,
            'cooldown_hours': 24,
            'max_total_actions': 12,
            'intent_ttl_minutes': 240,
            'action_mix_order': ['post', 'reply', 'curate', 'like'],
            'max_per_type': {'post': 2, 'reply': 3, 'curate': 8, 'like': 5},
            'posture': 'broad-active',
        }
    return _social_policy_for_mode('conservative')


def build_dispatch_config(
    *,
    cycle_id: str,
    strategy_id: str,
    generated_at: str,
    mode: str,
    op: float | None,
    vp: float | None,
    tas_total: float | None,
    tas_social: float | None,
    tas_trade: float | None,
    tas_status: str,
    strategy_action: str,
    planning_focus: str,
    bookmarker_status: str,
    trader_status: str,
    claimable_usd: float | None,
) -> dict[str, Any]:
    social = _social_policy_for_mode(mode)
    trader_status_allow = ['ok', 'partial']
    claim_threshold_usd = 2.0
    claim_threshold_passed = claimable_usd is not None and claimable_usd >= claim_threshold_usd
    # Phase 3: Read strategy_level from treasury-strategy.json (written above in Phase 3 block).
    _trade_strat = read_json(RUNTIME / 'main' / 'treasury-strategy.json') or {}
    _trade_env = _trade_strat.get('resource_envelope') or {}
    treasury_allow_claim = bool(_trade_env.get('allow_claim', claim_threshold_passed))
    treasury_allow_trade = bool(_trade_env.get('auto_trade_enabled', False))
    treasury_enabled = (
        _mode_rank(mode) > _mode_rank('blocked-runtime')
        and trader_status in trader_status_allow
        and (treasury_allow_claim or treasury_allow_trade)
    )
    treasury_reason = 'claim-first posture opened' if treasury_enabled else 'treasury paused'
    if _mode_rank(mode) <= _mode_rank('blocked-runtime'):
        treasury_reason = 'blocked-runtime disables treasury lane'
    elif trader_status not in trader_status_allow:
        treasury_reason = f'trader status {trader_status} not in allowed set {trader_status_allow}'
    elif not treasury_allow_claim and not treasury_allow_trade:
        treasury_reason = f'treasury both disabled: strategy={_trade_strat.get("strategy_level","?")}, claimable check = {_trade_env.get("allow_claim")}, trade = {_trade_env.get("auto_trade_enabled")}'
    social_reason = 'social disabled by mode posture'
    if social.get('enabled'):
        social_reason = f"{mode} mode enables social lane with posture {social.get('posture')}"

    return {
        'version': 'v1',
        'config_kind': 'dispatch-config',
        'cycle_id': cycle_id,
        'strategy_id': strategy_id,
        'generated_at': generated_at,
        'generated_by': 'main',
        'source_class': 'main-owned',
        'mode_context': {
            'current_mode': mode,
            'resolved_policy_mode': mode,
            'strategy_action': strategy_action,
            'planning_focus': planning_focus,
            'op': op,
            'vp': vp,
            'tas_total': tas_total,
            'tas_social': tas_social,
            'tas_trade': tas_trade,
            'tas_status': tas_status,
        },
        'social': {
            'enabled': bool(social.get('enabled')),
            'min_mode': str(social.get('min_mode')),
            'require_bookmarker_status_in': _status_list(social.get('require_bookmarker_status_in'), ['ok']),
            'require_candidates': bool(social.get('require_candidates', True)),
            'require_drafts': bool(social.get('require_drafts', True)),
            'breaker_enabled': bool(social.get('breaker_enabled', True)),
            'cooldown_hours': int(social.get('cooldown_hours', 24) or 24),
            'max_total_actions': int(social.get('max_total_actions', 0) or 0),
            'intent_ttl_minutes': max(240, int(social.get('intent_ttl_minutes', 240) or 240)),
            'action_mix_order': list(social.get('action_mix_order') or ['post', 'reply', 'curate', 'like']),
            'max_per_type': dict(social.get('max_per_type') or {'post': 0, 'reply': 0, 'curate': 0, 'like': 0}),
        },
        'treasury': {
            'enabled': treasury_enabled,
            'require_trader_status_in': trader_status_allow,
            'policy_ttl_minutes': 30,
            'min_claimable_usd': claim_threshold_usd,
            'allow_claim': treasury_allow_claim,
            'allow_trade': treasury_allow_trade,
            'trading': {
                # Phase 3: trading params driven by treasury-strategy resource_envelope
                'max_budget_usd': float(_trade_env.get('max_budget_usd', 0.0)),
                'max_position_change_pct': 0.15,
                'max_trades_per_cycle': 2 if treasury_allow_trade else 0,
                'max_trades_per_day': 5 if treasury_allow_trade else 0,
                'max_sells_per_day': 2 if treasury_allow_trade else 0,
                'max_same_tick_trades_per_day': 2 if treasury_allow_trade else 0,
                'min_bnb_reserve': 0.005,
                'max_sell_usd': float(_trade_env.get('max_budget_usd', 0.0)),
                'allowed_actions': ['trade', 'claim'] if treasury_allow_trade else ['claim'],
                'allowed_ticks': ['TagClaw', 'BUIDL', 'TTAI'],
                'sell_triggers': {'stop_loss_pct': -15, 'take_profit_pct': 30},
            },
        },
        'decision_trace': {
            'policy_source': 'main runtime policy engine',
            'resolved_policy_mode': mode,
            'strategy_action': strategy_action,
            'planning_focus': planning_focus,
            'bookmarker_status_seen': bookmarker_status,
            'trader_status_seen': trader_status,
            'tas_status_seen': tas_status,
            'claimable_usd_seen': claimable_usd,
            'claim_threshold_usd': claim_threshold_usd,
            'claim_threshold_passed': claim_threshold_passed,
            'social_enabled_policy': bool(social.get('enabled')),
            'social_min_mode': str(social.get('min_mode')),
            'treasury_enabled_policy': treasury_enabled,
            'treasury_allow_claim': treasury_allow_claim,
            'treasury_allow_trade': treasury_allow_trade,
            'social_lane_reason': social_reason,
            'treasury_lane_reason': treasury_reason,
        },
    }


def build_dispatch_summary(
    dispatch_config: dict[str, Any],
    *,
    social_gate_authorized: bool,
    social_selection_outcome: str,
    social_final_authorized: bool,
    treasury_gate_authorized: bool,
    treasury_selection_outcome: str,
    treasury_final_authorized: bool,
) -> dict[str, Any]:
    social = (dispatch_config.get('social') or {}) if isinstance(dispatch_config, dict) else {}
    treasury = (dispatch_config.get('treasury') or {}) if isinstance(dispatch_config, dict) else {}
    mode_context = (dispatch_config.get('mode_context') or {}) if isinstance(dispatch_config, dict) else {}
    return {
        'generated_at': dispatch_config.get('generated_at'),
        'resolved_policy_mode': mode_context.get('resolved_policy_mode') or mode_context.get('current_mode'),
        'social_enabled': bool(social.get('enabled', False)),
        'social_min_mode': social.get('min_mode'),
        'social_gate_authorized': social_gate_authorized,
        'social_selection_outcome': social_selection_outcome,
        'social_final_authorized': social_final_authorized,
        'treasury_enabled': bool(treasury.get('enabled', False)),
        'treasury_allow_claim': bool(treasury.get('allow_claim', False)),
        'treasury_allow_trade': bool(treasury.get('allow_trade', False)),
        'treasury_gate_authorized': treasury_gate_authorized,
        'treasury_selection_outcome': treasury_selection_outcome,
        'treasury_final_authorized': treasury_final_authorized,
    }


def update_runtime_status(
    *,
    main_status: str,
    generated_at: str,
    dispatch_summary: dict[str, Any],
    social_lane_state: str,
    treasury_lane_state: str,
) -> None:
    rs_path = RUNTIME / 'shared' / 'runtime-status.json'
    try:
        runtime_status = json.loads(rs_path.read_text(encoding='utf-8')) if rs_path.exists() else {}
    except Exception:
        runtime_status = {}
    runtime_status.setdefault('schema', 'runtime-status.v1')
    runtime_status['main'] = {
        'status': main_status,
        'updated_at': generated_at,
        'last_heartbeat': generated_at,
    }
    runtime_status.setdefault('lanes', {})
    runtime_status['lanes']['social'] = {'state': social_lane_state}
    runtime_status['lanes']['treasury'] = {'state': treasury_lane_state}
    runtime_status['dispatch'] = dict(dispatch_summary)
    runtime_status.pop('bootstrap', None)
    atomic_write_json(rs_path, runtime_status)


def atomic_append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + '\n'
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        tmp.write(existing + line)
        temp_name = tmp.name
    os.replace(temp_name, path)


def _last_good_tas_trade(hist_path: Path) -> float | None:
    """Read the last entry with status 'ok' from TAS history and return its tas_trade value."""
    if not hist_path.exists():
        return None
    try:
        lines = hist_path.read_text(encoding='utf-8').strip().splitlines()
        for line in reversed(lines):
            entry = json.loads(line)
            if entry.get('status') == 'ok' and entry.get('tas_trade') is not None:
                return float(entry['tas_trade'])
    except Exception:
        pass
    return None


_TAS_TRADE_DROP_THRESHOLD = 0.4  # flag as degraded if value drops below 40% of last good


# ── Reward-Aware Community Selector (P3 2026-04-11) ─────────────────────────
# Deterministic tick scoring for post-community choice.
# Prefers communities with real recent PoB / claimable reward evidence.
# Degrades toward TagClaw / BUIDL / TTAI; deprioritizes CLAW under uncertainty.

# Base priority when no reward evidence exists (higher = preferred)
_TICK_BASE_PRIORITY: dict[str, int] = {
    'TagClaw': 100,
    'BUIDL': 90,
    'TTAI': 80,
    'AGENT': 50,
    'CLAW': 10,  # explicitly low — no recent reward evidence
}
_TICK_DEFAULT_PRIORITY = 30  # unknown ticks


def select_reward_aware_tick(
    reward_status: dict[str, Any],
    wiki_trending_ticks: list[str] | None = None,
    current_tick: str | None = None,
) -> dict[str, Any]:
    """Score ticks by reward evidence and return the best community for posting.

    Returns dict with keys: tick, score, reason, scores (full breakdown).

    Evidence sources (deterministic proxies):
      1. reward_status.claimable[] — per-tick claimable_amount + reward_value_usd
      2. wiki_trending_ticks — platform trending signal (weak tiebreaker)
      3. _TICK_BASE_PRIORITY — hardcoded policy: TagClaw/BUIDL/TTAI >> CLAW

    Limitations:
      - No per-post reward attribution (exact post → reward mapping unavailable).
      - claimable[] only reflects snapshot at last trader cycle, not real-time.
      - Does not account for reward velocity, only presence/absence + value.
    """
    trending = wiki_trending_ticks or []
    claimable_list = reward_status.get('claimable') or [] if isinstance(reward_status, dict) else []

    # Build per-tick reward evidence map
    reward_evidence: dict[str, float] = {}
    for item in claimable_list:
        if not isinstance(item, dict):
            continue
        tick = str(item.get('tick') or '').strip()
        usd = safe_float(item.get('reward_value_usd')) or 0.0
        if tick:
            reward_evidence[tick] = reward_evidence.get(tick, 0.0) + usd

    # Candidate ticks: union of base-priority ticks, reward-evidence ticks,
    # trending ticks, and the current tick if provided
    candidates = set(_TICK_BASE_PRIORITY.keys())
    candidates.update(reward_evidence.keys())
    candidates.update(trending[:5])
    if current_tick:
        candidates.add(current_tick)

    scores: dict[str, dict[str, Any]] = {}
    for tick in candidates:
        base = _TICK_BASE_PRIORITY.get(tick, _TICK_DEFAULT_PRIORITY)
        # Reward bonus: 50 points per $1 of claimable reward (capped at 200)
        reward_usd = reward_evidence.get(tick, 0.0)
        reward_bonus = min(200, int(reward_usd * 50))
        # Trending bonus: +10 if in top-5 trending
        trending_bonus = 10 if tick in trending[:5] else 0
        total = base + reward_bonus + trending_bonus
        reason_parts = [f'base={base}']
        if reward_bonus > 0:
            reason_parts.append(f'reward=${reward_usd:.2f}(+{reward_bonus})')
        if trending_bonus > 0:
            reason_parts.append(f'trending(+{trending_bonus})')
        scores[tick] = {
            'score': total,
            'base': base,
            'reward_usd': round(reward_usd, 4),
            'reward_bonus': reward_bonus,
            'trending_bonus': trending_bonus,
            'reason': ', '.join(reason_parts),
        }

    # Sort by score descending, then alphabetically for determinism
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1]['score'], kv[0]))
    best_tick = ranked[0][0] if ranked else 'BUIDL'
    best_info = scores.get(best_tick, {})

    return {
        'tick': best_tick,
        'score': best_info.get('score', 0),
        'reason': f"reward-aware-selector: {best_info.get('reason', 'default')}",
        'scores': {k: v for k, v in ranked[:6]},  # top-6 for trace
    }


def _append_tas_history(runtime: Path, cycle_id: str, tas_total: Any, tas_social: Any, tas_trade: Any, status: str, tas_xreco: Any = None) -> None:
    """Append one TAS data point to runtime/shared/tas-history.jsonl for dashboard charting.

    Guards against degraded trader snapshots: if tas_trade drops by >60% from the
    last known-good value, the entry is written with status='degraded' so dashboards
    can filter it out.

    P1 2026-04-10: entries carry ``history_eligible`` — only ``ok`` entries are
    canonical trend points.  Partial/degraded entries are preserved for
    observability but must not distort canonical trend math or charts.

    P1 gate (full): entry is degraded if price_visibility != ok, measurement_quality != ok,
    or portfolio_usd is missing — not just on anomalous numeric drop.
    """
    hist_path = runtime / 'shared' / 'tas-history.jsonl'

    # P1 gate: read trader measurement quality to enforce full preconditions
    effective_status = status
    mq = read_json(runtime / 'trader' / 'measurement-quality.json') or {}
    tt = read_json(runtime / 'trader' / 'tas-trade.json') or {}
    price_visibility = mq.get('price_visibility', 'unknown')
    mq_status = mq.get('overall_status', 'unknown')
    portfolio_usd = tt.get('portfolio_usd_raw')
    if price_visibility != 'ok' or mq_status != 'ok' or portfolio_usd is None:
        effective_status = 'degraded'

    # Detect degraded tas_trade via anomalous drop from last good value
    if effective_status == 'ok' and tas_trade is not None:
        prev_trade = _last_good_tas_trade(hist_path)
        if prev_trade is not None and prev_trade > 0:
            ratio = float(tas_trade) / prev_trade
            if ratio < _TAS_TRADE_DROP_THRESHOLD:
                effective_status = 'degraded'

    # P1: only 'ok' entries participate in canonical history / trend math
    history_eligible = effective_status == 'ok'

    strategy_exp = read_json(runtime / 'shared' / 'strategy-experiment.json') or {}
    cycle_count = strategy_exp.get('cycle_count')
    entry_obj = {
        'ts': cycle_id,
        'tas_total': tas_total,
        'tas_social': tas_social,
        'tas_trade': tas_trade,
        'tas_xreco': tas_xreco,
        'status': effective_status,
        'history_eligible': history_eligible,
    }
    if isinstance(cycle_count, int):
        entry_obj['cycle_count'] = cycle_count
    entry = json.dumps(entry_obj, ensure_ascii=False)
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    with hist_path.open('a', encoding='utf-8') as f:
        f.write(entry + '\n')


def load_wiki_execution_brief() -> dict | None:
    """读取 runtime/shared/wiki-execution-brief.json，校验新鲜度。

    返回 dict（若新鲜），或 None（若不存在/已过期/解析失败）。
    新鲜度判断：valid_until 字段 > 当前 UTC 时间。
    """
    brief_path = RUNTIME / 'shared' / 'wiki-execution-brief.json'
    data = read_json(brief_path)
    if not data:
        return None
    valid_until_str = data.get('valid_until')
    if valid_until_str:
        try:
            valid_until = datetime.fromisoformat(valid_until_str.replace('Z', '+00:00'))
            if valid_until < datetime.now(timezone.utc):
                return None  # 已过期
        except Exception:
            pass  # 解析失败则视为有效
    return data


WIKI_PLATFORM_RAW = ROOT / 'wiki' / 'tagclaw-platform' / 'raw'


def load_wiki_platform_context() -> dict:
    """Read wiki/tagclaw-platform/raw/ snapshots and return a summary dict.

    Returns a dict with trending_ticks, top_marketcap_ticks, top_communities,
    platform_vp_ref, platform_op_ref, snapshot_age_hours, and stale flag.
    Gracefully degrades: missing/old files → stale=True, empty lists.
    """
    result: dict[str, Any] = {
        'trending_ticks': [],
        'top_marketcap_ticks': [],
        'top_communities': [],
        'platform_vp_ref': None,
        'platform_op_ref': None,
        'snapshot_age_hours': None,
        'stale': True,
    }

    # Helper: read a platform raw JSON file
    def _read_platform(name: str) -> dict | None:
        p = WIKI_PLATFORM_RAW / name
        return read_json(p) if p.exists() else None

    # Determine freshness from any file's _meta.fetched_at
    fetched_at_str = None
    for probe_file in ('ticks_trending.json', 'me.json'):
        data = _read_platform(probe_file)
        if data and isinstance(data.get('_meta'), dict):
            fetched_at_str = data['_meta'].get('fetched_at')
            if fetched_at_str:
                break

    stale = True
    snapshot_age_hours = None
    if fetched_at_str:
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str.replace('Z', '+00:00'))
            age = datetime.now(timezone.utc) - fetched_at
            snapshot_age_hours = round(age.total_seconds() / 3600, 1)
            stale = snapshot_age_hours > 168  # 7 days
        except Exception:
            pass

    result['snapshot_age_hours'] = snapshot_age_hours
    result['stale'] = stale

    # Trending ticks
    trending_data = _read_platform('ticks_trending.json')
    if trending_data:
        ticks_list = (trending_data.get('data') or {}).get('ticks') or []
        result['trending_ticks'] = [t.get('tick') for t in ticks_list[:5] if t.get('tick')]

    # Marketcap ticks
    mc_data = _read_platform('ticks_marketcap.json')
    if mc_data:
        mc_list = (mc_data.get('data') or {}).get('ticks') or []
        result['top_marketcap_ticks'] = [t.get('tick') for t in mc_list[:5] if t.get('tick')]

    # Communities by marketcap
    comm_data = _read_platform('community_by_marketcap.json')
    if comm_data:
        comm_list = comm_data.get('data') or []
        result['top_communities'] = [c.get('name') for c in comm_list[:5] if c.get('name')]

    # Me — vp/op reference
    # The cached snapshot may carry any of the server's /me envelope shapes
    # (top-level agent, data.agent, flat data, or legacy bare dict). Probe
    # all of them rather than hard-coding one — this is the same normalization
    # rule used by adapters/tagclaw.extract_me_agent.
    me_data = _read_platform('me.json')
    if me_data:
        agent: dict = {}
        for candidate in (
            me_data.get('agent'),
            (me_data.get('data') or {}).get('agent'),
            me_data.get('data'),
            me_data,
        ):
            if isinstance(candidate, dict) and ('vp' in candidate or 'op' in candidate):
                agent = candidate
                break
        vp_val = agent.get('vp')
        op_val = agent.get('op')
        if vp_val is not None:
            result['platform_vp_ref'] = round(float(vp_val), 2)
        if op_val is not None:
            result['platform_op_ref'] = round(float(op_val), 2)

    return result


WIKI_CONCEPTS_DIR = ROOT / 'wiki' / 'concepts'
HEATMAP_PATH_MAIN = RUNTIME / 'bookmarker' / 'topic-heatmap.json'

# Reverse mapping from heatmap topic names to concept page names
_TOPIC_TO_CONCEPT: dict[str, str] = {
    'AgentInfrastructure': 'AgentEconomy',
    'AgentSwarm': 'AgentEconomy',
    'TagClaw': 'TagClaw',
    'ATOC': 'ATOC',
    'TokenEconomy': 'TokenEconomy',
    'DeSoc': 'DeSoc',
    'Misc': 'Misc',
    'Projects': 'Philosophy',
    'MarketTrading': 'MarketTrading',
}


def _load_wiki_topic_insight(topic: str) -> str | None:
    """Load the '对 TagClawX 的启示' section from wiki/concepts/ for a given heatmap topic.

    Returns first 3 lines of the section, or None.
    """
    import re as _re
    concept_name = _TOPIC_TO_CONCEPT.get(topic, topic)
    path = WIKI_CONCEPTS_DIR / f'{concept_name}.md'
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding='utf-8')
    except Exception:
        return None
    pattern = _re.compile(r'^#{1,4}\s*对\s*TagClawX.*启示', _re.MULTILINE)
    match = pattern.search(content)
    if not match:
        return None
    lines = content[match.end():].splitlines()
    results: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#') and results:
            break
        if stripped.startswith('-') or stripped.startswith('*'):
            clean = _re.sub(r'^[-*]\s*', '', stripped)
            clean = _re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
            if clean:
                results.append(clean)
        if len(results) >= 3:
            break
    return '\n'.join(results) if results else None


def _get_top_wiki_topic() -> tuple[str | None, str | None, str | None]:
    """Return (wiki_topic, wiki_insight, wiki_reason) from topic-heatmap.json.

    Composite score = heat_1m * 0.7 + community_fit * 0.3, filtered to community_fit > 0.
    """
    heatmap_data = read_json(HEATMAP_PATH_MAIN)
    if not heatmap_data:
        return None, None, None
    heat_1m: dict[str, float] = (heatmap_data.get('heatmap') or {}).get('1m') or {}
    community_fit: dict[str, float] = heatmap_data.get('community_fit_scores') or {}
    best_topic: str | None = None
    best_score: float = -1.0
    for topic, heat in heat_1m.items():
        fit = community_fit.get(topic, 0.0)
        if fit <= 0:
            continue
        score = float(heat) * 0.7 + float(fit) * 0.3
        if score > best_score:
            best_score = score
            best_topic = topic
    if not best_topic:
        return None, None, None
    insight = _load_wiki_topic_insight(best_topic)
    reason = f'wiki/{best_topic}: {insight[:120]}' if insight else None
    return best_topic, insight, reason


def build_metric_strategy_loop(
    metric_name: str,
    current_value: float | None,
    previous_value: float | None,
    current_status: str,
    previous_status: str | None = None,
    previous_strategy: str | None = None,
    previous_reason: str | None = None,
    *,
    resource_floor_unmet: bool = False,
) -> dict[str, Any]:
    previous_status = previous_status or 'unknown'
    delta = round(current_value - previous_value, 6) if current_value is not None and previous_value is not None else None
    if current_value is None or previous_value is None:
        trend = 'blocked' if ('blocked' in {current_status, previous_status}) else 'partial'
    elif abs(delta or 0.0) < 1e-9:
        trend = 'flat'
    elif (delta or 0.0) > 0:
        trend = 'improved'
    else:
        trend = 'declined'

    if trend == 'improved':
        strategy_action = 'reinforce_previous_strategy'
        planning_focus = f'{metric_name} improved; reinforce the previous cycle strategy that just worked.'
    elif trend == 'declined':
        strategy_action = 'discard_previous_strategy'
        planning_focus = f'{metric_name} declined; discard the previous cycle strategy and change approach.'
    else:
        strategy_action = 'conservative_explore'
        planning_focus = f'{metric_name} is {trend}; stay conservative and explore / repair instead of blindly repeating the old strategy.'

    # FIX-7: override conservative_explore when resource floor is unmet
    # Breaks the death loop: flat TAS → conservative → no actions → TAS stays flat.
    _resource_floor_override_applied = False
    if resource_floor_unmet and strategy_action == 'conservative_explore':
        strategy_action = 'active'
        planning_focus = (
            f'{metric_name} is {trend}; daily resource P0 floor unmet — override conservative '
            f'to active to catch up on daily OP/VP targets and break the flat-TAS death loop.'
        )
        _resource_floor_override_applied = True

    return {
        'metric': metric_name,
        'current_value': current_value,
        'previous_value': previous_value,
        'delta': delta,
        'current_status': current_status,
        'previous_status': previous_status,
        'trend': trend,
        'strategy_action': strategy_action,
        'planning_focus': planning_focus,
        'rule': {
            'improved': 'reinforce_previous_strategy',
            'declined': 'discard_previous_strategy',
            'flat_or_partial_or_blocked': 'conservative_explore',
        },
        'resource_floor_override': _resource_floor_override_applied,
        'previous_strategy': previous_strategy,
        'previous_reason': previous_reason,
    }


def build_strategy_hypothesis(target_components: list[str], mode: str, tas_status: str) -> str:
    parts: list[str] = []
    if 'tas_social' in target_components:
        parts.append('improve social quality and execution efficiency via bookmarker')
    if 'tas_trade' in target_components:
        # FIX-4: trade-focused language when TAS_trade is present (always now)
        parts.append('drive treasury yield and trade timing improvement via trader (P0 target — always included)')
    if not parts:
        parts.append('preserve current gains while exploring conservatively')
    return f"Main control-plane cycle in mode={mode} targeting {', '.join(parts)} (tas_status={tas_status})."


def synthesize_trade_drafts(
    runtime: Path,
    wiki_trending_ticks: list[str],
    mode: str,
    cycle_id: str,
) -> list[dict]:
    """[DISABLED 2026-05-26 plan-A] returns [] always.

    Previously generated up to 2 trade-focused drafts per cycle from 8
    hardcoded templates (4× market_commentary + 4× community_heat_observation).
    When the wiki-sourced draft pool ran dry these fallbacks dominated, and
    @clawdbot posted 12 near-duplicate `${tick} signal building / community
    heat index rising ...` posts in 24 h. That tanked TAS_social to 0 (no
    owner engagement on templated noise) and wasted OP budget on near-dups.

    Wiki-grounded drafts now come from `build_wiki_grounded_drafts_v1.py`
    (daily cron). If that pool runs dry the executor simply skips its tick
    — better to post nothing than another templated dup. To bring this
    function back, change the early-return to the original logic and add a
    real freshness/dedup guard.
    """
    return []
    # === legacy fallback below kept for reference; intentionally unreachable ===
    community_heat = read_json(runtime / 'shared' / 'community-heat.json') or {}
    hot_items = (community_heat.get('hot') or community_heat.get('items') or [])[:3]
    now_str = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
    drafts: list[dict] = []

    # Draft 1: market commentary on top trending tick.
    # FIX-6: randomized template variants for content diversity; hour_label removed
    # because execute_social_intent_v2 now strips [HH:MM UTC] before publishing
    # (FIX-2) and 7-day text-content dedup uses the tick field for hash uniqueness.
    _market_commentary_templates = [
        '${tick} signal building. Volume and community momentum aligning. Worth watching closely. #TagClaw #DeFi',
        '${tick} showing early momentum. On-chain activity and community engagement both picking up. #TagClaw #DeFi',
        '${tick} on the radar — volume profile and community signals converging. Not financial advice. #TagClaw #DeFi',
        '${tick} positioning is interesting here. Community activity ramping ahead of price. #TagClaw #DeFi',
    ]
    if wiki_trending_ticks:
        tick = str(wiki_trending_ticks[0])
        _tmpl = random.choice(_market_commentary_templates)
        drafts.append({
            'id': f'trade-tick-{cycle_id}',
            'type': 'post',
            'tick': tick,
            'text': _tmpl.replace('${tick}', f'${tick}'),
            'priority': 8,
            'source': 'synthesize_trade_drafts',
            'draft_type': 'market_commentary',
            'generated_at': now_str,
        })

    # Draft 2: community heat observation using hot tick or second trending tick.
    # FIX-D-2026-05-25: always prefer wiki_trending_ticks[1] for tick diversity;
    # community-heat hot tick used only when it differs from the primary tick.
    # FIX-6: randomized template variants, hour_label removed (same reason as above).
    _heat_observation_templates = [
        '${tick} community heat index rising — when communities get active before price moves, that is the signal. Not financial advice. #TagClaw',
        '${tick} community activity surging. Social signals like this tend to precede price discovery. Not financial advice. #TagClaw',
        '${tick} seeing elevated community engagement. Early social momentum is worth tracking. Not financial advice. #TagClaw',
        '${tick} community heat up. Coordinated interest often leads liquidity. Not financial advice. #TagClaw',
    ]
    heat_tick: str | None = None
    _fallback_heat_tick = str(wiki_trending_ticks[1]) if len(wiki_trending_ticks) > 1 else None
    if hot_items:
        first_hot = hot_items[0] if isinstance(hot_items[0], dict) else {}
        _candidate_heat = first_hot.get('tick')
        _primary_tick = str(wiki_trending_ticks[0]) if wiki_trending_ticks else ''
        if _candidate_heat and str(_candidate_heat) != _primary_tick:
            heat_tick = str(_candidate_heat)
        else:
            heat_tick = _fallback_heat_tick
    else:
        heat_tick = _fallback_heat_tick

    if heat_tick:
        _tmpl = random.choice(_heat_observation_templates)
        drafts.append({
            'id': f'trade-heat-{cycle_id}',
            'type': 'post',
            'tick': heat_tick,
            'text': _tmpl.replace('${tick}', f'${heat_tick}'),
            'priority': 7,
            'source': 'synthesize_trade_drafts',
            'draft_type': 'community_heat_observation',
            'generated_at': now_str,
        })

    return drafts[:2]


def build_social_trade_post_directive(runtime: Path) -> dict[str, Any] | None:
    """Resolve a trader social-trade brief into a post_directive payload.

    The brief lane is intentionally a fallback publisher: if the current social
    cycle did not already select a post, main can inject this directive so the
    brief reaches the normal execute_social_intent_v2 -> /tagclaw/post path.
    """
    brief = (
        read_json(runtime / 'trader' / 'PENDING_BRIEF.claimed.json')
        or read_json(runtime / 'trader' / 'PENDING_BRIEF.json')
        or {}
    )
    if not isinstance(brief, dict) or brief.get('status') not in (None, 'ok', 'partial'):
        return None
    candidate = brief.get('post_candidate') if isinstance(brief.get('post_candidate'), dict) else {}
    text = str(candidate.get('text') or '').strip()
    tick = str(candidate.get('tick') or '').strip()
    if text and tick:
        return {
            'text': text,
            'tick': tick,
            'reason': str(candidate.get('reason') or 'social-trade brief post'),
            'source': str(candidate.get('source') or 'social-trade-brief'),
            'draft_type': str(candidate.get('draft_type') or 'social_trade_brief'),
            'target_key': str(candidate.get('target_key') or f'tagclaw:post-brief-{tick}'),
            'brief_ref': 'runtime/trader/PENDING_BRIEF*.json',
        }
    thesis = str(brief.get('thesis') or '').strip()
    cashtags = [str(x).strip() for x in (brief.get('cashtags') or []) if str(x).strip()]
    if not thesis or not cashtags:
        return None
    tick = cashtags[0]
    cashtag_text = ' '.join(f'${tag}' for tag in cashtags[:2])
    thesis_short = thesis[:107].rstrip(' ,.;:') + '...' if len(thesis) > 110 else thesis
    text = f"Social-trade brief live. Focus: {cashtag_text}. {thesis_short} #TagClaw #DeFi".strip()
    return {
        'text': text,
        'tick': tick,
        'reason': 'social-trade brief post',
        'source': 'social-trade-brief',
        'draft_type': 'social_trade_brief',
        'target_key': f'tagclaw:post-brief-{tick}',
        'brief_ref': 'runtime/trader/PENDING_BRIEF*.json',
    }


def compute_budget_allocation(
    op: float | None,
    vp: float | None,
    mode: str,
    social_authorized: bool,
    treasury_allowed: bool,
    treasury_payload: dict[str, Any],
    warnings_count: int,
    blockers_count: int,
) -> dict[str, Any]:
    op_available = max(0.0, float(op or 0.0))
    vp_available = max(0.0, float(vp or 0.0))
    if mode == 'super-active':
        social_op_budget = 1200.0
        social_vp_budget = 150.0
    elif mode in {'mid-active', 'active'}:
        social_op_budget = 400.0 if mode == 'mid-active' else 250.0
        social_vp_budget = 18.0 if mode == 'mid-active' else 12.0
    elif mode == 'standard':
        social_op_budget = 200.0
        social_vp_budget = 8.0
    elif mode == 'conservative':
        social_op_budget = 200.0
        social_vp_budget = 8.0
    elif mode == 'vp-flush':
        # vp-flush: 积极消耗 OP/VP，目标日耗 667 OP + 67 VP
        # 给 bookmarker 所有可用的 OP（上限 800）和 VP（上限 100）
        # 主原则：资源不用就浪费，不卡严格阈值
        # 修复(2026-05-14): 之前当 op_available < 400 时给 400 OP 然后再被 min() 截断，
        # 导致 post(200 OP) 永远无法执行。现在直接给 op_available，让 executor 自行判断。
        social_op_budget = min(op_available, 800.0)
        social_vp_budget = min(vp_available, 100.0)
    elif mode == 'vp-drain':
        # vp-drain: primarily VP-curate, but allow at least 1 post if OP >= 200
        # Previously hardcoded to 0.0 which blocked all posts — fixed 2026-04-06
        social_op_budget = 200.0 if op_available >= 200 else 0.0
        social_vp_budget = 20.0
    else:
        social_op_budget = 0.0
        social_vp_budget = 0.0

    social_op_budget = min(op_available, social_op_budget) if social_authorized else 0.0
    social_vp_budget = min(vp_available, social_vp_budget) if social_authorized else 0.0
    reserve_op = max(0.0, round(op_available - social_op_budget, 6))
    reserve_vp = max(0.0, round(vp_available - social_vp_budget, 6))

    dev_budget = 1 if (warnings_count > 0 or blockers_count > 0) else 0
    risk_budget = 'medium' if treasury_allowed else ('low' if social_authorized else 'minimal')
    treasury_usd_budget = float(treasury_payload.get('max_budget_usd', 0) or 0) if treasury_allowed else 0.0

    return {
        'op_budget': round(social_op_budget, 6),
        'vp_budget': round(social_vp_budget, 6),
        'risk_budget': risk_budget,
        'dev_budget': dev_budget,
        'attention_budget': 'high' if social_authorized else ('medium' if treasury_allowed else 'low'),
        'allocations': {
            'bookmarker': {
                'lane': 'social',
                'execution_owner': 'bookmarker',
                'op_budget': round(social_op_budget, 6),
                'vp_budget': round(social_vp_budget, 6),
                'authorized': social_authorized,
            },
            'trader': {
                'lane': 'treasury',
                'execution_owner': 'trader',
                'usd_budget': round(treasury_usd_budget, 6),
                'authorized': treasury_allowed,
                'risk_budget': risk_budget,
            },
            'claude_dispatch': {
                'lane': 'dev',
                'execution_owner': 'claude_dispatch',
                'slots': dev_budget,
            },
            'main': {
                'lane': 'control-plane',
                'execution_owner': 'main',
                'reserve_op': reserve_op,
                'reserve_vp': reserve_vp,
            },
        },
    }



# - Claim data absorbed into TAS_trade (trader owns it)
# - Community interaction absorbed into TAS_social (bookmarker owns it)
# Retained as no-op for backward compatibility; returns None always.


def _maybe_write_wiki_query(
    strategy_action: str,
    tas_total: float,
    previous_tas: float | None,
    social_intent: dict,
    treasury_policy: dict,
    date_str: str,
    root: Path,
) -> None:
    """Record TAS decision to telemetry. Kept the original name for callsite
    stability; the function now writes a JSONL line to
    ``runtime/main/tas-decisions-YYYY-MM.jsonl`` instead of polluting
    ``wiki/queries/``.

    Reason (2026-05-21 redesign): the wiki/queries lane is meant for
    durable analytical answers per Karpathy's LLM-Wiki pattern (good
    answers compound into the knowledge base). The hourly TAS heartbeat
    flooded it with ~24 mechanically-generated decision logs per day, so
    real synthesis was drowned out (247 files in queries/, 99% TAS noise;
    2912 lines in wiki/log.md, 97.8% TAS noise). Telemetry-shaped data
    belongs in ``runtime/main/``, not the wiki.
    """
    if strategy_action == 'flat':
        return
    if previous_tas is not None:
        delta = abs(tas_total - float(previous_tas))
        if delta <= 0.05:
            return

    now = datetime.now(timezone.utc)
    record = {
        'timestamp': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'date': date_str,
        'strategy_action': strategy_action,
        'tas_total': tas_total,
        'previous_tas': previous_tas,
        'tas_delta': round(tas_total - float(previous_tas), 4) if previous_tas is not None else None,
        'social_intent': {
            'status': social_intent.get('status', 'unknown'),
            'topic_focus': social_intent.get('topic_focus') or social_intent.get('content_direction', ''),
        },
        'treasury_policy': {
            'status': treasury_policy.get('status', 'unknown'),
        },
    }
    out_dir = root / 'runtime' / 'main'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'tas-decisions-{now.strftime("%Y-%m")}.jsonl'
    try:
        with out_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass  # never interrupt main flow


def main() -> int:
    packet = read_json(RUNTIME / 'main' / 'input-packet.json') or {}
    runtime_state = read_json(RUNTIME / 'main' / 'runtime-state.json') or {}
    previous_strategy_plan = read_json(RUNTIME / 'main' / 'strategy-plan.json') or {}
    previous_budget_allocation = read_json(RUNTIME / 'shared' / 'budget-allocation.json') or {}
    previous_tas_latest = read_json(RUNTIME / 'main' / 'tas-latest.json') or {}
    previous_last_decision = read_json(RUNTIME / 'main' / 'last-decision.json') or {}
    previous_social_intent = read_json(RUNTIME / 'main' / 'social-intent.json') or {}
    previous_treasury_policy = read_json(RUNTIME / 'main' / 'treasury-policy.json') or {}
    bookmarker_latest = read_json(RUNTIME / 'bookmarker' / 'latest.json') or {}
    trader_latest = read_json(RUNTIME / 'trader' / 'latest.json') or {}
    bookmarker_social_drafts = read_json(RUNTIME / 'bookmarker' / 'social-drafts.json') or {}
    social_history = read_json(RUNTIME / 'shared' / 'social-history.json') or {}
    social_write_state = read_json(RUNTIME / 'shared' / 'social-write-state.json') or {}
    reward_status = read_json(RUNTIME / 'trader' / 'reward-status.json') or {}
    execution_record = read_json(RUNTIME / 'trader' / 'execution-record.json') or {}

    # ── Wiki Execution Brief（预编译决策层）────────────────────────────────
    wiki_brief = load_wiki_execution_brief()
    wiki_top_themes = (wiki_brief or {}).get('top_themes') or []
    wiki_top_theme = wiki_top_themes[0] if wiki_top_themes else {}
    wiki_credit_strategy = (wiki_brief or {}).get('credit_strategy') or {}
    wiki_forbidden = (wiki_brief or {}).get('forbidden') or []

    # ── Wiki Platform Context（平台快照数据）──────────────────────────────
    wiki_platform = load_wiki_platform_context()
    wiki_trending_ticks = wiki_platform.get('trending_ticks', [])
    wiki_top_communities = wiki_platform.get('top_communities', [])

    generated_at = now_iso()
    issued_at_dt = parse_dt(generated_at) or datetime.now(timezone.utc).astimezone()
    run_id = f"main-native-{issued_at_dt.strftime('%Y%m%dT%H%M%S')}"
    cycle_id = generated_at
    strategy_id = f"main-strategy-{issued_at_dt.strftime('%Y%m%dT%H%M%S')}"

    summary = packet.get('summary') if isinstance(packet.get('summary'), dict) else {}
    provenance = packet.get('provenance') if isinstance(packet.get('provenance'), dict) else {}
    fallback_fields = provenance.get('fallback_fields') if isinstance(provenance.get('fallback_fields'), list) else []
    op = safe_float(summary.get('op')) if summary else safe_float(runtime_state.get('op'))
    vp = safe_float(summary.get('vp')) if summary else safe_float(runtime_state.get('vp'))
    # FIX-3: compute daily resource consumption to detect resource floor status
    _daily_consumption = compute_daily_consumption(RUNTIME)
    resource_floor_met = bool(_daily_consumption.get('resource_floor_met', True))
    resource_floor_unmet = not resource_floor_met
    mode = compute_main_mode(op, vp, resource_floor_unmet=resource_floor_unmet)

    x_trend_keywords = summary.get('x_trend_keywords') if isinstance(summary.get('x_trend_keywords'), list) else []
    _tas_social_doc = summary.get('tas_social') if isinstance(summary.get('tas_social'), dict) else {}
    _tas_trade_doc = summary.get('tas_trade') if isinstance(summary.get('tas_trade'), dict) else {}
    _tas_xreco_doc = summary.get('tas_xreco') if isinstance(summary.get('tas_xreco'), dict) else {}
    tas_social = safe_float((_tas_social_doc or {}).get('value'))
    tas_trade = safe_float((_tas_trade_doc or {}).get('value'))
    tas_xreco = safe_float((_tas_xreco_doc or {}).get('value'))
    _tas_trade_source_status = (_tas_trade_doc or {}).get('status', '')
    _tas_xreco_source_status = (_tas_xreco_doc or {}).get('status', '')
    # If the trader itself reports a non-ok status, treat tas_trade as unavailable
    # for aggregation so degraded values don't poison tas_total.
    # P1 2026-04-10: include 'partial' — partial measurement quality must not
    # silently produce a misleading canonical TAS_trade contribution.
    if _tas_trade_source_status in ('degraded', 'blocked', 'stale', 'partial'):
        tas_trade = None
    # TAS_XReco: null if missing data or insufficient pushes — fall back gracefully
    if _tas_xreco_source_status in ('missing', 'insufficient_data', 'degraded', 'blocked'):
        tas_xreco = None
    tas_economic = None  # retired
    # TAS_XReco is now a sub-factor of TAS_social; TAS_total uses original 2-factor formula
    tas_values = [tas_social, tas_trade]
    available = [v for v in tas_values if v is not None]
    # Formula: 0.7×TAS_social + 0.3×TAS_trade (TAS_XReco absorbed into TAS_social)
    if available:
        tas_total = round(0.7 * (tas_social or 0) + 0.3 * (tas_trade or 0), 6)
        _tas_formula = '0.7 * TAS_social + 0.3 * TAS_trade'
    else:
        tas_total = None
        _tas_formula = '0.7 * TAS_social + 0.3 * TAS_trade'
    tas_status = infer_tas_status(tas_values)
    previous_tas_total = safe_float(previous_tas_latest.get('tas_total'))
    main_strategy_loop = build_metric_strategy_loop(
        'TAS',
        tas_total,
        previous_tas_total,
        tas_status,
        normalize_status(previous_tas_latest.get('status'), default='stale'),
        previous_last_decision.get('strategy_action') or previous_last_decision.get('social_decision'),
        previous_last_decision.get('reason'),
        resource_floor_unmet=resource_floor_unmet,
    )
    # FIX-4: TAS_trade always included — it is the P0 optimization target
    target_components = [name for name, value in [('tas_social', tas_social), ('tas_trade', tas_trade)] if value is None or value < 1.0]
    if 'tas_trade' not in target_components:
        target_components.append('tas_trade')
    if not target_components:
        target_components = ['tas_social', 'tas_trade']

    # FIX-6: synthesize trade drafts and inject into bookmarker draft pool
    _trade_drafts = synthesize_trade_drafts(RUNTIME, wiki_trending_ticks, mode, cycle_id)
    if _trade_drafts:
        _existing_drafts = list(bookmarker_social_drafts.get('drafts') or [])
        _existing_ids = {d.get('id') for d in _existing_drafts if isinstance(d, dict)}
        _new_trade_drafts = [d for d in _trade_drafts if d.get('id') not in _existing_ids]
        if _new_trade_drafts:
            bookmarker_social_drafts['drafts'] = _existing_drafts + _new_trade_drafts
            atomic_write_json(RUNTIME / 'bookmarker' / 'social-drafts.json', bookmarker_social_drafts)

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not packet:
        blockers.append({'code': 'missing_input_packet', 'message': 'runtime/main/input-packet.json missing', 'severity': 'error'})
    if fallback_fields:
        warnings.append({'code': 'legacy_fallback_fields_present', 'message': f'main input packet still depends on legacy fallback for: {", ".join(fallback_fields)}', 'severity': 'warning'})
    if op is None or vp is None:
        blockers.append({'code': 'op_vp_unavailable', 'message': 'main native runtime could not recover OP/VP', 'severity': 'error'})
        mode = 'blocked-runtime'
    # FIX-3: log warning when daily resource floor is not met
    if resource_floor_unmet:
        _op_consumed = _daily_consumption.get('daily_op_consumed', 0)
        _vp_consumed = _daily_consumption.get('daily_vp_consumed', 0)
        warnings.append({
            'code': 'resource_floor_unmet',
            'message': (
                f'Daily P0 floor not met: {_op_consumed:.0f}/{_daily_consumption.get("daily_op_target", 670):.0f} OP, '
                f'{_vp_consumed:.0f}/{_daily_consumption.get("daily_vp_target", 67):.0f} VP. '
                f'Mode override applied: strategy will be forced active.'
            ),
            'severity': 'warning',
        })

    bookmarker_status = normalize_status(bookmarker_latest.get('status'), default='blocked')
    trader_status = normalize_status(trader_latest.get('status'), default='blocked')
    claimable_usd = safe_float(summary.get('claimable_usd'))
    dispatch_config = build_dispatch_config(
        cycle_id=cycle_id,
        strategy_id=strategy_id,
        generated_at=generated_at,
        mode=mode,
        op=op,
        vp=vp,
        tas_total=tas_total,
        tas_social=tas_social,
        tas_trade=tas_trade,
        tas_status=tas_status,
        strategy_action=main_strategy_loop['strategy_action'],
        planning_focus=main_strategy_loop['planning_focus'],
        bookmarker_status=bookmarker_status,
        trader_status=trader_status,
        claimable_usd=claimable_usd,
    )
    atomic_write_json(RUNTIME / 'shared' / 'dispatch-config.json', dispatch_config)
    social_gate = dispatch_config.get('social') or {}
    treasury_gate = dispatch_config.get('treasury') or {}

    social_cooldown_hours = int(social_gate.get('cooldown_hours', 24) or 24)
    social_mix_order = list(social_gate.get('action_mix_order') or ['post', 'reply', 'curate', 'like'])
    social_max_per_type = dict(social_gate.get('max_per_type') or {'post': 1, 'reply': 1, 'curate': 1, 'like': 1})
    social_max_actions = int(social_gate.get('max_total_actions', 0) or 0)
    # Extended TTL: 4h minimum so bookmarker cron (every 2h) always catches a valid intent.
    # dispatch-config intent_ttl_minutes is overridden upward to 240 if lower.
    social_ttl_minutes = max(240, int(social_gate.get('intent_ttl_minutes', 240) or 240))
    social_expires_at = (issued_at_dt + timedelta(minutes=social_ttl_minutes)).isoformat(timespec='seconds')

    recent_social_targets = recent_executed_target_keys(social_history, social_cooldown_hours)
    recent_noop_curate_targets = recent_noop_curate_target_keys(social_history, social_cooldown_hours)

    breaker = social_write_state.get('breaker') if isinstance(social_write_state.get('breaker'), dict) else {}
    breaker_until = parse_dt(breaker.get('until'))
    breaker_open = bool(social_gate.get('breaker_enabled', True)) and breaker.get('state') == 'open' and breaker_until and breaker_until > issued_at_dt

    _all_pass_rewrite = True
    _rewrite_gate_failures = []
    # LCS threshold: any contiguous substring of source text >= this many chars
    # appearing in the draft body trips the gate, even if the draft's own
    # rewrite flags claim success. This catches the "fake paraphrase" failure
    # mode where a draft prepends framing but still embeds the source verbatim
    # (SsNi6hjdmf class incidents).
    # Phase 1: relaxed from 60→300 — only catches near-verbatim full copies,
    # not legitimate paraphrases that happen to share a sentence-length substring.
    _VERBATIM_LCS_THRESHOLD = 300

    def _longest_common_substring_len(a: str, b: str) -> int:
        if not a or not b:
            return 0
        # Heuristic check first: if any sliding window of length threshold from
        # `b` appears in `a`, we already know the LCS is >= threshold. This is
        # O(len(a)*len(b)/threshold) instead of building the full DP table.
        thr = _VERBATIM_LCS_THRESHOLD
        if len(b) >= thr:
            step = max(1, thr // 4)
            for i in range(0, len(b) - thr + 1, step):
                if b[i:i + thr] in a:
                    return thr
        # Fall back to a bounded LCS scan capped at threshold.
        m, n = len(a), len(b)
        prev = [0] * (n + 1)
        best = 0
        for i in range(1, m + 1):
            curr = [0] * (n + 1)
            ai = a[i - 1]
            for j in range(1, n + 1):
                if ai == b[j - 1]:
                    curr[j] = prev[j - 1] + 1
                    if curr[j] > best:
                        best = curr[j]
                        if best >= thr:
                            return best
            prev = curr
        return best

    for _d in (bookmarker_social_drafts.get('drafts') or []):
        if not isinstance(_d, dict) or _d.get('type') != 'post':
            continue
        if _d.get('source') == 'synthesize_trade_drafts':
            continue  # trade commentary — no source to rewrite
        _rg = _d.get('rewrite_gate_passed')
        _dr = _d.get('_draft_rewritten')
        # Phase 1: relaxed flag check — only block on explicit False, not on unknown/missing.
        # The LCS reverse-check (300-char threshold) already catches genuine verbatim copies.
        _flag_fail = _rg is False

        # Verbatim reverse-check: regardless of what the flags claim, the body
        # must not echo a long contiguous chunk of the source.
        _verbatim_fail = False
        _verbatim_len = 0
        _body = str(_d.get('text') or '')
        # Source signals: prefer full source_excerpt if present, else fall back
        # to source_tweet_text / source_text fields that some drafts carry.
        _source_candidates = [
            _d.get('source_excerpt'),
            _d.get('source_tweet_text'),
            _d.get('source_text'),
        ]
        for _src in _source_candidates:
            _src_s = str(_src or '').strip()
            if not _src_s or len(_src_s) < 20:
                continue
            _verbatim_len = _longest_common_substring_len(_body, _src_s)
            if _verbatim_len >= _VERBATIM_LCS_THRESHOLD:
                _verbatim_fail = True
                break

        if _flag_fail or _verbatim_fail:
            _all_pass_rewrite = False
            _rewrite_gate_failures.append({
                'draft_id': _d.get('id') or _d.get('draft_id') or '(unknown)',
                'rewrite_gate_passed': _rg,
                '_draft_rewritten': _dr,
                'verbatim_lcs_len': _verbatim_len if _verbatim_fail else None,
                'fail_reason': 'verbatim_source_overlap' if _verbatim_fail and not _flag_fail else ('flag_fail' if _flag_fail and not _verbatim_fail else 'flag_and_verbatim'),
            })
    if not _all_pass_rewrite:
        print(f"[WARN][social_gate] drafts_pass_rewrite_gate FAILED — {len(_rewrite_gate_failures)} draft(s) blocked: {_rewrite_gate_failures}")

    social_gate_checks = {
        'lane_enabled': bool(social_gate.get('enabled', False)),
        'mode_ok': gate_allows_mode(mode, str(social_gate.get('min_mode', 'active'))),
        'bookmarker_status_ok': bookmarker_status in _status_list(social_gate.get('require_bookmarker_status_in'), ['ok']),
        'has_candidates': bool((packet.get('bookmarker') or {}).get('candidate_count')) if social_gate.get('require_candidates', True) else True,
        'has_drafts': bool((bookmarker_social_drafts.get('drafts') or [])) if social_gate.get('require_drafts', True) else True,
        'breaker_closed': not breaker_open,
        'no_main_blockers': not blockers,
        'drafts_pass_rewrite_gate': _all_pass_rewrite,
    }
    social_gate_authorized = all(social_gate_checks.values())
    social_authorized = social_gate_authorized
    social_selection_outcome = 'gate_blocked'
    social_selection = analyze_social_action_selection(
        bookmarker_social_drafts,
        social_max_actions,
        recent_social_targets,
        recent_noop_curate_targets,
        social_mix_order,
        social_max_per_type,
    ) if social_gate_authorized else {'actions': [], 'selection_reason': 'gate_blocked', 'suppressed': {}, 'draft_count': 0, 'selected_ids': []}
    social_actions = social_selection['actions']
    social_selection_outcome = str(social_selection.get('selection_reason') or ('selected_actions' if social_actions else 'gate_blocked'))
    if social_authorized and not social_actions:
        social_authorized = False
        reason = social_selection.get('selection_reason')
        suppressed = social_selection.get('suppressed') or {}
        if reason == 'drafts_missing':
            code = 'social_gate_drafts_missing'
            message = 'social lane passed gating but the drafts payload was missing or unreadable'
        elif reason == 'no_drafts':
            code = 'social_gate_no_drafts'
            message = 'social lane passed gating but no social drafts were available for this cycle'
        elif reason == 'cooldown_or_policy_suppressed':
            code = 'social_gate_actions_suppressed'
            details = []
            if suppressed.get('recent_target'):
                details.append(f"recent_target={suppressed['recent_target']}")
            if suppressed.get('recent_noop_curate'):
                details.append(f"recent_noop_curate={suppressed['recent_noop_curate']}")
            detail_text = f" ({', '.join(details)})" if details else ''
            message = f'social lane passed gating but all candidate drafts were suppressed by cooldown / duplicate-target policy{detail_text}'
        elif reason == 'duplicate_target_suppressed':
            code = 'social_gate_duplicate_target_suppressed'
            message = 'social lane passed gating but candidate drafts collapsed under duplicate-target constraints'
        elif reason == 'type_cap_suppressed':
            code = 'social_gate_type_cap_suppressed'
            message = 'social lane passed gating but candidate drafts were suppressed by per-type action caps'
        else:
            code = 'social_gate_no_actions'
            message = 'social lane passed gating but no executable social actions survived current selection constraints'
        warnings.append({'code': code, 'message': message, 'severity': 'warning'})

    bookmarker_budget_slice = ((compute_budget_allocation(op, vp, mode, social_authorized, False, {}, len(warnings), len(blockers)).get('allocations') or {}).get('bookmarker') or {})

    # Phase 4: simplified action extraction — main no longer builds post_directive/reply_directive.
    # Only curate_targets extracted as reference hints for bookmarker.
    curate_targets: list[dict[str, Any]] = []
    _content_topic = None
    _content_reason = None
    for action in (social_actions if isinstance(social_actions, list) else []):
        if not isinstance(action, dict):
            continue
        atype = action.get('type') or action.get('action_type') or ''
        if atype in ('curate', 'like'):
            tid = action.get('tweetId') or action.get('tweet_id') or action.get('target_key', '').replace('tagclaw:', '')
            if tid:
                curate_targets.append({
                    'tweet_id': str(tid),
                    'reason': action.get('reason') or action.get('note') or f'{atype} from main cycle',
                })
        elif atype == 'post' and _content_topic is None:
            _content_topic = action.get('wiki_topic') or wiki_top_theme.get('name') if wiki_top_theme else None
            _content_reason = action.get('reason') or (wiki_top_theme.get('agent_action') if wiki_top_theme else None)

    # FIX-RC3: when resource floor is unmet and no curate_targets were extracted from
    # social_actions (drafts had no curate-type entries), inject fallback curate_targets
    # from content_candidates.json so the executor has explicit targets instead of
    # falling back to a potentially-empty feed scan.
    if resource_floor_unmet and not curate_targets:
        _cc = read_json(RUNTIME / 'bookmarker' / 'content-candidates.json') or {}
        _cc_items = _cc.get('items') if isinstance(_cc.get('items'), list) else []
        _existing_target_ids = {t['tweet_id'] for t in curate_targets}
        for _item in _cc_items:
            if len(curate_targets) >= 5:
                break
            if not isinstance(_item, dict):
                continue
            _pid = str(_item.get('post_id') or '').strip()
            if not _pid or _pid in _existing_target_ids:
                continue
            # FIX-2026-05-25: skip targets already known to be noop'd to prevent recycling
            _target_key = f'tagclaw:{_pid}'
            if _target_key in (recent_noop_curate_targets or set()):
                continue
            curate_targets.append({
                'tweet_id': _pid,
                'reason': f'floor-unmet fallback curate from content_candidates (score={_item.get("score")})',
            })
            _existing_target_ids.add(_pid)

    # P2 2026-04-03: strategy experiment — run before building social_intent so
    # next_arms can override post_config and curator_config in the payload.
    _next_arms: dict[str, Any] = {}
    try:
        import importlib.util as _ilu
        _exp_spec = _ilu.spec_from_file_location(
            'strategy_experiment', ROOT / 'scripts' / 'strategy_experiment.py'
        )
        _exp_mod = _ilu.module_from_spec(_exp_spec)
        _exp_spec.loader.exec_module(_exp_mod)

        # Graceful fallback sources ─────────────────────────────────────────
        _reward_attr = read_json(RUNTIME / 'bookmarker' / 'reward-attribution.json') or {}
        _curator_reward_usd = safe_float(_reward_attr.get('curator_reward_usd')) or 0.0

        _bk_social_exec = read_json(RUNTIME / 'bookmarker' / 'social-execution-result.json') or {}
        _vp_spent = safe_float(_bk_social_exec.get('vp_spent')) or 0.0

        _bk_post_log = read_json(RUNTIME / 'bookmarker' / 'post-engagement-log.json') or {}
        _bk_entries = _bk_post_log.get('entries') or []
        _recent_entries = _bk_entries[-5:]
        _posts_count = len([e for e in _recent_entries if e.get('our_post_id')])
        _curators_attracted = sum(
            len(e.get('curators_attracted') or [])
            for e in _recent_entries
        )
        _creator_reward_usd = safe_float(_bk_post_log.get('creator_reward_usd')) or 0.0

        # No prior baseline → neutral delta. Previously `(prev or 0.0)` made a cold
        # start read as a full-magnitude "improved", faking a reinforce verdict.
        if previous_tas_total is None:
            _tas_delta = 0.0
        else:
            _tas_delta = round((tas_total or 0.0) - previous_tas_total, 4)
        _prev_tas_social = safe_float(previous_tas_latest.get('tas_social'))
        if _prev_tas_social is None:
            _tas_social_delta = 0.0
        else:
            _tas_social_delta = round((tas_social or 0.0) - _prev_tas_social, 4)

        _next_arms = _exp_mod.run_cycle(
            tas_delta=_tas_delta,
            tas_social_delta=_tas_social_delta,
            curator_reward_usd=_curator_reward_usd,
            vp_spent=_vp_spent,
            creator_reward_usd=_creator_reward_usd,
            posts_count=_posts_count,
            curators_attracted=_curators_attracted,
            cycle_id=cycle_id,
        )
    except Exception:
        _next_arms = {}

    # ── Phase 2: Write social-strategy.json for bookmarker consumption ──
    # Replaces legacy select_strategy bookmarker-guidance.json.
    # Main writes high-level strategy direction only. Bookmarker reads this
    # and decides execution details autonomously.
    _tas_trend = main_strategy_loop.get('strategy_action', 'conservative_explore')
    _mode = mode or 'conservative'

    # Determine strategy_level from TAS trend + mode
    if _mode in ('super-active', 'vp-flush'):
        _social_level = 'aggressive'
    elif _mode in ('mid-active', 'active', 'vp-drain'):
        _social_level = 'normal'
    else:
        _social_level = 'cautious'
    # TAS trend override: rising TAS can bump up; falling can bump down
    if _tas_trend in ('reinforce_previous_strategy',) and _social_level != 'aggressive':
        _social_level = 'normal'
    elif _tas_trend in ('discard_previous_strategy',) and _social_level != 'cautious':
        _social_level = 'cautious'

    _social_strategy = {
        'schema': 'main.social-strategy.v1',
        'version': 'v1',
        'generated_at': generated_at,
        'cycle_id': cycle_id,
        'run_id': run_id,
        'strategy_level': _social_level,
        'mode': _mode,
        'strategy_action': main_strategy_loop.get('strategy_action'),
        'resource_envelope': {
            'max_actions': max(social_max_actions, 1) if resource_floor_unmet else social_max_actions,
            'op_target': bookmarker_budget_slice.get('op', 0),
            'vp_target': bookmarker_budget_slice.get('vp', 0),
            'min_actions_override': bool(resource_floor_unmet),
        },
        'planning_focus': main_strategy_loop.get('planning_focus'),
        'target_components': target_components,
        'experiment_verdict_a': str(_next_arms.get('verdict_a', 'none')) if _next_arms else None,
        'experiment_verdict_b': str(_next_arms.get('verdict_b', 'none')) if _next_arms else None,
    }
    atomic_write_json(RUNTIME / 'main' / 'social-strategy.json', _social_strategy)

    # ── Phase 3: Write treasury-strategy.json for trader consumption ──
    # Derived from same mode + TAS trend as social strategy, but with trader-specific levels.
    if _mode in ('super-active', 'mid-active'):
        _trade_level = 'aggressive'
    elif _mode in ('active', 'vp-flush'):
        _trade_level = 'normal'
    else:
        _trade_level = 'cautious'
    # TAS_trade trend override
    _trade_trend = main_strategy_loop.get('strategy_action', 'conservative_explore')
    if _trade_trend in ('reinforce_previous_strategy',) and _trade_level != 'aggressive':
        _trade_level = 'normal'
    elif _trade_trend in ('discard_previous_strategy',) and _trade_level != 'cautious':
        _trade_level = 'cautious'

    _claimable = safe_float(reward_status.get('claimable_usd')) if reward_status else 0.0
    _trade_strategy = {
        'schema': 'main.treasury-strategy.v1',
        'version': 'v1',
        'generated_at': generated_at,
        'cycle_id': cycle_id,
        'run_id': run_id,
        'strategy_level': _trade_level,
        'mode': _mode,
        'resource_envelope': {
            'claim_threshold_usd': max(0.5, 2.0 - [0.0, 1.0, 1.5][['cautious', 'normal', 'aggressive'].index(_trade_level)] if _trade_level in ('cautious', 'normal', 'aggressive') else 0.0),
            'auto_trade_enabled': _trade_level in ('aggressive', 'normal'),
            'max_budget_usd': {'aggressive': 5.0, 'normal': 2.0, 'cautious': 0.0}.get(_trade_level, 0.0),
            'allow_claim': (_claimable or 0.0) >= ({'aggressive': 0.5, 'normal': 1.0, 'cautious': 2.0}.get(_trade_level, 2.0)),
        },
        'planning_focus': main_strategy_loop.get('planning_focus'),
    }
    atomic_write_json(RUNTIME / 'main' / 'treasury-strategy.json', _trade_strategy)

    social_decision = 'authorize' if social_authorized else 'hold'
    social_trade_post_directive = None
    if social_authorized and not any(str((action or {}).get('type') or '') == 'post' for action in social_actions):
        social_trade_post_directive = build_social_trade_post_directive(RUNTIME)
    social_intent = {
        'version': 'v2',
        'intent_kind': 'social-intent',
        'issued_by': 'main',
        'target_agent': 'bookmarker',
        'cycle_id': cycle_id,
        'strategy_id': strategy_id,
        'run_id': run_id,
        'issued_at': generated_at,
        # Always +4h so bookmarker cron (every 2h) sees a fresh intent.
        'expires_at': social_expires_at,
        'status': 'active' if social_authorized else 'revoked',
        'reason': 'native runtime-first authorization' if social_authorized else 'native runtime-first gate did not authorize social execution this cycle',
        'strategy_action': main_strategy_loop['strategy_action'],
        'planning_focus': main_strategy_loop['planning_focus'],
        'constraints': {
            'social_lock_required': True,
            'max_total_actions': social_max_actions if social_authorized else 0,
        },
        'payload': {
            'authorized': social_authorized,
            'gate_authorized': social_gate_authorized,
            'selection_outcome': social_selection_outcome,
            'mode': mode,
            'actions': social_actions,
            'curate_targets': curate_targets,
            **({'post_directive': social_trade_post_directive} if social_trade_post_directive else {}),
            'content_direction': _content_topic or (wiki_top_theme.get('agent_action') if wiki_top_theme else None),
            'content_reason': _content_reason,
            'max_total_actions': social_max_actions if social_authorized else 0,
            'budget_slice': bookmarker_budget_slice,
            **({'x_trend_keywords': x_trend_keywords} if x_trend_keywords else {}),
            'wiki_top_theme': wiki_top_theme.get('name') if wiki_top_theme else None,
            'wiki_content_direction': wiki_top_theme.get('agent_action') if wiki_top_theme else None,
            'post_config': {
                # Phase 1: simplified — main only gives high-level envelope
                'resource_mode': mode,
                'strategy_action': main_strategy_loop.get('strategy_action'),
                'max_actions': social_max_actions if social_authorized else 0,
                'budget_slice': bookmarker_budget_slice,
                # P2 (2026-06-06): wire the bandit's Track-B arm into the payload so
                # engagement_mode/target_agents actually drive bookmarker behavior.
                # Consumer: execute_social_intent_v2.py reads post_config.engagement_mode.
                # Previously severed (arm chosen but never written) → fake learning.
                'engagement_mode': (_next_arms.get('track_b') or {}).get('engagement_mode', 'none'),
                'target_agents': (_next_arms.get('track_b') or {}).get('target_agents', []),
            },
            'curator_config': {
                # Phase 1: simplified — no more experiment arm parameters
                'resource_mode': mode,
                'strategy_action': main_strategy_loop.get('strategy_action'),
                'vp_budget': bookmarker_budget_slice.get('vp'),
                'op_budget': bookmarker_budget_slice.get('op'),
            },
        },
        'meta': {
            'decision_engine': 'native-runtime-v1',
            'strategy_loop': main_strategy_loop,
            'strategy_ref': 'runtime/main/strategy-plan.json',
            'budget_ref': 'runtime/shared/budget-allocation.json',
            'target_components': target_components,
            'previous_social_intent_status': previous_social_intent.get('status'),
            'gate_checks': social_gate_checks,
            'authorization': {
                'gate_authorized': social_gate_authorized,
                'selection_outcome': social_selection_outcome,
                'final_authorized': social_authorized,
            },
            'selection_reason': social_selection.get('selection_reason'),
            'selection_suppressed': social_selection.get('suppressed'),
            'selected_draft_ids': social_selection.get('selected_ids'),
            'social_trade_post_directive_enabled': bool(social_trade_post_directive),
            'cooldown_hours': social_cooldown_hours,
            'action_mix_order': social_mix_order,
            'max_per_type': social_max_per_type,
            'recently_executed_target_keys': sorted(recent_social_targets),
            'recent_noop_curate_target_keys': sorted(recent_noop_curate_targets),
            'breaker_state': breaker.get('state'),
            'breaker_until': breaker.get('until'),
            'breaker_last_failure_reason': breaker.get('last_failure_reason'),
            'tick_selector': None,
        },
        # Wiki-driven fields
        'wiki_brief_available': wiki_brief is not None,
        'wiki_brief_valid_until': (wiki_brief or {}).get('valid_until'),
        'wiki_top_theme': wiki_top_theme.get('name') if wiki_top_theme else None,
        'wiki_content_direction': wiki_top_theme.get('agent_action') if wiki_top_theme else None,
        'wiki_align_hook': wiki_top_theme.get('align_hook') if wiki_top_theme else None,
        'wiki_forbidden': wiki_forbidden[:5] if wiki_forbidden else [],
        'wiki_platform_available': not wiki_platform.get('stale', True),
        'wiki_trending_ticks': wiki_trending_ticks[:5],
        'wiki_platform_snapshot_age_hours': wiki_platform.get('snapshot_age_hours'),
    }

    treasury_ttl_minutes = int(treasury_gate.get('policy_ttl_minutes', 30) or 30)
    treasury_expires_at = (issued_at_dt + timedelta(minutes=treasury_ttl_minutes)).isoformat(timespec='seconds')
    recent_operations = summary.get('recent_operations') if isinstance(summary.get('recent_operations'), list) else []
    last_failed_operation = summary.get('last_failed_operation') if isinstance(summary.get('last_failed_operation'), dict) else None
    pending_or_unconfirmed_orders = summary.get('pending_or_unconfirmed_orders') if isinstance(summary.get('pending_or_unconfirmed_orders'), list) else []
    trading_config = treasury_gate.get('trading') or {}
    allow_claim_gate = bool(treasury_gate.get('allow_claim', False))
    allow_trading_gate = bool(treasury_gate.get('allow_trade', False))
    claim_threshold_ok = (claimable_usd is not None and claimable_usd >= float(treasury_gate.get('min_claimable_usd', 2.0)))
    treasury_gate_checks = {
        'lane_enabled': bool(treasury_gate.get('enabled', False)),
        'trader_status_ok': trader_status in (treasury_gate.get('require_trader_status_in') or ['ok', 'partial']),
        'claim_or_trade_eligible': allow_claim_gate or allow_trading_gate,
        'no_main_blockers': not blockers,
    }
    treasury_gate_authorized = all(treasury_gate_checks.values())
    treasury_allowed = treasury_gate_authorized
    treasury_selection_outcome = 'trade_allowed' if (treasury_allowed and allow_trading_gate) else ('claim_allowed' if (treasury_allowed and allow_claim_gate) else 'gate_blocked')
    budget_allocation = {
        'version': 'v1',
        'allocation_kind': 'budget-allocation',
        'cycle_id': cycle_id,
        'strategy_id': strategy_id,
        'generated_at': generated_at,
        'generated_by': 'main',
        'source_class': 'main-owned',
        **compute_budget_allocation(op, vp, mode, social_authorized, treasury_allowed, {
            'max_budget_usd': float(trading_config.get('max_budget_usd', 0)),
        }, len(warnings), len(blockers)),
        'previous_budget_ref': 'runtime/shared/budget-allocation.json' if previous_budget_allocation else None,
    }
    if last_failed_operation:
        warnings.append({
            'code': 'treasury_recent_failed_operation',
            'message': f"recent trader operation failed or stayed unconfirmed: {last_failed_operation.get('action')} {last_failed_operation.get('tick')}",
            'severity': 'warning',
        })
    elif pending_or_unconfirmed_orders:
        warnings.append({
            'code': 'treasury_pending_or_unconfirmed',
            'message': f"trader has {len(pending_or_unconfirmed_orders)} pending or unconfirmed treasury operation(s)",
            'severity': 'warning',
        })
    # P1-4B: Read bookmarker align-events.json for cross-agent coupling
    _align_events = read_json(RUNTIME / 'bookmarker' / 'align-events.json') or {}
    _align_event_active = False
    _align_coupling = {'align_event_active': False, 'source': 'missing'}
    if _align_events.get('has_active_event'):
        _expires_str = _align_events.get('event_expires_at', '')
        if _expires_str:
            try:
                _expires_dt = datetime.fromisoformat(_expires_str.replace('Z', '+00:00'))
                if _expires_dt > datetime.now(timezone.utc):
                    _align_event_active = True
            except Exception:
                pass
        if _align_event_active:
            _align_coupling = {
                'align_event_active': True,
                'align_detected_at': _align_events.get('detected_at'),
                'recommended_claim_threshold_usd': 2.0,
                'coupling_source': 'bookmarker-align',
            }
        else:
            _align_coupling = {'align_event_active': False, 'source': 'expired'}
    elif _align_events:
        _align_coupling = {'align_event_active': False, 'source': 'no_active_event'}

    treasury_policy = {
        'version': 'v1',
        'intent_kind': 'treasury-policy',
        'issued_by': 'main',
        'target_agent': 'trader',
        'cycle_id': cycle_id,
        'strategy_id': strategy_id,
        'run_id': run_id,
        'issued_at': generated_at,
        'expires_at': treasury_expires_at if treasury_allowed else generated_at,
        'status': 'active' if treasury_allowed else 'revoked',
        'reason': 'native runtime-first treasury policy' if treasury_allowed else 'native runtime-first gate did not authorize treasury execution this cycle',
        'strategy_action': main_strategy_loop['strategy_action'],
        'planning_focus': main_strategy_loop['planning_focus'],
        'constraints': {'treasury_lock_required': True},
        'payload': {
            'execution_allowed': treasury_allowed,
            'mode': 'conservative' if treasury_allowed else 'pause',
            'gate_authorized': treasury_gate_authorized,
            'claims_allowed': treasury_allowed and allow_claim_gate,
            'trading_allowed': treasury_allowed and allow_trading_gate,
            'rebalance_allowed': treasury_allowed and ('rebalance' in (trading_config.get('allowed_actions') or [])),
            'max_budget_usd': float(trading_config.get('max_budget_usd', 0)),
            'max_position_change_pct': float(trading_config.get('max_position_change_pct', 0)),
            'max_trades_per_cycle': int(trading_config.get('max_trades_per_cycle', 1)),
            'max_trades_per_day': int(trading_config.get('max_trades_per_day', 2)),
            'max_sells_per_day': int(trading_config.get('max_sells_per_day', 1)),
            'max_same_tick_trades_per_day': int(trading_config.get('max_same_tick_trades_per_day', 1)),
            'min_bnb_reserve': float(trading_config.get('min_bnb_reserve', 0.005)),
            'max_sell_usd': float(trading_config.get('max_sell_usd', 0)),
            'allowed_actions': trading_config.get('allowed_actions', []),
            'allowed_ticks': trading_config.get('allowed_ticks', []),
            'sell_triggers': trading_config.get('sell_triggers', {}),
            'notes': 'claim-first with trading enabled' if (treasury_allowed and allow_trading_gate) else ('claim-only policy envelope' if treasury_allowed else 'treasury lane not authorized'),
            'budget_slice': (budget_allocation.get('allocations') or {}).get('trader'),
            'wiki_recommended_tokens': wiki_trending_ticks[:3] or wiki_credit_strategy.get('recommended_tokens', ['TagClaw', 'BUIDL', 'TTAI']),
            'wiki_trending_ticks': wiki_trending_ticks[:5],
            'wiki_top_communities': wiki_top_communities[:5],
            'wiki_vp_flush_threshold': wiki_credit_strategy.get('vp_flush_threshold', 150),
            'wiki_daily_vp_target': wiki_credit_strategy.get('daily_vp_target', 67.0),
            'coupling': _align_coupling,
        },
        'meta': {
            'decision_engine': 'native-runtime-v1',
            'strategy_loop': main_strategy_loop,
            'strategy_ref': 'runtime/main/strategy-plan.json',
            'budget_ref': 'runtime/shared/budget-allocation.json',
            'target_components': target_components,
            'previous_treasury_policy_status': previous_treasury_policy.get('status'),
            'gate_checks': treasury_gate_checks,
            'authorization': {
                'gate_authorized': treasury_gate_authorized,
                'selection_outcome': treasury_selection_outcome,
                'final_authorized': treasury_allowed,
            },
            'claimable_usd': claimable_usd,
            'last_execution_status': execution_record.get('status'),
            'execution_count_today': summary.get('execution_count_today'),
            'recent_operations': recent_operations,
            'last_failed_operation': last_failed_operation,
            'pending_or_unconfirmed_orders': pending_or_unconfirmed_orders,
        },
    }

    dispatch_summary = build_dispatch_summary(
        dispatch_config,
        social_gate_authorized=social_gate_authorized,
        social_selection_outcome=social_selection_outcome,
        social_final_authorized=social_authorized,
        treasury_gate_authorized=treasury_gate_authorized,
        treasury_selection_outcome=treasury_selection_outcome,
        treasury_final_authorized=treasury_allowed,
    )

    for name, ok in social_gate_checks.items():
        if not ok:
            warnings.append({'code': f'social_gate_{name}', 'message': f'social gate check failed: {name}', 'severity': 'warning'})
    for name, ok in treasury_gate_checks.items():
        if not ok:
            warnings.append({'code': f'treasury_gate_{name}', 'message': f'treasury gate check failed: {name}', 'severity': 'warning'})

    strategy_confidence = 0.85
    if warnings:
        strategy_confidence -= min(0.25, 0.05 * len(warnings))
    if blockers:
        strategy_confidence -= min(0.5, 0.15 * len(blockers))
    strategy_confidence = max(0.05, round(strategy_confidence, 2))
    strategy_plan = {
        'version': 'v1',
        'plan_kind': 'strategy-plan',
        'cycle_id': cycle_id,
        'strategy_id': strategy_id,
        'run_id': run_id,
        'issued_at': generated_at,
        'issued_by': 'main',
        'source_class': 'main-owned',
        'strategy_action': main_strategy_loop['strategy_action'],
        'planning_focus': main_strategy_loop['planning_focus'],
        'hypothesis': build_strategy_hypothesis(target_components, mode, tas_status),
        'target_metrics': ['TAS'] + [name.upper() if name == 'tas' else name for name in target_components],
        'assigned_agents': ['main'] + (['bookmarker'] if social_authorized or 'tas_social' in target_components else []) + (['trader'] if treasury_allowed or 'tas_trade' in target_components else []) + (['claude_dispatch'] if warnings or blockers else []),
        'expected_uplift': {
            'tas': round(0.12 if social_authorized or treasury_allowed else 0.04, 4),
            'tas_social': round(0.18 if 'tas_social' in target_components else 0.05, 4),
            'tas_trade': round(0.15 if 'tas_trade' in target_components else 0.03, 4),  # FIX-4: elevated uplift — TAS_trade always targeted
        },
        'confidence': strategy_confidence,
        'previous_strategy_id': previous_strategy_plan.get('strategy_id'),
        'strategy_loop': main_strategy_loop,
        'refs': {
            'previous_strategy_plan': 'runtime/main/strategy-plan.json' if previous_strategy_plan else None,
            'tas_latest': 'runtime/main/tas-latest.json',
            'social_intent': 'runtime/main/social-intent.json',
            'treasury_policy': 'runtime/main/treasury-policy.json',
        },
    }
    last_decision = {
        'version': 'v1',
        'cycle_id': cycle_id,
        'strategy_id': strategy_id,
        'run_id': run_id,
        'updated_at': generated_at,
        'mode': mode,
        'social_decision': social_decision,
        'treasury_decision': 'allow' if treasury_allowed else 'pause',
        'reason': runtime_state.get('notes') or packet.get('notes') or 'native runtime-first main cycle',
        'strategy_action': main_strategy_loop['strategy_action'],
        'planning_focus': main_strategy_loop['planning_focus'],
        'target_components': target_components,
        'strategy_loop': main_strategy_loop,
        'strategy_ref': 'runtime/main/strategy-plan.json',
        'budget_ref': 'runtime/shared/budget-allocation.json',
        'social_intent_ref': 'runtime/main/social-intent.json',
        'treasury_policy_ref': 'runtime/main/treasury-policy.json',
    }

    tas_latest = {
        'version': 'v2',
        'cycle_id': cycle_id,
        'strategy_id': strategy_id,
        'updated_at': generated_at,
        'tas_social': tas_social,
        'tas_trade': tas_trade,
        'tas_xreco': tas_xreco,
        'tas_economic': None,  # retired 2026-03-25, kept for backward compat
        'tas_total': tas_total,
        'formula': _tas_formula,
        'status': tas_status,
        'comparison': main_strategy_loop,
        'strategy_action': main_strategy_loop['strategy_action'],
        'planning_focus': main_strategy_loop['planning_focus'],
        'target_components': target_components,
        'strategy_ref': 'runtime/main/strategy-plan.json',
        'budget_ref': 'runtime/shared/budget-allocation.json',
        'notes': 'TAS_economic retired; TAS_XReco absorbed into TAS_social as sub-factor; formula: 0.7×social + 0.3×trade',
    }

    runtime_health = {
        'version': 'v1',
        'updated_at': generated_at,
        'main_status': 'partial' if warnings else ('blocked' if blockers else 'ok'),
        'bookmarker_status': bookmarker_status,
        'trader_status': trader_status,
        'social_lane': 'ready' if social_authorized else 'idle',
        'treasury_lane': 'ready' if treasury_allowed else 'idle',
        'notes': 'native runtime-first main health snapshot',
    }

    latest = {
        'version': 'v1',
        'agent': 'main',
        'run_id': run_id,
        'status': 'blocked' if blockers else ('partial' if warnings else 'ok'),
        'generated_at': generated_at,
        'data_window': {
            'start': packet.get('updated_at') or generated_at,
            'end': generated_at,
        },
        'ttl_seconds': 14400,
        'freshness_seconds': 0,
        'inputs': {
            'input_packet': 'runtime/main/input-packet.json',
            'previous_strategy_plan': 'runtime/main/strategy-plan.json' if previous_strategy_plan else None,
            'previous_budget_allocation': 'runtime/shared/budget-allocation.json' if previous_budget_allocation else None,
            'bookmarker_latest': 'runtime/bookmarker/latest.json',
            'trader_latest': 'runtime/trader/latest.json',
            'dispatch_config': 'runtime/shared/dispatch-config.json',
            'social_history': 'runtime/shared/social-history.json',
        },
        'outputs': {
            'strategy_id': strategy_id,
            'last_failed_operation': last_failed_operation,
            'pending_or_unconfirmed_orders': pending_or_unconfirmed_orders,
            'trader_execution_summary': {
                'execution_count_today': summary.get('execution_count_today'),
                'recent_operations': recent_operations,
                'last_failed_operation': last_failed_operation,
                'pending_or_unconfirmed_orders': pending_or_unconfirmed_orders,
            },
            'mode': mode,
            'tas': {
                'social': tas_social,
                'trade': tas_trade,
                'xreco': tas_xreco,
                'total': tas_total,
                'formula': _tas_formula,
                'status': tas_status,
            },
            'strategy_loop': main_strategy_loop,
            'strategy_action': main_strategy_loop['strategy_action'],
            'planning_focus': main_strategy_loop['planning_focus'],
            'strategy_plan_ref': 'runtime/main/strategy-plan.json',
            'budget_allocation_ref': 'runtime/shared/budget-allocation.json',
            'target_components': target_components,
            'social_intent_ref': 'runtime/main/social-intent.json',
            'treasury_policy_ref': 'runtime/main/treasury-policy.json',
            'bookmarker_guidance_ref': 'runtime/main/bookmarker-guidance.json',
            'trader_guidance_ref': 'runtime/main/trader-guidance.json',
            'social_decision': social_decision,
            'treasury_decision': 'allow' if treasury_allowed else 'pause',
            'claimable_usd': claimable_usd,
            'claim_recommended': claim_threshold_ok,
            'reason': last_decision['reason'],
            'dispatch_complete': social_authorized or treasury_allowed,
            'dispatch': dispatch_summary,
        },
        'dispatch': dispatch_summary,
        'blockers': blockers,
        'warnings': warnings,
        'next_recommended_action': 'main is control-plane only; run bookmarker social worker or trader treasury worker when respective guidance/policy is active',
        'meta': {
            'decision_engine': 'native-runtime-v1',
            'op': op,
            'vp': vp,
            'fallback_fields': fallback_fields,
            'previous_run_id': previous_last_decision.get('run_id'),
        },
        # Wiki-driven fields (platform + brief)
        'wiki_brief_available': wiki_brief is not None,
        'wiki_brief_valid_until': (wiki_brief or {}).get('valid_until'),
        'wiki_top_theme': wiki_top_theme.get('name') if wiki_top_theme else None,
        'wiki_content_direction': wiki_top_theme.get('agent_action') if wiki_top_theme else None,
        'wiki_forbidden': wiki_forbidden[:3] if wiki_forbidden else [],
        'wiki_platform_available': not wiki_platform.get('stale', True),
        'wiki_trending_ticks': wiki_trending_ticks[:5],
        'wiki_platform_snapshot_age_hours': wiki_platform.get('snapshot_age_hours'),
    }

    atomic_write_json(RUNTIME / 'main' / 'strategy-plan.json', strategy_plan)
    atomic_write_json(RUNTIME / 'main' / 'daily-consumption.json', _daily_consumption)  # FIX-3
    atomic_write_json(RUNTIME / 'shared' / 'budget-allocation.json', budget_allocation)
    atomic_append_jsonl(RUNTIME / 'shared' / 'strategy-ledger.jsonl', {
        'cycle_id': cycle_id,
        'strategy_id': strategy_id,
        'generated_at': generated_at,
        'strategy_action': strategy_plan['strategy_action'],
        'planning_focus': strategy_plan['planning_focus'],
        'target_metrics': strategy_plan['target_metrics'],
        'confidence': strategy_plan['confidence'],
    })
    atomic_write_json(RUNTIME / 'main' / 'social-intent.json', social_intent)
    atomic_write_json(RUNTIME / 'main' / 'treasury-policy.json', treasury_policy)
    atomic_write_json(RUNTIME / 'main' / 'last-decision.json', last_decision)
    atomic_write_json(RUNTIME / 'main' / 'tas-latest.json', tas_latest)
    try:
        _append_tas_history(RUNTIME, cycle_id, tas_total, tas_social, tas_trade, tas_status, tas_xreco=tas_xreco)
    except Exception:
        pass  # graceful degrade — never interrupt main flow
    atomic_write_json(RUNTIME / 'main' / 'runtime-health.json', runtime_health)
    atomic_write_json(RUNTIME / 'main' / 'latest.json', latest)
    update_runtime_status(
        main_status=latest['status'],
        generated_at=generated_at,
        dispatch_summary=dispatch_summary,
        social_lane_state=str(((social_intent.get('meta') or {}).get('authorization') or {}).get('selection_outcome') or social_decision),
        treasury_lane_state=str(((treasury_policy.get('meta') or {}).get('authorization') or {}).get('selection_outcome') or ('allow' if treasury_allowed else 'pause')),
    )

    # ── D2: Query writeback — high-signal decisions → wiki/queries/ ──────────
    _maybe_write_wiki_query(
        strategy_action=main_strategy_loop['strategy_action'],
        tas_total=tas_total,
        previous_tas=previous_last_decision.get('tas_total'),
        social_intent=social_intent,
        treasury_policy=treasury_policy,
        date_str=generated_at[:10],
        root=ROOT,
    )

    # Phase 2 deprecation: select_strategy.py guidance files removed.
    # strategy_experiment.py + social-strategy.json now cover strategy direction.
    # select_strategy --stats remains available for manual diagnostic use.
    _guidance = {}
    _exp_mode = 'phase2/deprecated'

    print(json.dumps([
        {'agent': 'main', 'status': latest['status'],
         'latest_path': str(RUNTIME / 'main' / 'latest.json'),
         'guidance_experiment_mode': _exp_mode}
    ], ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
