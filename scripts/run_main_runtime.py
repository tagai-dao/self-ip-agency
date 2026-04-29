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
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from runtime_utils import (
    analyze_social_action_selection,
    atomic_write_json,
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
    best_tick = ranked[0][0] if ranked else 'TagClaw'
    best_info = scores.get(best_tick, {})

    return {
        'tick': best_tick,
        'score': best_info.get('score', 0),
        'reason': f"reward-aware-selector: {best_info.get('reason', 'default')}",
        'scores': {k: v for k, v in ranked[:6]},  # top-6 for trace
    }


def _append_tas_history(runtime: Path, cycle_id: str, tas_total: Any, tas_social: Any, tas_trade: Any, status: str) -> None:
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
        'previous_strategy': previous_strategy,
        'previous_reason': previous_reason,
    }


def build_strategy_hypothesis(target_components: list[str], mode: str, tas_status: str) -> str:
    parts: list[str] = []
    if 'tas_social' in target_components:
        parts.append('improve social quality and execution efficiency via bookmarker')
    if 'tas_trade' in target_components:
        parts.append('improve treasury timing / evidence quality via trader')
    if not parts:
        parts.append('preserve current gains while exploring conservatively')
    return f"Main control-plane cycle in mode={mode} targeting {', '.join(parts)} (tas_status={tas_status})."


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
        social_op_budget = 600.0
        social_vp_budget = 30.0
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
        # vp-flush: 低OP + 高VP → 策展优先，但允许最低限度发帖
        # VP预算=30（积极策展），OP预算=200（允许1帖）当OP充足时
        # 之前 OP=0 导致 post_directive 永远无法执行
        social_op_budget = 200.0 if op_available >= 400 else 0.0
        social_vp_budget = 30.0
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
    """D2: Write TAS decision summary to wiki/queries/ when signal is strong enough.

    Only fires when strategy_action != 'flat' AND |TAS delta| > 0.05.
    Avoids noise from routine heartbeats with no meaningful signal.
    """
    if strategy_action == 'flat':
        return
    if previous_tas is not None:
        delta = abs(tas_total - float(previous_tas))
        if delta <= 0.05:
            return

    # Build content summary
    social_status = social_intent.get('status', 'unknown')
    social_topic = social_intent.get('topic_focus') or social_intent.get('content_direction', '')
    treasury_status = treasury_policy.get('status', 'unknown')
    content = (
        f"## TAS 对比结论\n\n"
        f"- strategy_action: `{strategy_action}`\n"
        f"- tas_total: {tas_total}\n"
        f"- previous_tas: {previous_tas}\n"
        f"- tas_delta: {round(tas_total - float(previous_tas), 4) if previous_tas is not None else 'N/A'}\n\n"
        f"## 本轮 Social Intent\n\n"
        f"- status: {social_status}\n"
        f"- topic: {social_topic}\n\n"
        f"## 本轮 Treasury Policy\n\n"
        f"- status: {treasury_status}\n"
    )

    title = f"TAS Decision {date_str} — {strategy_action}"
    script = root / 'scripts' / 'write_wiki_query.py'
    if not script.exists():
        return

    import subprocess
    try:
        subprocess.run(
            [
                'python3', str(script),
                '--title', title,
                '--content', content,
                '--source', 'main',
                '--tags', 'tas,heartbeat,decision',
            ],
            timeout=15,
            check=False,
        )
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
    dispatch_config = read_json(RUNTIME / 'shared' / 'dispatch-config.json') or {}
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
    mode = compute_main_mode(op, vp)

    x_trend_keywords = summary.get('x_trend_keywords') if isinstance(summary.get('x_trend_keywords'), list) else []
    _tas_social_doc = summary.get('tas_social') if isinstance(summary.get('tas_social'), dict) else {}
    _tas_trade_doc = summary.get('tas_trade') if isinstance(summary.get('tas_trade'), dict) else {}
    tas_social = safe_float((_tas_social_doc or {}).get('value'))
    tas_trade = safe_float((_tas_trade_doc or {}).get('value'))
    _tas_trade_source_status = (_tas_trade_doc or {}).get('status', '')
    # If the trader itself reports a non-ok status, treat tas_trade as unavailable
    # for aggregation so degraded values don't poison tas_total.
    # P1 2026-04-10: include 'partial' — partial measurement quality must not
    # silently produce a misleading canonical TAS_trade contribution.
    if _tas_trade_source_status in ('degraded', 'blocked', 'stale', 'partial'):
        tas_trade = None
    tas_economic = None  # retired
    tas_values = [tas_social, tas_trade]
    available = [v for v in tas_values if v is not None]
    tas_total = round(0.7 * (tas_social or 0) + 0.3 * (tas_trade or 0), 6) if available else None
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
    )
    target_components = [name for name, value in [('tas_social', tas_social), ('tas_trade', tas_trade)] if value is None or value < 1.0]
    if not target_components:
        target_components = ['tas_social', 'tas_trade']

    social_gate = (dispatch_config.get('social') or {}) if isinstance(dispatch_config, dict) else {}
    treasury_gate = (dispatch_config.get('treasury') or {}) if isinstance(dispatch_config, dict) else {}

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not packet:
        blockers.append({'code': 'missing_input_packet', 'message': 'runtime/main/input-packet.json missing', 'severity': 'error'})
    if fallback_fields:
        warnings.append({'code': 'legacy_fallback_fields_present', 'message': f'main input packet still depends on legacy fallback for: {", ".join(fallback_fields)}', 'severity': 'warning'})
    if op is None or vp is None:
        blockers.append({'code': 'op_vp_unavailable', 'message': 'main native runtime could not recover OP/VP', 'severity': 'error'})
        mode = 'blocked-runtime'

    bookmarker_status = normalize_status(bookmarker_latest.get('status'), default='blocked')
    trader_status = normalize_status(trader_latest.get('status'), default='blocked')

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

    social_gate_checks = {
        'lane_enabled': bool(social_gate.get('enabled', False)),
        'mode_ok': gate_allows_mode(mode, str(social_gate.get('min_mode', 'active'))),
        'bookmarker_status_ok': bookmarker_status == str(social_gate.get('require_bookmarker_status', 'ok')),
        'has_candidates': bool((packet.get('bookmarker') or {}).get('candidate_count')) if social_gate.get('require_candidates', True) else True,
        'has_drafts': bool((bookmarker_social_drafts.get('drafts') or [])) if social_gate.get('require_drafts', False) else True,
        'breaker_closed': not breaker_open,
        'no_main_blockers': not blockers,
    }
    social_authorized = all(social_gate_checks.values())
    social_selection = analyze_social_action_selection(
        bookmarker_social_drafts,
        social_max_actions,
        recent_social_targets,
        recent_noop_curate_targets,
        social_mix_order,
        social_max_per_type,
    ) if social_authorized else {'actions': [], 'selection_reason': 'gate_blocked', 'suppressed': {}, 'draft_count': 0, 'selected_ids': []}
    social_actions = social_selection['actions']
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

    # Extract structured directives from social_actions for bookmarker execution plane.
    # curate_targets: populated from curate/like actions regardless of authorization status,
    # so bookmarker always has suggestions even when the gate is revoked.
    curate_targets: list[dict[str, Any]] = []
    post_directive: dict[str, Any] | None = None
    reply_directive: dict[str, Any] | None = None
    for action in (social_actions if isinstance(social_actions, list) else []):
        if not isinstance(action, dict):
            continue
        atype = action.get('type') or action.get('action_type') or ''
        if atype in ('curate', 'like'):
            tid = action.get('tweetId') or action.get('tweet_id') or action.get('target_key', '').replace('tagclaw:', '')
            if tid:
                curate_targets.append({
                    'tweet_id': str(tid),
                    'suggested_vp': int(action.get('vp') or action.get('suggested_vp') or 3),
                    'reason': action.get('reason') or action.get('note') or f'{atype} from main cycle',
                })
        elif atype == 'post' and post_directive is None:
            text = action.get('text') or ''
            tick = action.get('tick') or 'TagClaw'
            # If text is missing, deref the draft to get it
            if not text:
                draft_ref = action.get('draft_ref') or ''
                if '#' in draft_ref:
                    draft_id = draft_ref.split('#', 1)[1]
                    bookmarker_drafts_obj = read_json(RUNTIME / 'bookmarker' / 'social-drafts.json') or {}
                    for _d in (bookmarker_drafts_obj.get('drafts') or []):
                        if isinstance(_d, dict) and _d.get('id') == draft_id:
                            text = _d.get('text') or ''
                            tick = _d.get('tick') or tick
                            break
            if text:
                post_directive = {'tick': str(tick), 'text': str(text), 'reason': action.get('reason') or 'main cycle post', 'draft_ref': action.get('draft_ref')}
                # P3 2026-04-11: Reward-aware tick override — prefer communities
                # with real claimable reward evidence; deprioritize CLAW.
                _rw_sel = select_reward_aware_tick(reward_status, wiki_trending_ticks, current_tick=str(tick))
                original_tick = str(tick)
                if _rw_sel['tick'] != original_tick:
                    post_directive['tick'] = _rw_sel['tick']
                    post_directive['tick_override_reason'] = _rw_sel['reason']
                    post_directive['tick_original'] = original_tick
                post_directive['tick_selector'] = {
                    'chosen': _rw_sel['tick'],
                    'score': _rw_sel['score'],
                    'reason': _rw_sel['reason'],
                    'top_scores': _rw_sel['scores'],
                }
                # Enrich post_directive with wiki topic insight
                _wiki_topic, _wiki_insight, _wiki_reason = _get_top_wiki_topic()
                if _wiki_topic:
                    post_directive['wiki_topic'] = _wiki_topic
                if _wiki_insight:
                    post_directive['wiki_insight'] = _wiki_insight
                if _wiki_reason:
                    post_directive['wiki_reason'] = _wiki_reason
        elif atype == 'reply' and reply_directive is None:
            tid = action.get('tweetId') or action.get('tweet_id') or ''
            text = action.get('text') or ''
            if tid and text:
                reply_directive = {'tweet_id': str(tid), 'text': str(text)}

    # Wiki Brief Fallback: 若 social_actions 没有产生 post_directive，
    # 且 wiki-brief 新鲜，用 wiki top theme 的 agent_action 作为 post 内容方向
    if post_directive is None and wiki_top_theme:
        _wiki_agent_action = wiki_top_theme.get('agent_action', '')
        _wiki_theme_name = wiki_top_theme.get('name', '')
        if _wiki_agent_action and _wiki_theme_name:
            # 不直接发帖，而是写入 content_direction 供 bookmarker 参考
            # post_directive 保持 None（不绕过 bookmarker 的发帖职责）
            pass  # content_direction 在 social_intent 里设置

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

        _tas_delta = round((tas_total or 0.0) - (previous_tas_total or 0.0), 4)
        _prev_tas_social = safe_float(previous_tas_latest.get('tas_social')) or 0.0
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

    social_decision = 'authorize' if social_authorized else 'hold'
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
            'mode': mode,
            'actions': social_actions,
            'curate_targets': curate_targets,
            **({'post_directive': post_directive} if post_directive else {}),
            **({'reply_directive': reply_directive} if reply_directive else {}),
            'max_total_actions': social_max_actions if social_authorized else 0,
            'budget_slice': bookmarker_budget_slice,
            **({'x_trend_keywords': x_trend_keywords} if x_trend_keywords else {}),
            'wiki_top_theme': wiki_top_theme.get('name') if wiki_top_theme else None,
            'wiki_content_direction': wiki_top_theme.get('agent_action') if wiki_top_theme else None,
            'post_config': {
                # P2: engagement_mode overridden by strategy experiment arm when available
                'engagement_mode': ((_next_arms.get('track_b') or {}).get('engagement_mode')) or 'reply_to_top_agents',
                'target_agents': ((_next_arms.get('track_b') or {}).get('target_agents')) or ['foxclaw', 'clawdiai', 'alita'],
                'max_replies_per_cycle': 2,
                'reply_after_post': True,
            },
            'curator_config': {
                # P2: vp_strategy and target_selection from Track A arm
                'vp_strategy': ((_next_arms.get('track_a') or {}).get('vp_strategy')) or 'balanced',
                'credit_strategy': ((_next_arms.get('track_a') or {}).get('credit_strategy')) or 'hold',
                'target_selection': ((_next_arms.get('track_a') or {}).get('target_selection')) or 'any',
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
            'selection_reason': social_selection.get('selection_reason'),
            'selection_suppressed': social_selection.get('suppressed'),
            'selected_draft_ids': social_selection.get('selected_ids'),
            'cooldown_hours': social_cooldown_hours,
            'action_mix_order': social_mix_order,
            'max_per_type': social_max_per_type,
            'recently_executed_target_keys': sorted(recent_social_targets),
            'recent_noop_curate_target_keys': sorted(recent_noop_curate_targets),
            'breaker_state': breaker.get('state'),
            'breaker_until': breaker.get('until'),
            'breaker_last_failure_reason': breaker.get('last_failure_reason'),
            # P3: reward-aware tick selector trace
            'tick_selector': post_directive.get('tick_selector') if isinstance(post_directive, dict) else None,
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
    claimable_usd = safe_float(summary.get('claimable_usd'))
    recent_operations = summary.get('recent_operations') if isinstance(summary.get('recent_operations'), list) else []
    last_failed_operation = summary.get('last_failed_operation') if isinstance(summary.get('last_failed_operation'), dict) else None
    pending_or_unconfirmed_orders = summary.get('pending_or_unconfirmed_orders') if isinstance(summary.get('pending_or_unconfirmed_orders'), list) else []
    trading_config = treasury_gate.get('trading') or {}
    allow_trading_gate = bool(treasury_gate.get('allow_trading', False))
    claim_threshold_ok = (claimable_usd is not None and claimable_usd >= float(treasury_gate.get('min_claimable_usd', 2.0)))
    treasury_gate_checks = {
        'lane_enabled': bool(treasury_gate.get('enabled', False)),
        'trader_status_ok': trader_status in (treasury_gate.get('require_trader_status_in') or ['ok', 'partial']),
        'claim_or_trade_eligible': claim_threshold_ok or allow_trading_gate,
        'no_main_blockers': not blockers,
    }
    treasury_allowed = all(treasury_gate_checks.values())
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
            'claims_allowed': treasury_allowed and bool(treasury_gate.get('allow_claims', True)),
            'trading_allowed': treasury_allowed and allow_trading_gate,
            'rebalance_allowed': treasury_allowed and bool(treasury_gate.get('allow_rebalance', False)),
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
            'claimable_usd': claimable_usd,
            'last_execution_status': execution_record.get('status'),
            'execution_count_today': summary.get('execution_count_today'),
            'recent_operations': recent_operations,
            'last_failed_operation': last_failed_operation,
            'pending_or_unconfirmed_orders': pending_or_unconfirmed_orders,
        },
    }

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
        'assigned_agents': ['main'] + ([ 'bookmarker' ] if social_authorized or 'tas_social' in target_components else []) + ([ 'trader' ] if treasury_allowed or 'tas_trade' in target_components else []) + ([ 'claude_dispatch' ] if warnings or blockers else []),
        'expected_uplift': {
            'tas': round(0.12 if social_authorized or treasury_allowed else 0.04, 4),
            'tas_social': round(0.18 if 'tas_social' in target_components else 0.05, 4),
            'tas_trade': round(0.12 if 'tas_trade' in target_components else 0.03, 4),
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
        'tas_economic': None,  # retired 2026-03-25, kept for backward compat
        'tas_total': tas_total,
        'formula': '0.7 * TAS_social + 0.3 * TAS_trade',
        'status': tas_status,
        'comparison': main_strategy_loop,
        'strategy_action': main_strategy_loop['strategy_action'],
        'planning_focus': main_strategy_loop['planning_focus'],
        'target_components': target_components,
        'strategy_ref': 'runtime/main/strategy-plan.json',
        'budget_ref': 'runtime/shared/budget-allocation.json',
        'notes': 'TAS_economic retired; formula updated to 0.7×social + 0.3×trade',
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
                'total': tas_total,
                'formula': '0.7×social + 0.3×trade',
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
        },
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
        _append_tas_history(RUNTIME, cycle_id, tas_total, tas_social, tas_trade, tas_status)
    except Exception:
        pass  # graceful degrade — never interrupt main flow
    atomic_write_json(RUNTIME / 'main' / 'runtime-health.json', runtime_health)
    atomic_write_json(RUNTIME / 'main' / 'latest.json', latest)

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

    # Generate guidance files using select_strategy (autoresearch hill-climbing)
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location('select_strategy', ROOT / 'scripts' / 'select_strategy.py')
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _guidance = _mod.select_strategy(apply=True)
        _exp_mode = (
            f"bk={_guidance['bookmarker']['mode']}/"
            f"tr={_guidance['trader']['mode']}"
        )
    except Exception as _e:
        _exp_mode = f'error: {_e}'

    print(json.dumps([
        {'agent': 'main', 'status': latest['status'],
         'latest_path': str(RUNTIME / 'main' / 'latest.json'),
         'guidance_experiment_mode': _exp_mode}
    ], ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
